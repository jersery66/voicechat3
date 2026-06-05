# Pipeline - Unified conversation pipeline and shared constants

import re
import time
import traceback
from typing import Optional, Any, Callable, List, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger import get_logger
from services.metrics import get_metrics
from config import MIN_ROUNDS_BEFORE_SCALE, SCALE_ROUTE_CONFIDENCE, RELAX_ROUTE_CONFIDENCE

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


def limit_to_one_question(text: str) -> str:
    """Prevent one reply from containing multiple questions."""
    if not text:
        return ""
    marks = [m.start() for m in re.finditer(r"[？?]", text)]
    if len(marks) <= 1:
        return text.strip()
    # Only keep up to the first question mark
    return text[:marks[0] + 1].strip()


def detect_phq_item_from_text(text: str) -> Optional[int]:
    """Detect which PHQ-9 item the user's text naturally refers to.

    Returns item number (1-9) or None if no clear match.
    Used to score the symptom the user is actually talking about,
    rather than forcing the current active question.
    """
    t = text or ""
    if any(x in t for x in ["没兴趣", "没意思", "提不起劲", "不想做", "做什么都没劲"]):
        return 1
    if any(x in t for x in ["心情不好", "不开心", "低落", "沮丧", "没希望", "绝望"]):
        return 2
    if any(x in t for x in ["睡不着", "失眠", "睡不好", "早醒", "睡太多", "入睡困难"]):
        return 3
    if any(x in t for x in ["累", "没力气", "没劲", "疲惫", "没活力", "乏力"]):
        return 4
    if any(x in t for x in ["吃不下", "没胃口", "吃太多", "饭量"]):
        return 5
    if any(x in t for x in ["觉得自己很糟", "失败", "失望", "自责", "不够好"]):
        return 6
    if any(x in t for x in ["注意力", "集中不了", "看不进去", "专注"]):
        return 7
    if any(x in t for x in ["动作变慢", "坐不住", "烦躁", "动来动去", "说话慢"]):
        return 8
    if any(x in t for x in ["不想活", "伤害自己", "死", "自杀", "自残"]):
        return 9
    return None


def is_user_explicit_end_text(text: str) -> bool:
    """Check if user explicitly wants to end the session.

    Weak responses like "好吧", "嗯", "没有" should NOT trigger session end.
    """
    t = (text or "").strip("。！？!?,， ")

    # Weak responses — definitely not ending
    weak = {"好吧", "嗯", "哦", "没有", "行吧", "可以吧", "还好吧", "不知道", "嗯嗯", "好的", "行"}
    if t in weak:
        return False

    explicit_end = [
        "不想聊了", "今天不聊了", "今天先这样", "先到这吧", "先这样吧",
        "我要结束", "结束吧", "不说了", "我想休息了", "我累了想睡了",
        "可以结束了", "聊完了", "好多了", "舒服多了", "轻松多了", "没事了", "现在好多了",
    ]
    return any(x in t for x in explicit_end)


def is_scale_interruption_text(text: str) -> bool:
    """Check if user is interrupting/resisting scale questioning.

    Returns True if the user is clearly resisting, changing topic, or
    expressing frustration about being questioned.
    """
    t = (text or "").strip()

    interruption_phrases = [
        "为啥一直问", "为什么一直问", "别问了", "不想回答", "换个话题",
        "不想说这个", "就是想聊天", "你怎么老问", "别再问了",
        "聊点别的", "说点别的", "不想聊这个", "不要问了",
        "我来聊天的", "我不是来做问卷的",
    ]
    return any(x in t for x in interruption_phrases)


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
        self._scale_pause_turns: int = 0            # turns to pause before next scale item
        self._crisis_lock_turns: int = 0            # turns to block all scales after crisis
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
        self._scale_pause_turns = 0
        self._crisis_lock_turns = 0

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

    @staticmethod
    def _is_valid_scale_trigger_text(text: str) -> bool:
        """Check if user text is meaningful enough to trigger a scale.

        Suppresses scale start on ASR noise, meaningless short text, or
        utterances with no clear symptom/emotion keywords.
        Symptom keywords take priority over length checks.
        """
        t = (text or "").strip("。！？!?,， ")
        if not t:
            return False

        # Symptom keywords pass immediately, even if short (e.g., "睡不好")
        symptom_keywords = [
            "心情不好", "不开心", "低落", "难受", "没意思", "没兴趣",
            "睡不着", "失眠", "睡不好", "早醒", "睡太多",
            "吃不下", "没胃口", "吃太多",
            "累", "没力气", "没劲", "疲惫", "乏力",
            "焦虑", "紧张", "害怕", "恐惧", "烦躁",
            "不想活", "想死", "自杀", "自残", "伤害自己",
            "绝望", "没希望", "痛苦", "心里累", "撑不住",
        ]
        if any(k in t for k in symptom_keywords):
            return True

        # No symptom keywords — apply length and noise filters
        if len(t) < 4:
            return False

        # Obvious ASR noise or meaningless short phrases
        noise = {"啊", "哦", "嗯", "好", "是吧", "最早", "友谊酒店", "528",
                 "没有了吧", "还好吧", "不知道", "嗯嗯", "好的", "行"}
        if t in noise:
            return False

        # Mostly digits
        digit_count = sum(ch.isdigit() for ch in t)
        if digit_count >= 2 and digit_count / max(len(t), 1) > 0.4:
            return False

        return False

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

        # Crisis lock: block all scale logic for N turns after crisis detection
        if self._crisis_lock_turns > 0:
            self._crisis_lock_turns -= 1
            self._active_scale = None
            logger.warning(f"[CrisisDebug] crisis lock active, skip scale logic: remaining={self._crisis_lock_turns}")

        # Interruption detection: if user resists questioning, stop scale immediately
        if self._active_scale and is_scale_interruption_text(result.user_text):
            logger.warning(f"[ScaleDebug] user interrupted scale questioning: {result.user_text!r}")
            self._active_scale = None
            self._active_scale_q = 1
            self._active_scale_waiting_answer = False
            self._scale_pause_turns = 3  # pause before next scale attempt

        # Round gate
        current_rounds = self.report.get_round_count() if self.report else 0

        # --- Agent unified routing (3B model decides scale/relaxation/crisis) ---
        agent_route = None
        if self.agent and self.agent.is_available():
            try:
                relax_done = self._get_relaxation_done()
                agent_route = self.agent.route_conversation_actions(
                    user_text=result.user_text,
                    recent_history=self._get_recent_dialogue_text(),
                    current_round=current_rounds,
                    active_scale=self._active_scale,
                    collected_scales=self._scale_answers,
                    relaxation_done=relax_done,
                )
                logger.warning(
                    f"[AgentRoute] user={result.user_text!r} "
                    f"scale_action={agent_route.get('scale_action')} "
                    f"scale={agent_route.get('scale')} "
                    f"item={agent_route.get('item')} "
                    f"probe_hint={agent_route.get('probe_hint', '')[:80]} "
                    f"recommend_relaxation={agent_route.get('recommend_relaxation')} "
                    f"relaxation_type={agent_route.get('relaxation_type')} "
                    f"confidence={agent_route.get('confidence')} "
                    f"risk={agent_route.get('risk_level')} "
                    f"reason={agent_route.get('reason', '')[:100]}"
                )
            except Exception as e:
                logger.warning(f"[AgentRoute] failed: {e}")
                agent_route = None

        # Hard safety: crisis keywords always take priority (not dependent on agent)
        if self.agent:
            quick_crisis = self.agent._keyword_crisis_risk(result.user_text)
            if quick_crisis.get("immediate_action"):
                from config import CRISIS_INTERVENTION_SUFFIX
                if system_suffix:
                    system_suffix += "\n" + CRISIS_INTERVENTION_SUFFIX
                else:
                    system_suffix = CRISIS_INTERVENTION_SUFFIX
                self._crisis_lock_turns = 4
                self._active_scale = None
                self._active_scale_q = 1
                self._active_scale_waiting_answer = False
                self._scale_queue.clear()
                logger.warning(f"[Pipeline] Crisis keywords detected: risk={quick_crisis.get('risk_level')}, lock=4 turns")
                # Override agent route for safety
                if agent_route:
                    agent_route["immediate_crisis"] = True
                    agent_route["scale_action"] = "pause"

        # Gate: block scale logic during crisis lock
        if self._crisis_lock_turns > 0:
            self._crisis_lock_turns -= 1
            self._active_scale = None
            allow_new_scale = False
            logger.warning(f"[CrisisDebug] crisis lock active, remaining={self._crisis_lock_turns}")
        else:
            allow_new_scale = True

        # --- Scale logic driven by agent route ---
        if agent_route and agent_route.get("confidence", 0) >= SCALE_ROUTE_CONFIDENCE:
            scale_action = agent_route.get("scale_action", "none")
            suggested_scale = agent_route.get("scale")
            probe_hint = agent_route.get("probe_hint", "")

            if scale_action == "pause":
                # Agent says user is resisting or context isn't right
                if self._active_scale:
                    logger.warning(f"[ScaleDebug] agent pause: clearing {self._active_scale}")
                self._active_scale = None
                self._active_scale_q = 1
                self._active_scale_waiting_answer = False
                self._scale_pause_turns = 2

            elif scale_action == "start" and not self._active_scale and allow_new_scale:
                # Agent recommends starting a new scale
                if suggested_scale:
                    self._active_scale = suggested_scale
                    # Use agent's item suggestion if available
                    route_item = agent_route.get("item")
                    if isinstance(route_item, int) and route_item > 0:
                        self._active_scale_q = route_item
                    else:
                        self._active_scale_q = self._next_unanswered_item(suggested_scale)
                    self._active_scale_waiting_answer = False
                    logger.warning(
                        f"[ScaleDebug] agent start: {suggested_scale} Q{self._active_scale_q}, "
                        f"confidence={agent_route.get('confidence')}, "
                        f"reason={agent_route.get('reason', '')[:60]}"
                    )
                    # Add subtle probe hint to system suffix
                    if probe_hint:
                        system_suffix += f"\n【隐性症状采样】{probe_hint}\n不要说量表、问卷、题目、评分。每轮最多一个问题。"

            elif scale_action == "continue" and self._active_scale:
                # Agent says continue probing current scale
                if probe_hint:
                    system_suffix += f"\n【隐性症状采样】{probe_hint}\n不要说量表、问卷、题目、评分。每轮最多一个问题。"
                logger.warning(f"[ScaleDebug] agent continue: {self._active_scale}, hint={probe_hint[:40]}")

            # Relaxation recommendation from agent
            if agent_route.get("recommend_relaxation") and agent_route.get("confidence", 0) >= RELAX_ROUTE_CONFIDENCE:
                rec_type = agent_route.get("relaxation_type") or "breathing"
                result.relaxation_rec = rec_type
                logger.warning(f"[ScaleDebug] agent recommend relaxation: {rec_type}")

        elif self._active_scale:
            # Agent unavailable — keep active scale alive with subtle hint
            # Use next unanswered item, not current _active_scale_q
            next_item = self._next_unanswered_item(self._active_scale)
            hint_q = next_item if next_item else self._active_scale_q
            logger.warning(
                f"[ScaleDebug] agent route failed but active scale remains: "
                f"{self._active_scale} Q{hint_q}"
            )
            natural = NATURAL_SCALE_QUESTIONS.get(
                (self._active_scale, hint_q), ""
            )
            if natural:
                system_suffix += f"""
【隐性症状采样】当前仍有一个状态点没了解完整。继续正常聊天。
如果语境自然，顺手了解：{natural}
不要说量表、问卷、题目、评分。每轮最多一个问题。
"""

        # Scale pause countdown
        if self._scale_pause_turns > 0:
            self._scale_pause_turns -= 1

        if self.emotion_tracker:
            hint = self.emotion_tracker.get_intervention_hint()
            if hint:
                system_suffix += "\n" + hint

        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

        # --- No programmatic scale questions ---
        # Scales are now explored naturally by the LLM via subtle system_suffix
        # hints. Background scoring happens via [SCALE:] tags and
        # infer_scale_score_from_text(). The LLM generates a natural response,
        # not a template question.

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
        # Limit to one question per reply to prevent rapid-fire questioning
        result.spoken_text = limit_to_one_question(result.spoken_text)
        result.clean_spoken = clean_for_display(result.spoken_text)
        emit("clean_last_ai", result.clean_spoken)
        result.tts_text = clean_for_tts(result.spoken_text)

        # --- Tag detection ---
        raw_end_type = detect_tag(result.full_response, END_PATTERNS)
        # Only allow END tag if user explicitly wants to end (not "好吧", "嗯", etc.)
        if raw_end_type and is_user_explicit_end_text(result.user_text):
            result.end_type = raw_end_type
        else:
            if raw_end_type:
                logger.warning(
                    f"[EndDebug] suppress END tag: user did not explicitly end. "
                    f"user={result.user_text!r}, raw_end={raw_end_type}"
                )
            result.end_type = None
        # Only override agent's relaxation_rec if LLM explicitly output a REC tag
        llm_rec = detect_tag(result.full_response, REC_TAGS)
        if llm_rec:
            result.relaxation_rec = llm_rec
        result.scale_tags = parse_scale_tags(result.full_response)
        # Track answered questions per scale
        for scale_name, answers in result.scale_tags.items():
            if scale_name not in self._scale_answers:
                self._scale_answers[scale_name] = {}
            self._scale_answers[scale_name].update(answers)

        # Short answer scoring: "经常", "是的", "没有" etc. for active scale items
        if self._active_scale:
            answered = self._scale_answers.get(self._active_scale, {})
            if self._active_scale_q not in answered:
                short_score = self._score_short_scale_answer(
                    self._active_scale, self._active_scale_q, result.user_text
                )
                if short_score is not None:
                    self._record_scale_score(self._active_scale, self._active_scale_q, short_score)
                    result.scale_tags.setdefault(self._active_scale, {})[self._active_scale_q] = short_score
                    logger.warning(
                        f"[ScaleDebug] short answer scored {self._active_scale} "
                        f"Q{self._active_scale_q} = {short_score}, text={result.user_text!r}"
                    )

        # Fallback: if LLM didn't output a [SCALE:...] tag for the current
        # question, try to infer the score from the user's plain text answer.
        # Also detect if user is naturally talking about a different symptom.
        # In latent mode, also try scoring even without explicit waiting state.
        if self._active_scale:
            answered = self._scale_answers.get(self._active_scale, {})
            if self._active_scale_q not in answered:
                # First check if user is talking about a different PHQ-9 item
                detected_item = None
                if self._active_scale == "PHQ-9":
                    detected_item = detect_phq_item_from_text(result.user_text)
                    if detected_item and detected_item != self._active_scale_q and detected_item not in answered:
                        # User is talking about a different symptom — score that instead
                        inferred = infer_scale_score_from_text(result.user_text, self._active_scale)
                        if inferred is not None:
                            self._scale_answers.setdefault(self._active_scale, {})[detected_item] = inferred
                            logger.warning(
                                f"[ScaleDebug] detected symptom Q{detected_item} (not Q{self._active_scale_q}), "
                                f"scored {self._active_scale} Q{detected_item} = {inferred}"
                            )

                # Still try to score the current question
                if self._active_scale_q not in self._scale_answers.get(self._active_scale, {}):
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
                    completed_name = self._active_scale
                    self._advance_active_scale_after_score(completed_name)
                    if self._active_scale is None:
                        # Scale fully complete
                        result.scale_completed = True
                        result.completed_scale_name = completed_name
                        if self._scale_queue:
                            logger.warning(f"[ScaleDebug] clearing stale scale_queue: {self._scale_queue}")
                            self._scale_queue.clear()
                        result.all_scales_completed = True
                        logger.warning(f"[ScaleDebug] all scales completed (last: {completed_name})")
                    else:
                        # Advanced to next item — pause 1 turn, don't chain questions
                        self._scale_pause_turns = 1
                        logger.warning(
                            f"[ScaleDebug] scored {completed_name} Q{current_q}={answered.get(current_q)}; "
                            f"next Q{self._active_scale_q}, pause 1 turn"
                        )
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
        """Build a subtle system hint for natural symptom exploration.

        This does NOT tell the LLM to "ask question N" or "score this item".
        Instead, it hints which symptom area to naturally explore, and lets
        the LLM generate a conversational response. Background scoring
        happens via [SCALE:] tags and infer_scale_score_from_text().
        """
        from services.scales import SCALES
        scale = SCALES.get(scale_name)
        if not scale:
            return ""

        total = len(scale["questions"])
        q_text = scale["questions"][q_num - 1]
        natural_q = NATURAL_SCALE_QUESTIONS.get((scale_name, q_num), q_text)

        # Map scale to a brief clinical hint
        scale_hints = {
            "PHQ-9": "抑郁倾向",
            "GAD-7": "焦虑倾向",
            "PCL-5": "创伤应激倾向",
        }
        clinical_hint = scale_hints.get(scale_name, "心理困扰")

        # Build a list of symptom areas still to explore
        answered = self._scale_answers.get(scale_name, {})
        remaining_areas = []
        for i, q in enumerate(scale["questions"], start=1):
            if i not in answered:
                area = NATURAL_SCALE_QUESTIONS.get((scale_name, i), q)
                remaining_areas.append(area)

        if not waiting_answer:
            # First time exploring this symptom area — give a subtle hint
            return f"""
【后台提示】来访者可能有{clinical_hint}。可以自然了解一下相关方面，但不要暴露你在做评估。
当前可以自然了解的方面：{natural_q}
口语回复严禁出现"量表""问卷""题""评分""分数""PHQ-9""GAD-7""PCL-5""接下来""第几题"。
像正常聊天一样自然地问，不要像在做问卷。
"""
        else:
            # Already asked — the LLM should continue the conversation naturally.
            # Background scoring will happen via [SCALE:] tags.
            # Hint: if user's response clearly maps to a score, output the tag.
            options_text = " / ".join(
                f"{opt['score']}-{opt['label']}" for opt in scale["options"]
            )
            return f"""
【后台提示】来访者正在回应关于"{natural_q}"的了解。
如果回答足够判断频率/程度，在回复末尾输出 [SCALE:{scale_name}:Q{q_num}:S分数]。
评分标准：{options_text}
如果回答模糊，不要猜分数，自然追问一句。
口语回复严禁出现"量表""问卷""题""评分""分数""PHQ-9""GAD-7""PCL-5""接下来"。
"""

    def _next_unanswered_item(self, scale_name: str):
        """Get the next unanswered question number for a scale, or None if complete."""
        from services.scales import SCALES
        total = len(SCALES.get(scale_name, {}).get("questions", []))
        answered = self._scale_answers.get(scale_name, {})
        for i in range(1, total + 1):
            if i not in answered:
                return i
        return None

    def _record_scale_score(self, scale_name: str, item: int, score: int):
        """Record a scale score into _scale_answers and mark as administered."""
        self._scale_answers.setdefault(scale_name, {})
        self._scale_answers[scale_name][int(item)] = int(score)
        self._administered_scales.add(scale_name)

    def _advance_active_scale_after_score(self, scale_name: str):
        """After scoring an item, advance to next unanswered or complete."""
        next_item = self._next_unanswered_item(scale_name)
        if next_item is None:
            logger.warning(f"[ScaleDebug] {scale_name} completed: {self._scale_answers.get(scale_name, {})}")
            self._active_scale = None
            self._active_scale_q = 1
            self._active_scale_waiting_answer = False
            return
        self._active_scale = scale_name
        self._active_scale_q = next_item
        self._active_scale_waiting_answer = False
        logger.warning(f"[ScaleDebug] advance {scale_name} to Q{next_item}")

    def _score_short_scale_answer(self, scale_name: str, item: int, user_text: str):
        """Score short natural answers to the currently active scale item.

        Returns score (0-3) or None if can't determine.
        """
        t = (user_text or "").strip("。！？!?,， ").lower()
        if not t:
            return None

        # Denial
        if t in {"没有", "没", "没有了", "也没有", "不是", "不太会", "不会", "不"}:
            return 0

        # Frequency answers (PHQ-9 / GAD-7)
        if any(x in t for x in ["偶尔", "有时候", "有时", "几天", "一两天"]):
            return 1
        if any(x in t for x in ["经常", "挺多", "不少", "一半以上", "大多数", "多数时候", "好多天"]):
            return 2
        if any(x in t for x in ["每天", "天天", "几乎每天", "一直", "总是", "老是", "基本每天"]):
            return 3

        # Affirmative without frequency — conservative score 1
        if t in {"是", "是的", "对", "对的", "嗯", "有", "会", "还会", "会的"}:
            return 1

        return None

    def _get_relaxation_done(self) -> bool:
        """Check if relaxation training was completed this session."""
        if hasattr(self, '_relaxation_done'):
            return self._relaxation_done
        return False

    def _get_recent_dialogue_text(self, max_turns: int = 6) -> str:
        """Get recent dialogue text for agent context."""
        if not self.llm or not hasattr(self.llm, 'conversation_history'):
            return ""
        recent = self.llm.conversation_history[-max_turns * 2:]
        lines = []
        for msg in recent:
            role = "来访者" if msg["role"] == "user" else "薇薇老师"
            content = msg.get("content", "")[:150]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

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

        # Final safety: if spoken_text is still empty or only contains analysis
        # tags that will be stripped by clean_for_display, use a safe fallback.
        if not clean_for_display(spoken_text).strip():
            logger.warning(
                f"spoken_text empty after final cleaning. full_response_head={full_response[:300]!r}"
            )
            spoken_text = make_safe_fallback_reply(text)

        return full_response, analysis_text, spoken_text
