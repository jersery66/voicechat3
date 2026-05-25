# Pipeline - Unified conversation pipeline and shared constants

import re
import time
import traceback
from typing import Optional, Any, Callable, List, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger import get_logger
from services.metrics import get_metrics

logger = get_logger(__name__)


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

SCALE_PATTERN = re.compile(r'\[SCALE:(\w+-\d+):Q(\d+):S(\d+)\]')

# Pre-compiled regexes for hot-path tag stripping (avoids re-compiling per chunk)
_RE_REC_TAG = re.compile(r'\[REC_[A-Z_]+\]')
_RE_END_TAG = re.compile(r'\[END_[A-Z_]+\]')
_RE_SCALE_TAG = re.compile(r'\[SCALE:[^\]]+\]')
_RE_BRACKETS_CN = re.compile(r'【.*?】')
_RE_PIPE_TAG = re.compile(r'<\|[^|]+\|>')
_RE_BREATH_LAUGH = re.compile(r'\[(?:breath|laughter)\]')

# Compile END_PATTERNS / REC_TAGS once for fast detection
_COMPILED_END_PATTERNS = [(re.compile(p), name) for p, name in END_PATTERNS.items()]
_COMPILED_REC_TAGS = [(re.compile(p), name) for p, name in REC_TAGS.items()]


def parse_scale_tags(text: str) -> Dict[str, Dict[int, int]]:
    """Extract scale answers from text. Returns {scale_name: {question_num: score}}."""
    results: Dict[str, Dict[int, int]] = {}
    for match in SCALE_PATTERN.finditer(text):
        scale_name = match.group(1)
        q_num = int(match.group(2))
        score = int(match.group(3))
        results.setdefault(scale_name, {})[q_num] = score
    return results


def detect_tag(text: str, patterns: dict) -> Optional[str]:
    """Find the first matching tag in text. Returns string name or None.

    Optimized path for the two well-known dicts (END_PATTERNS / REC_TAGS) using
    pre-compiled regexes; falls back to ad-hoc compilation for other dicts.
    """
    if patterns is END_PATTERNS:
        compiled = _COMPILED_END_PATTERNS
    elif patterns is REC_TAGS:
        compiled = _COMPILED_REC_TAGS
    else:
        compiled = [(re.compile(p), name) for p, name in patterns.items()]
    for pattern, tag_type in compiled:
        if pattern.search(text):
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
    text = _RE_REC_TAG.sub('', text)
    text = _RE_END_TAG.sub('', text)
    text = _RE_SCALE_TAG.sub('', text)
    text = _RE_PIPE_TAG.sub('', text)
    text = _RE_BRACKETS_CN.sub('', text)
    text = _RE_BREATH_LAUGH.sub('', text)
    return text.strip()


def clean_for_tts(text: str) -> str:
    """Keep [breath]/[laughter] for CosyVoice, strip control tags."""
    text = _RE_REC_TAG.sub('', text)
    text = _RE_END_TAG.sub('', text)
    text = _RE_SCALE_TAG.sub('', text)
    text = _RE_PIPE_TAG.sub('', text)
    text = _RE_BRACKETS_CN.sub('', text)
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
    crisis_risk: int = 0
    crisis_indicators: list = field(default_factory=list)
    scale_tags: dict = field(default_factory=dict)


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
                 session_emotions: list, emotion_tracker=None):
        self.stt = stt_service
        self.llm = llm_service
        self.tts = tts_service
        self.rag = rag_service
        self.agent = agent_service
        self.report = report_service
        self.data = data_manager
        self.session_emotions = session_emotions
        self.emotion_tracker = emotion_tracker
        # Shared executor for parallel intent / emotion classification.
        # Created once and reused across pipeline executions to avoid
        # spawning fresh worker threads on every user turn.
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="pipe-classify"
        )

    def shutdown(self):
        """Release the shared classification executor. Call on app exit."""
        try:
            self._executor.shutdown(wait=False)
        except Exception as e:
            logger.debug(f"executor shutdown error: {e}")

    def execute(self, config: PipelineConfig,
                emit: Callable[[str, Any], None]) -> PipelineResult:
        """
        Run the full pipeline. emit(msg_type, content) is called for UI updates.
        Returns PipelineResult with all parsed data.
        """
        metrics = get_metrics()
        pipeline_started = time.perf_counter()
        result = PipelineResult()

        # --- STT (optional) ---
        if config.use_stt:
            if config.audio_data is None or len(config.audio_data) == 0:
                emit("status", "未检测到语音")
                metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
                return result
            emit("status", "正在转写...")
            with metrics.timer("stt.transcribe"):
                result.user_text = self.stt.transcribe(config.audio_data)
            if not result.user_text.strip():
                emit("status", "无法识别内容")
                metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
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
        # Defensive: DataManager exposes ``current_subject_id`` (被试编号),
        # not ``current_user_id``. Fall back to ``default_subject`` when unset.
        if self.data:
            user_id = (
                getattr(self.data, "current_subject_id", None)
                or "default_subject"
            )
            self.data.set_user_id(user_id)
            if config.use_stt and config.audio_data is not None:
                self.data.save_user_message(config.audio_data, result.user_text)
            else:
                self.data.save_user_message(None, result.user_text)

        # --- Intent + Emotion (parallel 3B calls) ---
        with metrics.timer("agent.intent_emotion"):
            result.intent, result.emotion_result = self._classify_intent_and_emotion(
                result.user_text
            )

        # --- Emotion trend tracking ---
        if self.emotion_tracker:
            self.emotion_tracker.add_emotion(result.emotion_result)

        # --- Crisis risk assessment (before LLM, proactive) ---
        crisis_result = None
        if self.agent:
            # Skip LLM call for neutral/positive emotions with low intensity
            # — keyword fallback still catches critical cases instantly
            emotion = result.emotion_result.get("emotion", "neutral")
            intensity = result.emotion_result.get("intensity", 0.0)
            use_llm = emotion != "neutral" or intensity >= 0.5
            with metrics.timer("agent.crisis"):
                crisis_result = self.agent.assess_crisis_risk(
                    result.user_text, use_llm=use_llm)
            result.crisis_risk = crisis_result.get("risk_level", 0)
            result.crisis_indicators = crisis_result.get("indicators", [])
            if crisis_result.get("immediate_action"):
                emit("show_crisis", crisis_result)

        # --- System suffix construction ---
        with metrics.timer("rag.system_suffix"):
            system_suffix = self._build_system_suffix(result.user_text)
        if self.emotion_tracker:
            hint = self.emotion_tracker.get_intervention_hint()
            if hint:
                system_suffix += "\n" + hint
        if crisis_result and crisis_result.get("immediate_action"):
            from config import CRISIS_INTERVENTION_SUFFIX
            system_suffix += "\n" + CRISIS_INTERVENTION_SUFFIX

        # --- Scale suggestion (based on emotion trend) ---
        from services.scales import get_scale_manager
        scale_mgr = get_scale_manager()
        suggested_scale = scale_mgr.should_administer(self.emotion_tracker, self.report)
        if suggested_scale:
            scale_guidance = scale_mgr.get_scale_guidance_for_prompt(suggested_scale)
            if scale_guidance:
                system_suffix += "\n" + scale_guidance
                logger.info(f"Scale suggested: {suggested_scale}")

        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

        # --- LLM streaming ---
        emit("start_ai_message", None)
        with metrics.timer("llm.stream"):
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
        result.scale_tags = parse_scale_tags(result.full_response)

        # --- Save assistant message ---
        if self.data:
            self.data.save_assistant_message(None, result.full_response, sample_rate=24000)

        # --- TTS (optional) ---
        if config.use_tts and self.tts and result.tts_text:
            emit("status", "正在播放...")
            try:
                with metrics.timer("tts.play"):
                    self.tts.generate_and_play(result.tts_text)
            except Exception as e:
                logger.warning(f"TTS error: {e}")

        metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
        return result

    def _classify_intent_and_emotion(self, text: str) -> tuple:
        """Parallel 3B calls for intent + emotion. Returns (intent_str, emotion_dict).

        Uses the long-lived ``self._executor`` instead of constructing a fresh
        ``ThreadPoolExecutor`` on every turn.
        """
        intent_result = {"intent": "counseling", "confidence": 1.0}
        emotion_result = {"emotion": "neutral", "intensity": 0.0}
        if self.agent:
            futures = {
                self._executor.submit(self.agent.classify_intent, text): "intent",
                self._executor.submit(self.agent.detect_emotion, text): "emotion",
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
                    logger.warning(f"{tag} detection failed: {e}")
        intent = intent_result.get("intent", "counseling")
        logger.debug(
            f"Intent: {intent} ({intent_result.get('confidence', 0):.2f}) "
            f"| Emotion: {emotion_result.get('emotion', 'neutral')} "
            f"({emotion_result.get('intensity', 0):.2f})"
        )
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

            # Filter tags from stream buffer (uses pre-compiled regexes)
            stream_buffer = _RE_REC_TAG.sub('', stream_buffer)
            stream_buffer = _RE_END_TAG.sub('', stream_buffer)
            stream_buffer = _RE_SCALE_TAG.sub('', stream_buffer)
            stream_buffer = _RE_BRACKETS_CN.sub('', stream_buffer)
            stream_buffer = _RE_BREATH_LAUGH.sub('', stream_buffer)

            # Split on |||
            if not found_separator and '|||' in stream_buffer:
                parts = stream_buffer.split('|||', 1)
                analysis_text = parts[0]
                stream_buffer = parts[1]
                found_separator = True

            if found_separator and stream_buffer:
                display_text = _RE_PIPE_TAG.sub('', stream_buffer)
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
