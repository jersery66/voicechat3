# Pipeline - Unified conversation pipeline and shared constants

import re
import traceback
from typing import Optional, Any, Callable, List, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


# ==================== Tag Detection Constants (single source of truth) ====================

# Regex pattern -> string name (used for detection in pipeline)
END_PATTERNS = {
    r'\[END_GOAL_ACHIEVED\]': 'goal_achieved',
    r'\[END_TIME_LIMIT\]': 'time_limit',
    r'\[END_SAFETY\]': 'safety',
    r'\[END_INVALID\]': 'invalid',
    r'\[END_QUIT\]': 'quit',
}

REC_TAGS = {
    r'\[REC_BREATHING\]': 'breathing',
    r'\[REC_MUSCLE\]': 'muscle',
    r'\[REC_MEDITATION\]': 'meditation',
    r'\[REC_GAME\]': 'game',
}


def detect_tag(text: str, patterns: dict) -> Optional[str]:
    """Find the first matching tag in text. Returns string name or None."""
    for pattern, tag_type in patterns.items():
        if re.search(pattern, text):
            return tag_type
    return None


def get_end_type_enum(string_name: str):
    """Convert string end type name to EndType enum. Lazy import to avoid circular deps."""
    from services.report_service import EndType
    _map = {
        'goal_achieved': EndType.GOAL_ACHIEVED,
        'time_limit': EndType.TIME_LIMIT,
        'safety': EndType.SAFETY,
        'invalid': EndType.INVALID,
        'quit': EndType.QUIT,
    }
    return _map.get(string_name, EndType.GOAL_ACHIEVED)


# ==================== Tag Cleaning ====================

def clean_for_display(text: str) -> str:
    """Remove all control tags for UI display. Strips [breath]/[laughter] too."""
    text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
    text = re.sub(r'\[END_[A-Z_]+\]', '', text)
    text = re.sub(r'<\|[^|]+\|>', '', text)
    text = re.sub(r'【.*?】', '', text)
    text = re.sub(r'\[(?:breath|laughter)\]', '', text)
    return text.strip()


def clean_for_tts(text: str) -> str:
    """Keep [breath]/[laughter] for CosyVoice, strip control tags."""
    text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
    text = re.sub(r'\[END_[A-Z_]+\]', '', text)
    text = re.sub(r'<\|[^|]+\|>', '', text)
    text = re.sub(r'【.*?】', '', text)
    return text.strip()


# ==================== Pipeline Result ====================

@dataclass
class PipelineResult:
    """Result of a single pipeline execution."""
    user_text: str = ""
    full_response: str = ""
    analysis_text: str = ""
    spoken_text: str = ""
    clean_spoken: str = ""
    tts_text: str = ""
    end_type: Optional[str] = None          # 'goal_achieved', 'time_limit', etc.
    relaxation_rec: Optional[str] = None    # 'breathing', 'muscle', 'meditation', 'game'
    intent: str = "counseling"
    emotion_result: dict = field(default_factory=dict)


@dataclass
class PipelineConfig:
    """Controls which optional pipeline steps are enabled."""
    use_stt: bool = False       # True for voice input, False for text input
    use_tts: bool = False       # True for voice output, False for text-only
    audio_data: Optional[Any] = None  # Raw audio numpy array if use_stt=True
    user_text: str = ""         # Text input if use_stt=False


# ==================== Unified Conversation Pipeline ====================

class ConversationPipeline:
    """
    Unified pipeline: STT(optional) -> Intent+Emotion -> RAG -> LLM stream
    -> tag parse -> TTS(optional) -> post-process.

    Merges _process_pipeline and _process_text_pipeline into a single flow.
    The caller provides an emit callback for thread-safe UI updates.
    No Qt dependency.
    """

    def __init__(self, stt_service, llm_service, tts_service,
                 rag_service, agent_service, report_service, data_manager,
                 session_emotions: list):
        self.stt = stt_service
        self.llm = llm_service
        self.tts = tts_service
        self.rag = rag_service
        self.agent = agent_service
        self.report = report_service
        self.data = data_manager
        self.session_emotions = session_emotions

    def execute(self, config: PipelineConfig,
                emit: Callable[[str, Any], None]) -> PipelineResult:
        """
        Run the full pipeline. emit(msg_type, content) is called for UI updates.
        Returns PipelineResult with all parsed data.
        """
        result = PipelineResult()

        # --- STT (optional) ---
        if config.use_stt:
            if config.audio_data is None or len(config.audio_data) == 0:
                emit("status", "未检测到语音")
                return result
            emit("status", "正在转写...")
            result.user_text = self.stt.transcribe(config.audio_data)
            if not result.user_text.strip():
                emit("status", "无法识别内容")
                return result
        else:
            result.user_text = config.user_text

        emit("append_chat", ("user", result.user_text))

        # --- Round tracking ---
        if self.report:
            self.report.increment_round()
            should_warn, warning_msg = self.report.should_warn_time_limit()
            if should_warn:
                emit("session_warning", warning_msg)

        # --- Save user data ---
        user_id = self.data.current_user_id if self.data else "default_user"
        if self.data:
            self.data.set_user_id(user_id)
            if config.use_stt and config.audio_data is not None:
                self.data.save_user_message(config.audio_data, result.user_text)
            else:
                self.data.save_user_message(None, result.user_text)

        # --- Intent + Emotion (parallel 3B calls) ---
        result.intent, result.emotion_result = self._classify_intent_and_emotion(
            result.user_text
        )

        # --- System suffix construction ---
        system_suffix = self._build_system_suffix(result.user_text)
        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

        # --- LLM streaming ---
        emit("start_ai_message", None)
        result.full_response, result.analysis_text, result.spoken_text = \
            self._stream_llm(result.user_text, final_suffix, emit)
        emit("finish_streaming", None)

        # --- Clean for display ---
        result.clean_spoken = clean_for_display(result.spoken_text)
        emit("clean_last_ai", result.clean_spoken)

        # --- TTS text (keep [breath]/[laughter] for CosyVoice) ---
        result.tts_text = clean_for_tts(result.spoken_text)

        # --- Tag detection (single source of truth) ---
        result.end_type = detect_tag(result.full_response, END_PATTERNS)
        result.relaxation_rec = detect_tag(result.full_response, REC_TAGS)

        # --- Save assistant message ---
        if self.data:
            self.data.save_assistant_message(None, result.full_response, sample_rate=24000)

        # --- TTS (optional) ---
        if config.use_tts and self.tts and result.tts_text:
            emit("status", "正在播放...")
            try:
                self.tts.generate_and_play(result.tts_text)
            except Exception as e:
                print(f"TTS error: {e}")

        return result

    def _classify_intent_and_emotion(self, text: str) -> tuple:
        """Parallel 3B calls for intent + emotion. Returns (intent_str, emotion_dict)."""
        intent_result = {"intent": "counseling", "confidence": 1.0}
        emotion_result = {"emotion": "neutral", "intensity": 0.0}
        if self.agent:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(self.agent.classify_intent, text): "intent",
                    executor.submit(self.agent.detect_emotion, text): "emotion",
                }
                for future in as_completed(futures):
                    tag = futures[future]
                    try:
                        res = future.result()
                        if tag == "intent":
                            intent_result = res
                        else:
                            emotion_result = res
                            self.session_emotions.append({"role": "user", **emotion_result})
                    except Exception as e:
                        print(f"[WARNING] {tag} detection failed: {e}")
        intent = intent_result.get("intent", "counseling")
        print(f"[AGENT] Intent: {intent} ({intent_result.get('confidence', 0):.2f}) "
              f"| Emotion: {emotion_result.get('emotion', 'neutral')} "
              f"({emotion_result.get('intensity', 0):.2f})")
        return intent, emotion_result

    def _build_system_suffix(self, text: str) -> str:
        """Build system suffix with round warning + RAG context."""
        from config import MIN_ROUNDS_FOR_RELAXATION

        system_suffix = ""
        current_rounds = self.report.get_round_count() if self.report else 0

        if current_rounds < MIN_ROUNDS_FOR_RELAXATION:
            system_suffix = (
                f"【系统警告】当前仅第{current_rounds}轮对话"
                f"（少于{MIN_ROUNDS_FOR_RELAXATION}轮）。"
                f"无论用户说了什么，你绝对禁止推荐放松训练！"
                f"继续通过对话建立关系。"
            )

        if self.rag:
            rag_suffix = self.rag.get_system_suffix(text)
            if rag_suffix:
                system_suffix += "\n" + rag_suffix

        return system_suffix

    def _stream_llm(self, text: str, system_suffix: Optional[str],
                    emit: Callable[[str, Any], None]):
        """
        Stream LLM response, split on |||, filter tags, emit stream_text.
        Returns (full_response, analysis_text, spoken_text).
        """
        full_response = ""
        analysis_text = ""
        spoken_text = ""
        found_separator = False
        stream_buffer = ""

        llm_gen = self.llm.chat(text, system_suffix=system_suffix)

        for chunk in llm_gen:
            full_response += chunk
            stream_buffer += chunk

            # Filter tags from stream buffer
            stream_buffer = re.sub(r'\[REC_[A-Z_]+\]', '', stream_buffer)
            stream_buffer = re.sub(r'\[END_[A-Z_]+\]', '', stream_buffer)
            stream_buffer = re.sub(r'【.*?】', '', stream_buffer)
            stream_buffer = re.sub(r'\[(?:breath|laughter)\]', '', stream_buffer)

            # Split on |||
            if not found_separator and '|||' in stream_buffer:
                parts = stream_buffer.split('|||', 1)
                analysis_text = parts[0]
                stream_buffer = parts[1]
                found_separator = True

            if found_separator and stream_buffer:
                display_text = re.sub(r'<\|[^|]+\|>', '', stream_buffer)
                if display_text:
                    emit("stream_text", display_text)
                stream_buffer = ""

        # Parse full response
        if '|||' in full_response:
            parts = full_response.split('|||', 1)
            analysis_text = parts[0].strip()
            spoken_text = parts[1].strip()
        else:
            spoken_text = full_response.strip()

        return full_response, analysis_text, spoken_text
