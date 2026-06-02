# Pipeline - Unified conversation pipeline and shared constants

import re
import time
import traceback
from typing import Optional, Any, Callable, List, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger import get_logger
from services.metrics import get_metrics
from config import MIN_ROUNDS_BEFORE_SCALE

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

SCALE_PATTERN = re.compile(r'\[SCALE:(\w+-\d+):Q(\d+):S(\d+)\]', re.IGNORECASE)

# Pre-compiled regexes for hot-path tag stripping (avoids re-compiling per chunk)
_RE_REC_TAG = re.compile(r'\[REC_[A-Z_]+\]')
_RE_END_TAG = re.compile(r'\[END_[A-Z_]+\]')
_RE_SCALE_TAG = re.compile(r'\[SCALE:[^\]]+\]', re.IGNORECASE)
_RE_BRACKETS_CN = re.compile(r'【.*?】')
_RE_PIPE_TAG = re.compile(r'<\|[^|]+\|>')
_RE_BREATH_LAUGH = re.compile(r'\[(?:breath|laughter)\]')
_RE_THINK = re.compile(r'<think>[\s\S]*?</think>')

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


def infer_scale_score_from_text(text: str, scale_name: str) -> Optional[int]:
    """Fallback: infer a scale score from the user's plain-text answer.

    Used when the LLM fails to output a [SCALE:...] tag.  Returns None if
    the text doesn't match any known option pattern.
    """
    t = text.strip()

    if scale_name in ("PHQ-9", "GAD-7"):
        if any(x in t for x in ["完全不会", "没有", "不会"]):
            return 0
        if any(x in t for x in ["好几天", "几天", "偶尔"]):
            return 1
        if any(x in t for x in ["一半以上", "大多数", "超过一半"]):
            return 2
        if any(x in t for x in ["几乎每天", "每天", "天天"]):
            return 3

    if scale_name == "PCL-5":
        if any(x in t for x in ["完全没有", "没有"]):
            return 0
        if "有一点" in t:
            return 1
        if any(x in t for x in ["中等程度", "中等"]):
            return 2
        if "相当严重" in t:
            return 3
        if any(x in t for x in ["极度严重", "非常严重"]):
            return 4

    return None


# Natural-language versions of scale questions for conversational delivery.
# Keys are (scale_name, question_number).  Used by the programmatic question
# branch so the TTS reads like a casual follow-up rather than a questionnaire.
NATURAL_SCALE_QUESTIONS = {
    ("PHQ-9", 1): "这阵子做事情的时候，兴趣和劲头怎么样？",
    ("PHQ-9", 2): "心情这块儿呢，会不会经常低落、沮丧，或者觉得没希望？",
    ("PHQ-9", 3): "睡眠怎么样，入睡、睡踏实，或者睡太多这几种情况有没有？",
    ("PHQ-9", 4): "白天精力怎么样，会不会总觉得累、没力气？",
    ("PHQ-9", 5): "吃饭这块儿有没有变化，比如吃不下，或者吃得比平时多？",
    ("PHQ-9", 6): "有没有常觉得自己不够好，或者让家里人失望了？",
    ("PHQ-9", 7): "注意力怎么样，看东西或者做点事的时候，会不会很难集中？",
    ("PHQ-9", 8): "最近动作、说话有没有明显变慢，或者反过来坐不住、烦躁得厉害？",
    ("PHQ-9", 9): "有没有出现过不想活，或者伤害自己的念头？",
    ("GAD-7", 1): "这阵子紧张、焦虑、心里发急的情况多不多？",
    ("GAD-7", 2): "担心一起来的时候，能不能停下来？",
    ("GAD-7", 3): "会不会很多事都忍不住担心？",
    ("GAD-7", 4): "身体和心里放松下来难不难？",
    ("GAD-7", 5): "有没有不安到坐不住的时候？",
    ("GAD-7", 6): "最近会不会比平时更容易烦、容易急？",
    ("GAD-7", 7): "有没有总觉得要出什么不好的事？",
    ("PCL-5", 1): "那件让你压力很大的事，会不会不由自主地反复想起来？",
    ("PCL-5", 2): "有没有做过跟那件事有关的噩梦？",
    ("PCL-5", 3): "会不会尽量不去想、不去提那件事？",
    ("PCL-5", 4): "跟那件事相关的人、地方、活动，会不会尽量避开？",
    ("PCL-5", 5): "对自己、对别人、对这个世界，有没有一些很强烈的负面想法？",
    ("PCL-5", 6): "会不会一直在责怪自己，或者责怪别人？",
    ("PCL-5", 7): "最近是不是特别容易紧张、受惊？",
    ("PCL-5", 8): "集中注意力有没有变得困难？",
}


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
    if not text:
        return ""
    # If analysis|||spoken got mixed, only keep the spoken part
    if "|||" in text:
        text = text.rsplit("|||", 1)[-1]
    text = _RE_THINK.sub('', text)
    text = _RE_REC_TAG.sub('', text)
    text = _RE_END_TAG.sub('', text)
    text = _RE_SCALE_TAG.sub('', text)
    text = _RE_PIPE_TAG.sub('', text)
    text = _RE_BRACKETS_CN.sub('', text)
    text = _RE_BREATH_LAUGH.sub('', text)
    return text.strip()


def clean_for_tts(text: str) -> str:
    """Keep [breath]/[laughter] for TTS, strip control tags."""
    if not text:
        return ""
    # If analysis|||spoken got mixed, only keep the spoken part
    if "|||" in text:
        text = text.rsplit("|||", 1)[-1]
    text = _RE_THINK.sub('', text)
    text = _RE_REC_TAG.sub('', text)
    text = _RE_END_TAG.sub('', text)
    text = _RE_SCALE_TAG.sub('', text)
    text = _RE_PIPE_TAG.sub('', text)
    text = _RE_BRACKETS_CN.sub('', text)
    return text.strip()


def make_safe_fallback_reply(user_text: str) -> str:
    """Return a safe spoken fallback when the LLM outputs only analysis or empty text."""
    import random
    text = (user_text or "").strip("。！？!?,， ")

    if not text:
        return random.choice([
            "嗯，我在听呢。[breath]你可以慢慢说。",
            "你说，我听着。[breath]不着急。",
        ])

    if text in {"你好", "你好呀", "嗨", "哈喽", "在吗", "老师好", "喂"} or (
        "你好" in text and len(text) <= 10
    ):
        return "你好呀。[breath]今天感觉咋样？"

    if any(x in text for x in ["不知道", "说不出来", "不晓得"]):
        return random.choice([
            "你说'不知道'，感觉不是真的没想法，而是心里堵着不好表达。[breath]没关系，我们不急，你先说说现在最难受的是哪一块？",
            "听起来你现在有点被卡住了。[breath]没关系，我们慢慢捋，你先说说最近最让你放不下的是什么？",
        ])

    if any(x in text for x in ["不开心", "心情不好", "心里很累", "难受", "低落", "心情不是特别开心"]):
        return random.choice([
            "听起来这阵子心情一直压着，不太好受。[breath]这种不开心是最近才明显起来的，还是已经持续一段时间了？",
            "你说不开心，我想多了解一下。[breath]是最近发生了什么事，还是这种感觉已经憋了挺久？",
        ])

    return random.choice([
        "嗯，我听着呢。[breath]你接着说。",
        "你说的我都有在听。[breath]再往下说说？",
    ])


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
    scale_active: bool = False             # True when a scale is currently being administered
    scale_completed: bool = False
    all_scales_completed: bool = False
    completed_scale_name: Optional[str] = None


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
        self._active_scale: Optional[str] = None   # currently in-progress scale name
        self._active_scale_q: int = 1              # current question number
        self._active_scale_waiting_answer: bool = False  # True = asked, waiting for user reply
        self._scale_queue: List[str] = []           # scales queued while one is active
        # Shared executor for parallel intent / emotion / crisis classification.
        # Created once and reused across pipeline executions to avoid
        # spawning fresh worker threads on every user turn.
        self._executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="pipe-classify"
        )

    def shutdown(self):
        """Release the shared classification executor. Call on app exit."""
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            logger.debug(f"executor shutdown error: {e}")

    def reset_session(self):
        """Reset per-session state (scale tracking). Call on new session."""
        self._administered_scales.clear()
        self._scale_answers.clear()
        self._active_scale = None
        self._active_scale_q = 1
        self._active_scale_waiting_answer = False
        self._scale_queue.clear()

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

    def get_scale_results(self) -> Dict[str, Any]:
        """Return structured scale scores for report and persistence.

        Each scale entry contains:
          scale_name, completed, answered, total_items, total_score,
          max_score, severity, items[{q_num, question, score, label, answered}]
        """
        from services.scales import SCALES, get_scale_manager
        mgr = get_scale_manager()
        results: Dict[str, Any] = {}

        for scale_name in self._administered_scales:
            scale_def = SCALES.get(scale_name)
            if not scale_def:
                continue

            answers = self._scale_answers.get(scale_name, {})
            total_items = len(scale_def["questions"])
            completed = len(answers) == total_items

            ordered_scores = [
                answers[i] for i in range(1, total_items + 1) if i in answers
            ]
            score_summary = mgr.score_scale(scale_name, ordered_scores)

            items = []
            for i, question in enumerate(scale_def["questions"], start=1):
                score = answers.get(i)
                label = None
                if score is not None:
                    for opt in scale_def["options"]:
                        if opt["score"] == score:
                            label = opt["label"]
                            break
                items.append({
                    "q_num": i,
                    "question": question,
                    "score": score,
                    "label": label,
                    "answered": score is not None,
                })

            results[scale_name] = {
                "scale_name": scale_def["name"],
                "completed": completed,
                "answered": len(answers),
                "total_items": total_items,
                "total_score": score_summary["total"],
                "max_score": score_summary["max_score"],
                "severity": score_summary["severity"],
                "items": items,
            }

        return results

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

        # --- Fast path: skip LLM for simple greetings in early rounds ---
        _GREETING_INPUTS = {"你好", "你好呀", "嗨", "哈喽", "在吗", "老师好", "喂"}
        current_rounds = self.report.get_round_count() if self.report else 0
        _normalized = result.user_text.strip("。！？!?,， ").strip()
        _is_greeting = (
            _normalized in _GREETING_INPUTS
            or ("你好" in _normalized and len(_normalized) <= 10)
            or (len(_normalized) <= 6 and _normalized.replace("呀", "").replace("啊", "").replace("哈", "") in {"你好", "嗨"})
        )
        if current_rounds <= 2 and _is_greeting:
            import random
            _GREETING_REPLIES = [
                "你好呀。[breath]今天感觉咋样？",
                "嗨，来了呀。[breath]最近怎么样？",
                "你好。[breath]在这儿呢，有啥想说的？",
            ]
            spoken = random.choice(_GREETING_REPLIES)
            analysis = "【情绪】平静【状态】开放【策略】破冰回应"
            full = f"{analysis}|||{spoken}"
            result.full_response = full
            result.analysis_text = analysis
            result.spoken_text = spoken
            result.clean_spoken = clean_for_display(spoken)
            result.tts_text = clean_for_tts(spoken)
            result.intent = "counseling"
            emit("start_ai_message", None)
            emit("stream_text", result.clean_spoken)
            emit("finish_streaming", None)
            if self.data:
                self.data.save_assistant_message(None, full, sample_rate=48000)
            if self.llm and hasattr(self.llm, "conversation_history"):
                self.llm.conversation_history.append({"role": "user", "content": result.user_text})
                # Only store spoken text in history, not analysis|||spoken
                self.llm.conversation_history.append({"role": "assistant", "content": clean_for_display(spoken)})
            if config.use_tts and self.tts and result.tts_text:
                emit("status", "正在播放...")
                self._executor.submit(self._play_tts, result.tts_text)
            metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
            return result

        # --- System suffix: RAG + round warning + scale (no agent dependency) ---
        with metrics.timer("rag.system_suffix"):
            system_suffix = self._build_system_suffix(result.user_text)

        from services.scales import get_scale_manager
        scale_mgr = get_scale_manager()

        # Round gate: don't start new scales in early rapport-building rounds.
        # Active scales (already in progress) are不受此限制.
        current_rounds = self.report.get_round_count() if self.report else 0
        allow_new_scale = current_rounds >= MIN_ROUNDS_BEFORE_SCALE

        # Active scale takes priority — keep asking until completed
        if self._active_scale:
            active_prompt = self._build_active_scale_prompt(
                self._active_scale, self._active_scale_q,
                self._active_scale_waiting_answer
            )
            if active_prompt:
                system_suffix += "\n" + active_prompt
                logger.warning(f"[ScaleDebug] active scale {self._active_scale} "
                               f"Q{self._active_scale_q} waiting={self._active_scale_waiting_answer}")
            # Mark that we've asked the question — next turn is a user answer
            if not self._active_scale_waiting_answer:
                self._active_scale_waiting_answer = True

            # Still detect other scales to queue them (don't discard)
            candidates = scale_mgr.recommend_scale_candidates(
                result.user_text, administered=self._administered_scales
            )
            for c in candidates:
                if c != self._active_scale and c not in self._scale_queue:
                    self._scale_queue.append(c)
                    logger.warning(f"[ScaleDebug] queued {c} (active: {self._active_scale})")
            if candidates:
                logger.warning(f"[ScaleDebug] candidates={candidates}, "
                               f"active={self._active_scale}, queue={self._scale_queue}")
        else:
            # No active scale — check queue first, then detect new
            if self._scale_queue:
                next_scale = self._scale_queue.pop(0)
                self._administered_scales.add(next_scale)
                self._active_scale = next_scale
                self._active_scale_q = 1
                self._active_scale_waiting_answer = False
                active_prompt = self._build_active_scale_prompt(next_scale, 1, False)
                if active_prompt:
                    system_suffix += "\n" + active_prompt
                    logger.warning(f"[ScaleDebug] started queued {next_scale}, asking Q1")
            elif not allow_new_scale:
                # Still in rapport-building rounds — don't start new scales yet
                logger.warning(
                    f"[ScaleDebug] skip new scale: round {current_rounds} < {MIN_ROUNDS_BEFORE_SCALE}"
                )
            else:
                # Build conversation context for keyword detection across turns
                scale_context = ""
                if self.llm and hasattr(self.llm, 'conversation_history'):
                    recent_user = [
                        m["content"] for m in self.llm.conversation_history[-6:]
                        if m.get("role") == "user"
                    ]
                    if recent_user:
                        scale_context = "\n".join(recent_user[-3:])

                # Combine context + current text for keyword matching
                detect_text = result.user_text
                if scale_context:
                    detect_text = scale_context + "\n" + result.user_text

                # Use candidates for multi-scale detection (keyword-based)
                candidates = scale_mgr.recommend_scale_candidates(
                    detect_text, administered=self._administered_scales
                )
                # Also check should_administer for agent/emotion fallback
                if not candidates:
                    single = scale_mgr.should_administer(
                        self.emotion_tracker, self.report,
                        user_text=result.user_text, administered=self._administered_scales,
                        agent_service=self.agent,
                        conversation_context=scale_context,
                    )
                    if single:
                        candidates = [single]

                logger.warning(f"[ScaleDebug] text={result.user_text!r}, "
                               f"candidates={candidates}, administered={self._administered_scales}")
                if candidates:
                    first = candidates[0]
                    self._administered_scales.add(first)
                    self._active_scale = first
                    self._active_scale_q = 1
                    self._active_scale_waiting_answer = False
                    # Queue the rest
                    for c in candidates[1:]:
                        if c not in self._administered_scales:
                            self._administered_scales.add(c)
                            self._scale_queue.append(c)
                    active_prompt = self._build_active_scale_prompt(first, 1, False)
                    if active_prompt:
                        system_suffix += "\n" + active_prompt
                        logger.warning(f"[ScaleDebug] started {first}, "
                                       f"queued={self._scale_queue}")

        if self.emotion_tracker:
            hint = self.emotion_tracker.get_intervention_hint()
            if hint:
                system_suffix += "\n" + hint

        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

        # --- Quick crisis keyword check (fast, before LLM) ---
        # Must run BEFORE the programmatic scale branch so that crisis responses
        # are never skipped by a scale question being generated directly.
        _skip_programmatic_scale = False
        if self.agent:
            quick_crisis = self.agent._keyword_crisis_risk(result.user_text)
            if quick_crisis.get("immediate_action"):
                from config import CRISIS_INTERVENTION_SUFFIX
                if final_suffix:
                    final_suffix += "\n" + CRISIS_INTERVENTION_SUFFIX
                else:
                    final_suffix = CRISIS_INTERVENTION_SUFFIX
                _skip_programmatic_scale = True
                logger.warning(f"[Pipeline] Crisis keywords detected pre-LLM: risk={quick_crisis.get('risk_level')}")

        # --- Programmatic first scale question (bypass LLM) ---
        # If we just started a new scale or moved to a new question and are NOT
        # waiting for an answer, generate the question directly from a template
        # instead of relying on the LLM (which may ignore the scale prompt in
        # favor of relaxation training recommendations from SYSTEM_PROMPT).
        # Skipped when crisis keywords are detected — safety takes priority.
        _programmatic_scale = False
        if self._active_scale and not self._active_scale_waiting_answer and not _skip_programmatic_scale:
            from services.scales import SCALES
            scale_def = SCALES.get(self._active_scale)
            if scale_def:
                q_num = self._active_scale_q
                total = len(scale_def["questions"])
                q_text = scale_def["questions"][q_num - 1]
                # Use natural question phrasing if available
                natural = NATURAL_SCALE_QUESTIONS.get((self._active_scale, q_num))
                if q_num == 1:
                    spoken_text = f"我先顺着你刚才说的了解一下。[breath]{natural or q_text}"
                else:
                    spoken_text = f"[breath]{natural or q_text}"

                analysis_text = (
                    f"【情绪识别】待评估【状态评估】配合中"
                    f"【变革话语】无【策略选择】量表评估 - {self._active_scale} Q{q_num}/{total}"
                )
                full_response = f"{analysis_text}|||{spoken_text}"

                # Emit UI updates
                emit("start_ai_message", None)
                emit("stream_text", clean_for_display(spoken_text))
                emit("finish_streaming", None)

                result.full_response = full_response
                result.analysis_text = analysis_text
                result.spoken_text = spoken_text
                result.clean_spoken = clean_for_display(spoken_text)
                result.tts_text = clean_for_tts(spoken_text)
                result.intent = "counseling"

                # Mark as waiting for answer
                self._active_scale_waiting_answer = True

                logger.warning(
                    f"[ScaleDebug] Programmatic Q{q_num} for {self._active_scale}: {q_text}"
                )

                # Save assistant message
                if self.data:
                    self.data.save_assistant_message(None, full_response, sample_rate=48000)

                # Keep LLM conversation history consistent
                if self.llm and hasattr(self.llm, "conversation_history"):
                    self.llm.conversation_history.append({
                        "role": "assistant",
                        "content": full_response
                    })

                # TTS
                if config.use_tts and self.tts and result.tts_text:
                    emit("status", "正在播放...")
                    try:
                        with get_metrics().timer("tts.play"):
                            self.tts.generate_and_play(result.tts_text)
                    except Exception as e:
                        logger.warning(f"TTS error: {e}")

                metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
                _programmatic_scale = True

        if _programmatic_scale:
            return result

        # Append extra system context (e.g. remaining scale questions at exit)
        if config.extra_system_suffix:
            if final_suffix:
                final_suffix += "\n" + config.extra_system_suffix
            else:
                final_suffix = config.extra_system_suffix

        # --- LLM stream + Agent classification (concurrent) ---
        # Start LLM immediately with RAG/scale context; agent runs in parallel.
        # This saves 1-3s of agent wait time before first LLM token.
        emit("start_ai_message", None)
        agent_future = self._executor.submit(
            self._classify_intent_emotion_crisis, result.user_text
        )

        try:
            with metrics.timer("llm.stream"):
                result.full_response, result.analysis_text, result.spoken_text = \
                    self._stream_llm(result.user_text, final_suffix, emit)
        except RuntimeError as e:
            if "LLM_NO_FINAL_CONTENT" in str(e):
                logger.warning(f"[Pipeline] LLM thinking-only, no content, asking retry")
                result.spoken_text = "不好意思，刚才没组织好语言。[breath]你能再说一遍吗？"
                result.analysis_text = "【情绪】待确认【状态】需要重试【策略】请求重述"
                result.full_response = f"{result.analysis_text}|||{result.spoken_text}"
                emit("stream_text", clean_for_display(result.spoken_text))
            else:
                logger.exception(f"[Pipeline] LLM RuntimeError: {e}")
                result.spoken_text = "系统出了点小问题。[breath]你能再说一遍吗？"
                result.analysis_text = "【情绪】待确认【状态】需要重试【策略】系统错误"
                result.full_response = f"{result.analysis_text}|||{result.spoken_text}"
                emit("stream_text", clean_for_display(result.spoken_text))
        except Exception as e:
            logger.exception(f"[Pipeline] LLM failed: {e}")
            result.spoken_text = "系统出了点小问题。[breath]你能再说一遍吗？"
            result.analysis_text = "【情绪】待确认【状态】需要重试【策略】系统错误"
            result.full_response = f"{result.analysis_text}|||{result.spoken_text}"
            emit("stream_text", clean_for_display(result.spoken_text))
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

        # Fallback: if LLM didn't output a [SCALE:...] tag for the current
        # question, try to infer the score from the user's plain text answer.
        # This handles cases where the LLM forgets the tag or formats it wrong.
        if self._active_scale and self._active_scale_waiting_answer:
            answered = self._scale_answers.get(self._active_scale, {})
            if self._active_scale_q not in answered:
                inferred = infer_scale_score_from_text(result.user_text, self._active_scale)
                if inferred is not None:
                    self._scale_answers.setdefault(self._active_scale, {})[self._active_scale_q] = inferred
                    logger.warning(
                        f"[ScaleDebug] inferred score {self._active_scale} "
                        f"Q{self._active_scale_q} = {inferred} from user_text: {result.user_text!r}"
                    )

        # Advance active scale after scoring
        if self._active_scale:
            from services.scales import SCALES
            scale_def = SCALES.get(self._active_scale)
            if scale_def:
                total = len(scale_def["questions"])
                answered = self._scale_answers.get(self._active_scale, {})
                current_q = self._active_scale_q

                if current_q in answered:
                    # Current question was scored successfully — advance
                    next_q = None
                    for i in range(1, total + 1):
                        if i not in answered:
                            next_q = i
                            break
                    if next_q is None:
                        # Scale complete
                        completed_name = self._active_scale
                        logger.warning(f"[ScaleDebug] completed scale {completed_name}: {answered}")
                        result.scale_completed = True
                        result.completed_scale_name = completed_name
                        self._active_scale = None
                        self._active_scale_q = 1
                        self._active_scale_waiting_answer = False
                        # Start next queued scale if any
                        if self._scale_queue:
                            next_scale = self._scale_queue.pop(0)
                            self._administered_scales.add(next_scale)
                            self._active_scale = next_scale
                            self._active_scale_q = 1
                            self._active_scale_waiting_answer = False
                            logger.warning(f"[ScaleDebug] starting queued {next_scale}")
                        else:
                            # All triggered scales are now complete
                            result.all_scales_completed = True
                            logger.warning(f"[ScaleDebug] all scales completed (last: {completed_name})")
                    else:
                        self._active_scale_q = next_q
                        self._active_scale_waiting_answer = False  # need to ask next_q
                        logger.warning(f"[ScaleDebug] active scale {self._active_scale}, next Q{self._active_scale_q}")
                else:
                    # No score for current Q — LLM was clarifying or asking again
                    self._active_scale_waiting_answer = True
                    logger.warning(f"[ScaleDebug] no score for Q{current_q}, staying waiting")

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
        # Skip normal response TTS when END tag is detected — the session-end
        # flow will generate and play its own farewell TTS. Playing both causes
        # overlapping audio and stuttering.
        tts_future = None
        skip_normal_tts = bool(result.end_type)
        if skip_normal_tts:
            logger.info("[TTS] Skipping normal response TTS — END tag detected, session-end TTS will handle farewell.")
        if config.use_tts and self.tts and result.tts_text and not skip_normal_tts:
            emit("status", "正在播放...")
            tts_future = self._executor.submit(self._play_tts, result.tts_text)
            latency_ms = (time.perf_counter() - pipeline_started) * 1000.0
            logger.warning(f"[Latency] input→TTS: {latency_ms:.0f}ms | spoken_len={len(result.spoken_text)}")

        # Agent results + emotion tracking + crisis (parallel with TTS)
        try:
            agent_done = agent_future.result(timeout=10)
            result.intent, result.emotion_result, crisis_keyword_result = agent_done
        except Exception as e:
            logger.warning(f"Agent classification failed: {e}")
            result.intent = "counseling"
            result.emotion_result = {"emotion": "neutral", "intensity": 0.0}
            crisis_keyword_result = {"risk_level": 0, "indicators": [], "immediate_action": False}

        # Emotion keyword override: if agent misclassifies negative text as happy
        _emotion_text = result.user_text.lower()
        _negative_emotion_kw = {
            "不开心": "depressed", "难受": "depressed", "低落": "depressed",
            "没意思": "depressed", "心情不好": "depressed", "心里累": "depressed",
            "焦虑": "anxious", "紧张": "anxious", "烦躁": "anxious",
            "害怕": "fearful", "恐惧": "fearful",
            "愤怒": "angry", "生气": "angry",
        }
        detected_emotion = result.emotion_result.get("emotion", "neutral")
        for kw, emo in _negative_emotion_kw.items():
            if kw in _emotion_text and detected_emotion in ("happy", "neutral"):
                logger.warning(f"[EmotionFix] keyword '{kw}' overrides {detected_emotion} → {emo}")
                result.emotion_result = {"emotion": emo, "intensity": 0.7}
                break

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
            # Only trigger LLM crisis reassessment for negative/risk emotions
            # or very high intensity. Positive emotions like "happy" should NOT
            # trigger an extra LLM call.
            _risk_emotions = {
                "sad", "depressed", "hopeless", "angry", "fearful",
                "anxious", "stressed", "traumatized", "desperate",
            }
            if emotion in _risk_emotions or intensity >= 0.85:
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

        # Don't block pipeline waiting for TTS — let it play in background.
        # Use a callback to log errors without holding up the UI.
        if tts_future is not None:
            tts_future.add_done_callback(
                lambda f: logger.warning(f"TTS error: {f.exception()}") if f.exception() else None
            )

        result.scale_active = bool(self._active_scale)
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

    def _build_active_scale_prompt(self, scale_name: str, q_num: int,
                                    waiting_answer: bool) -> str:
        """Build a prompt that tells the LLM which symptom dimension to explore.

        The prompt is for the LLM's internal reasoning only.  The hard rule
        at the top ensures clinical jargon never leaks into spoken output.
        """
        from services.scales import SCALES
        scale = SCALES.get(scale_name)
        if not scale:
            return ""

        total = len(scale["questions"])
        q_text = scale["questions"][q_num - 1]
        natural_q = NATURAL_SCALE_QUESTIONS.get((scale_name, q_num), q_text)
        options_text = " / ".join(
            f"{opt['score']}-{opt['label']}" for opt in scale["options"]
        )

        next_q_num = q_num + 1
        next_q_text = scale["questions"][next_q_num - 1] if next_q_num <= total else None
        next_natural = NATURAL_SCALE_QUESTIONS.get((scale_name, next_q_num), next_q_text) if next_q_text else None

        # Common preamble — invisible-assessment expression rules
        rule_block = f"""
【无感量表表达规则】
你正在做的是后台症状评估，但口语回复必须像自然聊天。
口语回复中严禁出现以下词语：
"量表""问卷""题""这一题""上一题""下一题""第几题""评分""分数"
"{scale_name}""PHQ-9""GAD-7""PCL-5""接下来"

禁止说：
- "接下来这一题是关于……"
- "下一题想问……"
- "上一题你的回答……"
- "这个量表……"

允许说：
- "我也想顺着了解一下……"
- "那睡眠这块呢……"
- "这种状态挺频繁的，吃饭/睡觉有没有也受影响？"
- "我再轻轻问一句……"
- "这块我想多了解一点……"
"""

        # Not yet asked — ask current question, no scoring
        if not waiting_answer:
            return f"""{rule_block}
【后台任务】询问第 {q_num}/{total} 个症状维度。
维度描述：{q_text}
口语化表述：{natural_q}

本轮用户还没有回答，不要输出 SCALE 标签。
先简短共情一句（不超过15字），然后用口语化表述自然询问。
不要泛泛追问原因，要围绕该维度具体问。

【优先级】如果本轮同时存在知识库提示，必须以症状探索为主。知识库内容只能作为一句简短支持或过渡。
"""

        # Waiting for answer — score this turn
        if next_q_text:
            return f"""{rule_block}
【后台任务】评分第 {q_num}/{total} 个维度并自然过渡到下一个维度。
当前维度：{q_text}
下一维度口语化表述：{next_natural}

用户本轮输入是对当前维度的回答。

评分规则：
- 如果用户回答足够判断频率/程度，在回复末尾输出 [SCALE:{scale_name}:Q{q_num}:S分数]
- 然后把下一维度自然融入口语追问，不要说"下一题/接下来"
- 如果用户回答模糊（如"还好""差不多"），禁止猜测分数，不要输出 SCALE 标签。请围绕当前维度追问一个澄清问题，例如"是偶尔几天，还是一半以上时间都这样？"

评分标准：{options_text}

禁止改问"发生了什么事""为什么这样"这类泛化问题。

【优先级】如果本轮同时存在知识库提示，必须以症状探索为主。
"""
        # Last question — score and wrap up
        return f"""{rule_block}
【后台任务】评分最后一个维度（第 {q_num}/{total}）。
当前维度：{q_text}

用户本轮输入是对该维度的回答。

评分规则：
- 如果用户回答足够判断，在回复末尾输出 [SCALE:{scale_name}:Q{q_num}:S分数]
- 然后用一句自然、温暖的话收束，不要暴露"最后一题/量表结束"等信息
- 如果用户回答模糊，禁止猜测分数。请追问一个澄清问题。

评分标准：{options_text}

【优先级】如果本轮同时存在知识库提示，必须以症状探索为主。
"""

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
            # Use multi-turn context for RAG retrieval
            rag_text = text
            if self.llm and hasattr(self.llm, 'conversation_history'):
                recent = [m["content"] for m in self.llm.conversation_history[-6:]
                          if m.get("role") == "user"]
                if recent:
                    rag_text = "\n".join(recent[-3:] + [text])
            rag_suffix = self.rag.get_system_suffix(rag_text)
            # Truncate RAG suffix to keep prompt manageable for real-time voice
            MAX_RAG_SUFFIX_CHARS = 1200
            if rag_suffix and len(rag_suffix) > MAX_RAG_SUFFIX_CHARS:
                rag_suffix = rag_suffix[:MAX_RAG_SUFFIX_CHARS] + "\n【知识库已截断，仅保留最相关内容】"
            logger.warning(f"[RagDebug] user_text={text!r} rag_text={rag_text!r} "
                          f"injected={bool(rag_suffix)} len={len(rag_suffix) if rag_suffix else 0}")
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
                    cleaned = _RE_THINK.sub('', spoken_buffer)
                    cleaned = _RE_PIPE_TAG.sub('', cleaned)
                    cleaned = _RE_REC_TAG.sub('', cleaned)
                    cleaned = _RE_END_TAG.sub('', cleaned)
                    cleaned = _RE_SCALE_TAG.sub('', cleaned)
                    cleaned = _RE_BRACKETS_CN.sub('', cleaned)
                    cleaned = _RE_BREATH_LAUGH.sub('', cleaned)
                    if cleaned.strip():
                        emit("stream_text", cleaned.strip())
                continue

            cleaned = _RE_THINK.sub('', chunk)
            cleaned = _RE_PIPE_TAG.sub('', cleaned)
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
                cleaned = _RE_THINK.sub('', spoken_text)
                cleaned = _RE_PIPE_TAG.sub('', cleaned)
                cleaned = _RE_REC_TAG.sub('', cleaned)
                cleaned = _RE_END_TAG.sub('', cleaned)
                cleaned = _RE_SCALE_TAG.sub('', cleaned)
                cleaned = _RE_BRACKETS_CN.sub('', cleaned)
                cleaned = _RE_BREATH_LAUGH.sub('', cleaned)
                if cleaned.strip():
                    emit("stream_text", cleaned.strip())
                else:
                    logger.warning(f"Spoken text empty after cleaning, using raw response as fallback")
                    fallback = _RE_THINK.sub('', spoken_text)
                    fallback = _RE_REC_TAG.sub('', fallback)
                    fallback = _RE_END_TAG.sub('', fallback)
                    fallback = _RE_SCALE_TAG.sub('', fallback)
                    fallback = _RE_BREATH_LAUGH.sub('', fallback)
                    if fallback.strip():
                        emit("stream_text", fallback.strip())
                        spoken_text = fallback.strip()
                    else:
                        spoken_text = spoken_text

        # Final safety: if spoken_text is still empty or only contains analysis
        # tags that will be stripped by clean_for_display, use a safe fallback.
        if not clean_for_display(spoken_text).strip():
            logger.warning(
                f"spoken_text empty after final cleaning. full_response_head={full_response[:300]!r}"
            )
            spoken_text = make_safe_fallback_reply(text)

        return full_response, analysis_text, spoken_text
