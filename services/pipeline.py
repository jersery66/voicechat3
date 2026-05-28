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
    extra_system_suffix: str = ""  # Additional system context (e.g. scale questions)


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
        self._administered_scales: set = set()
        self._scale_answers: Dict[str, Dict[int, int]] = {}  # {scale_name: {q_num: score}}
        # Shared executor for parallel intent / emotion / crisis classification.
        # Created once and reused across pipeline executions to avoid
        # spawning fresh worker threads on every user turn.
        self._executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="pipe-classify"
        )

    def shutdown(self):
        """Release the shared classification executor. Call on app exit."""
        try:
            self._executor.shutdown(wait=False)
        except Exception as e:
            logger.debug(f"executor shutdown error: {e}")

    def reset_session(self):
        """Reset per-session state (scale tracking). Call on new session."""
        self._administered_scales.clear()
        self._scale_answers.clear()

    def get_incomplete_scales(self) -> List[Dict[str, Any]]:
        """Return scales with unanswered questions.

        Each entry: {scale_name, total, answered, remaining_questions,
                     remaining_nums}.
        """
        from services.scales import SCALES
        incomplete = []
        for scale_name in self._administered_scales:
            scale_def = SCALES.get(scale_name)
            if not scale_def:
                continue
            total = len(scale_def["questions"])
            answered = self._scale_answers.get(scale_name, {})
            answered_count = len(answered)
            if answered_count < total:
                remaining = []
                remaining_nums = []
                for i, q in enumerate(scale_def["questions"]):
                    q_num = i + 1
                    if q_num not in answered:
                        remaining.append(q)
                        remaining_nums.append(q_num)
                incomplete.append({
                    "scale_name": scale_name,
                    "total": total,
                    "answered": answered_count,
                    "remaining_questions": remaining,
                    "remaining_nums": remaining_nums,
                })
        return incomplete

    def get_remaining_scale_prompt(self) -> Optional[str]:
        """Generate a prompt for the LLM to ask remaining scale questions."""
        incomplete = self.get_incomplete_scales()
        if not incomplete:
            return None
        from services.scales import SCALES
        parts = []
        for info in incomplete:
            scale_name = info["scale_name"]
            scale_def = SCALES[scale_name]
            options_text = " / ".join(
                f"{opt['score']}-{opt['label']}" for opt in scale_def["options"]
            )
            questions_text = "\n".join(
                f"  Q{info['remaining_nums'][i]}: {q}"
                for i, q in enumerate(info["remaining_questions"])
            )
            parts.append(
                f"【量表补问 - {scale_name}】\n"
                f"已答 {info['answered']}/{info['total']} 题，还需问以下题目：\n"
                f"{questions_text}\n"
                f"评分标准：{options_text}\n"
                f"记录规则：以 [SCALE:{scale_name}:Q题号:S分数] 格式嵌入回复末尾。"
            )
        return "\n\n".join(parts)

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

        # --- System suffix: RAG + round warning + scale (no agent dependency) ---
        with metrics.timer("rag.system_suffix"):
            system_suffix = self._build_system_suffix(result.user_text)

        from services.scales import get_scale_manager
        scale_mgr = get_scale_manager()
        suggested_scale = scale_mgr.should_administer(
            self.emotion_tracker, self.report,
            user_text=result.user_text, administered=self._administered_scales,
            agent_service=self.agent,
        )
        if suggested_scale:
            self._administered_scales.add(suggested_scale)
            scale_guidance = scale_mgr.get_scale_guidance_for_prompt(suggested_scale)
            if scale_guidance:
                system_suffix += "\n" + scale_guidance
                logger.info(f"Scale suggested: {suggested_scale}")

        if self.emotion_tracker:
            hint = self.emotion_tracker.get_intervention_hint()
            if hint:
                system_suffix += "\n" + hint

        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

        # Append extra system context (e.g. remaining scale questions at exit)
        if config.extra_system_suffix:
            if final_suffix:
                final_suffix += "\n" + config.extra_system_suffix
            else:
                final_suffix = config.extra_system_suffix

        # --- Quick crisis keyword check (fast, before LLM) ---
        # The full agent classification runs in parallel with LLM, but crisis
        # keywords must be checked BEFORE LLM starts so the crisis suffix can
        # be injected into the system prompt for safety-critical responses.
        if self.agent:
            quick_crisis = self.agent._keyword_crisis_risk(result.user_text)
            if quick_crisis.get("immediate_action"):
                from config import CRISIS_INTERVENTION_SUFFIX
                if final_suffix:
                    final_suffix += "\n" + CRISIS_INTERVENTION_SUFFIX
                else:
                    final_suffix = CRISIS_INTERVENTION_SUFFIX
                logger.warning(f"[Pipeline] Crisis keywords detected pre-LLM: risk={quick_crisis.get('risk_level')}")

        # --- LLM stream + Agent classification (concurrent) ---
        # Start LLM immediately with RAG/scale context; agent runs in parallel.
        # This saves 1-3s of agent wait time before first LLM token.
        emit("start_ai_message", None)
        agent_future = self._executor.submit(
            self._classify_intent_emotion_crisis, result.user_text
        )

        with metrics.timer("llm.stream"):
            result.full_response, result.analysis_text, result.spoken_text = \
                self._stream_llm(result.user_text, final_suffix, emit)
        emit("finish_streaming", None)

        # --- Prepare TTS text immediately ---
        result.clean_spoken = clean_for_display(result.spoken_text)
        emit("clean_last_ai", result.clean_spoken)
        result.tts_text = clean_for_tts(result.spoken_text)

        # --- Tag detection ---
        result.end_type = detect_tag(result.full_response, END_PATTERNS)
        result.relaxation_rec = detect_tag(result.full_response, REC_TAGS)
        result.scale_tags = parse_scale_tags(result.full_response)
        # Track answered questions per scale
        for scale_name, answers in result.scale_tags.items():
            if scale_name not in self._scale_answers:
                self._scale_answers[scale_name] = {}
            self._scale_answers[scale_name].update(answers)
        logger.info(
            f"[Pipeline] End type: {result.end_type} "
            f"| Relaxation rec: {result.relaxation_rec} "
            f"| Scale tags: {result.scale_tags} "
            f"| Spoken text length: {len(result.spoken_text)}"
        )

        # --- Auto-end on time/round limit ---
        if not result.end_type and self.report and self.report.is_over_limit():
            emit("time_limit_ask", None)

        # --- Save assistant message ---
        if self.data:
            self.data.save_assistant_message(None, result.full_response, sample_rate=48000)

        # --- TTS + Agent post-processing (concurrent) ---
        # TTS doesn't need agent results. Run both in parallel to save 1-3s.
        tts_future = None
        if config.use_tts and self.tts and result.tts_text:
            emit("status", "正在播放...")
            tts_future = self._executor.submit(self._play_tts, result.tts_text)

        # Agent results + emotion tracking + crisis (parallel with TTS)
        try:
            agent_done = agent_future.result(timeout=10)
            result.intent, result.emotion_result, crisis_keyword_result = agent_done
        except Exception as e:
            logger.warning(f"Agent classification failed: {e}")
            result.intent = "counseling"
            result.emotion_result = {"emotion": "neutral", "intensity": 0.0}
            crisis_keyword_result = {"risk_level": 0, "indicators": [], "immediate_action": False}

        logger.info(
            f"[Pipeline] Intent: {result.intent} "
            f"| Emotion: {result.emotion_result.get('emotion', 'N/A')} "
            f"(intensity: {result.emotion_result.get('intensity', 'N/A')})"
        )

        if self.emotion_tracker:
            self.emotion_tracker.add_emotion(result.emotion_result)

        crisis_result = crisis_keyword_result
        if self.agent and crisis_result.get("risk_level", 0) < 7:
            emotion = result.emotion_result.get("emotion", "neutral")
            intensity = result.emotion_result.get("intensity", 0.0)
            if emotion != "neutral" or intensity >= 0.5:
                with metrics.timer("agent.crisis"):
                    crisis_result = self.agent.assess_crisis_risk(
                        result.user_text, use_llm=True)
        result.crisis_risk = crisis_result.get("risk_level", 0)
        result.crisis_indicators = crisis_result.get("indicators", [])
        if crisis_result.get("immediate_action"):
            emit("show_crisis", crisis_result)

        logger.info(
            f"[Pipeline] RAG suffix: {bool(final_suffix)} "
            f"| Crisis risk: {result.crisis_risk}"
        )

        # Wait for TTS to finish playing (if still running)
        if tts_future is not None:
            try:
                tts_future.result(timeout=120)
            except Exception as e:
                logger.warning(f"TTS error: {e}")

        metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
        return result

    def _play_tts(self, text: str):
        """Generate and play TTS. Runs on a worker thread."""
        try:
            with get_metrics().timer("tts.play"):
                self.tts.generate_and_play(text)
        except Exception as e:
            logger.warning(f"TTS error: {e}")

    def _classify_intent_emotion_crisis(self, text: str) -> tuple:
        """Parallel 3B calls for intent + emotion + crisis keyword check.

        Returns (intent_str, emotion_dict, crisis_keyword_result).
        Uses the long-lived ``self._executor`` instead of constructing a fresh
        ``ThreadPoolExecutor`` on every turn.
        """
        intent_result = {"intent": "counseling", "confidence": 1.0}
        emotion_result = {"emotion": "neutral", "intensity": 0.0}
        crisis_result = {"risk_level": 0, "indicators": [], "immediate_action": False}
        if self.agent:
            futures = {
                self._executor.submit(self.agent.classify_intent, text): "intent",
                self._executor.submit(self.agent.detect_emotion, text): "emotion",
                self._executor.submit(self.agent._keyword_crisis_risk, text): "crisis",
            }
            for future in as_completed(futures):
                tag = futures[future]
                try:
                    res = future.result()
                    if tag == "intent":
                        intent_result = res
                    elif tag == "emotion":
                        emotion_result = res
                        self.session_emotions.append({"role": "user", **emotion_result})
                    elif tag == "crisis":
                        crisis_result = res
                except Exception as e:
                    logger.warning(f"{tag} detection failed: {e}")
        intent = intent_result.get("intent", "counseling")
        logger.debug(
            f"Intent: {intent} ({intent_result.get('confidence', 0):.2f}) "
            f"| Emotion: {emotion_result.get('emotion', 'neutral')} "
            f"({emotion_result.get('intensity', 0):.2f}) "
            f"| Crisis keyword: {crisis_result.get('risk_level', 0)}"
        )
        return intent, emotion_result, crisis_result

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
                logger.info("[Pipeline] RAG context injected into system suffix")

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
        pre_separator_buffer = ""

        llm_gen = self.llm.chat(text, system_suffix=system_suffix)

        for chunk in llm_gen:
            full_response += chunk

            if not found_separator:
                pre_separator_buffer += chunk
                if '|||' in pre_separator_buffer:
                    parts = pre_separator_buffer.split('|||', 1)
                    analysis_text = parts[0].strip()
                    spoken_buffer = parts[1]
                    found_separator = True
                    cleaned = _RE_PIPE_TAG.sub('', spoken_buffer)
                    cleaned = _RE_REC_TAG.sub('', cleaned)
                    cleaned = _RE_END_TAG.sub('', cleaned)
                    cleaned = _RE_SCALE_TAG.sub('', cleaned)
                    cleaned = _RE_BRACKETS_CN.sub('', cleaned)
                    cleaned = _RE_BREATH_LAUGH.sub('', cleaned)
                    if cleaned.strip():
                        emit("stream_text", cleaned.strip())
                continue

            cleaned = _RE_PIPE_TAG.sub('', chunk)
            cleaned = _RE_REC_TAG.sub('', cleaned)
            cleaned = _RE_END_TAG.sub('', cleaned)
            cleaned = _RE_SCALE_TAG.sub('', cleaned)
            cleaned = _RE_BRACKETS_CN.sub('', cleaned)
            cleaned = _RE_BREATH_LAUGH.sub('', cleaned)
            if cleaned.strip():
                emit("stream_text", cleaned.strip())

        if '|||' in full_response:
            parts = full_response.split('|||', 1)
            analysis_text = parts[0].strip()
            spoken_text = parts[1].strip()
        else:
            spoken_text = full_response.strip()
            if not found_separator:
                cleaned = _RE_PIPE_TAG.sub('', spoken_text)
                cleaned = _RE_REC_TAG.sub('', cleaned)
                cleaned = _RE_END_TAG.sub('', cleaned)
                cleaned = _RE_SCALE_TAG.sub('', cleaned)
                cleaned = _RE_BRACKETS_CN.sub('', cleaned)
                cleaned = _RE_BREATH_LAUGH.sub('', cleaned)
                if cleaned.strip():
                    emit("stream_text", cleaned.strip())
                else:
                    logger.warning(f"Spoken text empty after cleaning, using raw response as fallback")
                    fallback = _RE_REC_TAG.sub('', spoken_text)
                    fallback = _RE_END_TAG.sub('', fallback)
                    fallback = _RE_SCALE_TAG.sub('', fallback)
                    fallback = _RE_BREATH_LAUGH.sub('', fallback)
                    if fallback.strip():
                        emit("stream_text", fallback.strip())
                        spoken_text = fallback.strip()
                    else:
                        spoken_text = spoken_text

        if not spoken_text and full_response:
            spoken_text = full_response.strip()
            logger.warning(f"spoken_text was empty, falling back to full_response")

        return full_response, analysis_text, spoken_text
