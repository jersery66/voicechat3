# Pipeline - Unified conversation pipeline and shared constants

import re
import hashlib
import threading
import time
import traceback
from typing import Optional, Any, Callable, List, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger import get_logger, text_meta
from services.metrics import get_metrics
from config import (
    MIN_ROUNDS_BEFORE_SCALE,
    MIN_ROUNDS_FOR_RELAXATION,
    AGENT_ROUTE_ENABLED,
    AGENT_ROUTE_COOLDOWN_ROUNDS,
)
from conversation.offer_ledger import OfferLedger, offer_snapshot_fields
from conversation.turn_signals import (
    detect_offer_question,
    detect_risk_marker,
    detect_wants_to_talk,
)
from decision.timing import (
    OUTCOME_BY_REASON,
    OfferKind,
    OfferOutcome,
    OfferState,
    mentions_offer,
    rank_scale_candidates,
)
from decision.posture import FOCUS_NAMES_ZH, FOCUS_LABELS

logger = get_logger(__name__)


_RELAXATION_TYPE_ALIASES = {
    "breathing": "breathing",
    "mindfulness": "meditation",
    "meditation": "meditation",
    "muscle": "muscle",
    "muscle_relaxation": "muscle",
    "progressive_muscle_relaxation": "muscle",
}

#: How each exercise is named to a participant.  The writer needs the same
#: label the Center will highlight, otherwise it can promise one exercise and
#: open another.  "meditation" used to read "正念练习" while the Center's button
#: read the same, but the left panel and the report called it "冥想放松训练",
#: so one participant was told to look for a "正念练习" that did not exist under
#: that name.  The label now matches the catalog display name exactly.
_RELAXATION_LABELS = {
    "breathing": "呼吸放松",
    "muscle": "肌肉放松",
    "meditation": "冥想放松",
}


def _focus_name(label: str) -> str:
    """Chinese display name for a focus label, used in prompt and report text."""
    return FOCUS_NAMES_ZH.get(label, label)


def _normalize_relaxation_type(value) -> Optional[str]:
    """Map an explicit user content preference to the UI/media contract."""
    if value is None or not str(value).strip():
        return None
    return _RELAXATION_TYPE_ALIASES.get(str(value).strip().lower())


# ==================== Tag Detection Constants (single source of truth) ====================
# Tag constants and cleaning functions live in core.tags (pure domain logic).
# They are re-exported here so existing call sites
# (`from services.pipeline import clean_for_display, END_PATTERNS, ...`)
# keep working unchanged.
from core.tags import (  # noqa: F401  (re-exported for backward compatibility)
    END_PATTERNS,
    REC_TAGS,
    SCALE_PATTERN,
    _RE_REC_TAG,
    _RE_END_TAG,
    _RE_SCALE_TAG,
    _RE_BRACKETS_CN,
    _RE_PIPE_TAG,
    _RE_BREATH_LAUGH,
    _RE_THINK,
    _COMPILED_END_PATTERNS,
    _COMPILED_REC_TAGS,
    parse_scale_tags,
    detect_tag,
    _FORBIDDEN_INTERNAL_TERMS,
    _contains_internal_leak,
    clean_for_display,
    clean_for_tts,
)

# Scoring / symptom-signal logic lives in core.scoring (pure domain logic).
# Re-exported for backward compatibility, same as core.tags above.
from core.scoring import (  # noqa: F401  (re-exported for backward compatibility)
    PHQ_POSITIVE_KEYWORDS_BY_ITEM,
    GAD7_POSITIVE_KEYWORDS_BY_ITEM,
    FREQUENCY_WORDS,
    SCALE_EVIDENCE_THRESHOLD,
    score_symptom_signals,
)

# Scale administration state is owned by assessment.ScaleRuntime.  This
# module only re-exports pure scoring helpers and keeps observation counters.
from assessment import ScaleAnswerInterpreter, ScaleRuntime
from conversation.response_builder import ResponseBuilder
from services.scales import get_scale_manager
from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnStateSnapshot,
)
from conversation.agent_observation import AgentObservation
from conversation.interview_state import (
    SYMPTOM_DOMAIN_KEYWORDS,
    InterviewState,
    format_history,
)
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import collect_turn_signals

#: Coarse, deterministic symptom domains.  The table itself lives in
#: :mod:`conversation.interview_state` so that production and the offline timing
#: replay read the *same* vocabulary; this alias keeps older imports working.
_SYMPTOM_DOMAIN_KEYWORDS = SYMPTOM_DOMAIN_KEYWORDS
from conversation.input_semantics import inspect_input_semantics
from conversation.delivery import (
    DeliveryLedger,
    GenerationController,
    SentenceDeliveryQueue,
    SentenceReady,
    SentenceSegmenter,
)
from conversation.pre_delivery_guard import (
    GenerationGuardState,
    GuardContext,
    PreDeliveryGuard,
)


# Natural-language versions of scale questions for conversational delivery.
# Keys are (scale_name, question_number).  Used only for natural prompt
# phrasing after Runtime has selected the current item.
NATURAL_SCALE_QUESTIONS = {
    ("PHQ-9", 1): "这段时间，你平时会觉得一些原本还能做的事，现在也提不起劲吗？",
    ("PHQ-9", 2): "这种不好受的状态，是偶尔冒出来，还是这两周里经常在？",
    ("PHQ-9", 3): "最近睡眠怎么样？是比较难睡着、容易醒，还是反而睡得特别多？",
    ("PHQ-9", 4): "白天的精神头怎么样？会不会经常觉得没力气、撑着过一天？",
    ("PHQ-9", 5): "吃饭这块最近有变化吗？比如没胃口，或者比平时吃得多很多。",
    ("PHQ-9", 6): "你会不会有时候对自己特别不满意，或者觉得自己挺失败的？",
    ("PHQ-9", 7): "最近做事或者聊天的时候，注意力会不会比较难集中？",
    ("PHQ-9", 8): "这段时间别人有没有说你反应变慢了，或者你自己感觉坐立不安、停不下来？",
    ("PHQ-9", 9): "有时候人难受到一定程度，会冒出不想撑下去的念头。你最近有没有出现过类似想法？",
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

# Scale item core definitions — provides the clinical meaning of each item
# for the agent to pass to the main model. NOT fixed question templates.
# The main model rewrites these into natural conversation.
PHQ9_ITEM_CORE = {
    1: {
        "scale_item_text": "过去两周，对做事的兴趣或愉快感是否明显减少？",
        "scoring_target": "兴趣下降或愉快感减少的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    2: {
        "scale_item_text": "过去两周，是否经常感到心情低落、沮丧或绝望？",
        "scoring_target": "低落、沮丧、绝望感的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    3: {
        "scale_item_text": "过去两周，睡眠是否受影响，例如入睡困难、睡不安稳或睡得过多？",
        "scoring_target": "睡眠问题的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    4: {
        "scale_item_text": "过去两周，是否经常感到疲倦、没劲或精力不足？",
        "scoring_target": "疲倦、乏力、精力不足的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    5: {
        "scale_item_text": "过去两周，食欲是否有明显变化（吃不下或吃太多）？",
        "scoring_target": "食欲变化的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    6: {
        "scale_item_text": "过去两周，是否经常觉得自己很糟、失败、或让家人失望？",
        "scoring_target": "自责、失败感的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    7: {
        "scale_item_text": "过去两周，注意力是否难以集中（如阅读、看电视）？",
        "scoring_target": "注意力困难的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    8: {
        "scale_item_text": "过去两周，是否动作或说话明显变慢，或反过来烦躁坐立不安？",
        "scoring_target": "精神运动迟缓或激越的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    9: {
        "scale_item_text": "过去两周，是否出现过不想活或伤害自己的念头？",
        "scoring_target": "自杀/自伤意念的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
}

GAD7_ITEM_CORE = {
    1: {
        "scale_item_text": "过去两周，是否经常感到紧张、焦虑或急切？",
        "scoring_target": "紧张、焦虑感的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    2: {
        "scale_item_text": "过去两周，是否无法停止或控制担忧？",
        "scoring_target": "不可控担忧的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    3: {
        "scale_item_text": "过去两周，是否对各种事情担忧过多？",
        "scoring_target": "过度担忧的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    4: {
        "scale_item_text": "过去两周，是否很难放松下来？",
        "scoring_target": "放松困难的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    5: {
        "scale_item_text": "过去两周，是否因不安而无法静坐？",
        "scoring_target": "坐立不安的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    6: {
        "scale_item_text": "过去两周，是否变得容易烦恼或急躁？",
        "scoring_target": "易怒、急躁的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
    7: {
        "scale_item_text": "过去两周，是否感到似乎将有可怕的事情发生？",
        "scoring_target": "恐惧/灾难化预期的频率",
        "required_answer_axis": "没有、几天、超过一半天数、几乎每天",
    },
}

SCALE_ITEM_CORES = {
    "PHQ-9": PHQ9_ITEM_CORE,
    "GAD-7": GAD7_ITEM_CORE,
}

# Critical semantic ASR rewrites are intentionally disabled.  Frequency,
# duration, negation, quantity, and symptom polarity require observation and
# policy clarification rather than a guessed replacement.
_ASR_CORRECTIONS = {}


def correct_asr_text(raw_text: str, recent_context: str = "") -> tuple:
    """Post-process ASR output to fix common speech recognition errors.

    Returns (corrected_text, corrections_list).
    """
    if not raw_text:
        return raw_text, []

    corrected = re.sub(r"\s+", " ", str(raw_text or "")).strip()
    corrections = []
    if corrected != raw_text:
        corrections.append("whitespace_normalized")

    if corrections:
        logger.warning(
            "[ASRCorrect] "
            f"{text_meta('raw', raw_text)} {text_meta('corrected', corrected)} "
            f"fixes={corrections}"
        )

    return corrected, corrections


def get_end_type_enum(string_name: str):
    """Convert string end type name to EndType enum."""
    from core.types import EndType
    _map = {
        'goal_achieved': EndType.GOAL_ACHIEVED,
        'time_limit': EndType.TIME_LIMIT,
        'invalid': EndType.INVALID,
        'quit': EndType.QUIT,
    }
    return _map.get(string_name, EndType.GOAL_ACHIEVED)


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
    relaxation_rec: Optional[str] = None    # explicit core preference only
    intent: str = "counseling"
    emotion_result: dict = field(default_factory=dict)
    agent_route: dict = field(default_factory=dict)
    scale_tags: dict = field(default_factory=dict)
    scale_active: bool = False             # True when a scale is currently being administered
    scale_completed: bool = False
    all_scales_completed: bool = False
    completed_scale_name: Optional[str] = None
    # Phase 2 authority audit trail.  These are immutable value objects; all
    # executable control fields below are derived from ``turn_decision``.
    router_proposal: Optional[RouterProposal] = None
    turn_state_snapshot: Optional[TurnStateSnapshot] = None
    turn_decision: Optional[TurnDecision] = None
    agent_observation: Optional[AgentObservation] = None
    # Passive, monotonic stage timings.  Missing fields mean not measured or
    # not applicable for the selected input/output mode.
    timing: dict = field(default_factory=dict)


@dataclass
class PipelineConfig:
    """Controls which optional pipeline steps are enabled."""
    use_stt: bool = False       # True for voice input, False for text input
    use_tts: bool = False       # True for voice output, False for text-only
    audio_data: Optional[Any] = None  # Raw audio numpy array if use_stt=True
    user_text: str = ""         # Text input if use_stt=False
    transcribed_text: str = ""  # Coordinator-owned STT result; skips a second ASR pass
    extra_system_suffix: str = ""  # Additional system context (e.g. scale questions)
    # Precomputed contracts are accepted for adapter/tests; production
    # ConversationPipeline builds them exactly once when omitted.
    router_proposal: Optional[RouterProposal] = None
    turn_decision: Optional[TurnDecision] = None
    session_state: str = "CHATTING"
    # Phase 7: caller-supplied delivery generation.  When absent, direct
    # compatibility callers keep the legacy unscoped event surface.
    generation_id: Optional[int] = None


@dataclass
class _SentenceFlushRegistration:
    """One identity-scoped segmenter registration for the pipeline watchdog."""

    generation_id: int
    segmenter: SentenceSegmenter
    emit: Callable[[str, Any], None]
    lock: Any = field(default_factory=threading.RLock, repr=False)
    next_deadline: Optional[float] = field(default=None, repr=False)
    timing: Optional[dict] = field(default=None, repr=False)


# ==================== Unified Conversation Pipeline ====================

class ConversationPipeline:
    """
    Unified pipeline: STT(optional) -> Intent+Emotion -> RAG -> LLM stream
    -> generation-scoped sentence delivery -> TTS(optional) -> post-process.

    Merges _process_pipeline and _process_text_pipeline into a single flow.
    The caller provides an emit callback for thread-safe UI updates.
    No Qt dependency.
    """

    def __init__(self, stt_service, llm_service, tts_service,
                 rag_service, agent_service, report_service, data_manager,
                 session_emotions: list, emotion_tracker=None,
                 turn_policy: TurnPolicy | None = None,
                 session_state_provider: Callable[[], Any] | None = None,
                 delivery_controller: GenerationController | None = None):
        self.stt = stt_service
        self.llm = llm_service
        self.tts = tts_service
        self.rag = rag_service
        self.agent = agent_service
        self.report = report_service
        self.data = data_manager
        self.session_emotions = session_emotions
        self.emotion_tracker = emotion_tracker
        self.turn_policy = turn_policy or TurnPolicy()
        self._session_state_provider = session_state_provider
        self.delivery_controller = delivery_controller or GenerationController()
        self.delivery_ledger = DeliveryLedger(self.delivery_controller)
        self.delivery_queue = SentenceDeliveryQueue(
            self.delivery_controller,
            self.tts,
        )
        self._last_stream_generation_id: Optional[int] = None
        self._sentence_segmenter_factory = SentenceSegmenter
        self._flush_condition = threading.Condition()
        self._active_flush_registration: Optional[_SentenceFlushRegistration] = None
        self._flush_watchdog_thread: Optional[threading.Thread] = None
        self._flush_watchdog_shutdown = threading.Event()
        # Guard state is transient delivery bookkeeping only. It is removed
        # on cancellation/finalization and never becomes business state.
        self._pre_delivery_guard = PreDeliveryGuard()
        self._guard_contexts: dict[int, GuardContext] = {}
        self._guard_states: dict[int, GenerationGuardState] = {}
        self._guard_fallbacks: dict[int, str] = {}
        self.delivery_controller.add_cancel_listener(self._on_guard_generation_cancelled)
        # Scale administration has one mutable owner.  Pipeline keeps only
        # symptom observations used to build TurnSignals; it never mirrors
        # active item, waiting, answers, pause, or completion state.
        self.scale_runtime = ScaleRuntime()
        self._scale_manager = get_scale_manager()
        self.answer_interpreter = ScaleAnswerInterpreter()
        self.symptom_scores = {
            name: 0 for name in self._scale_manager.get_scale_names()
        }
        self.symptom_turns = 0
        # Session-scoped invitation memory: which scale/relaxation offers were
        # made, and how the participant answered.  The decision layer reads it
        # as state; TurnPolicy reads it as a gate.
        self._offer_ledger = OfferLedger()
        # Accumulated, utterance-derived interview evidence (domains, duration,
        # frequency, functional impact, denials).  Shared with the offline
        # timing replay so the two paths cannot describe the same session
        # differently -- see conversation/interview_state.py.
        self._interview_state = InterviewState()
        # Whether the previous turn left something unclarified.  Read as state
        # by the decision layer; never used to act on its own.
        self._clarification_pending: bool = False
        # Consecutive turns spent asking the participant what they meant.  The
        # policy stops clarifying once this reaches its cap, otherwise a
        # withdrawn participant is asked "你指的是什么？" for the whole session.
        self._clarifications_in_a_row: int = 0
        # Turns elapsed since each (scale, item) started waiting.  Guards
        # against a stale item absorbing an unrelated "没有" long after the
        # conversation left it.
        self._scale_wait_turns: dict[tuple[str, int], int] = {}
        # Consecutive turns whose answer could not be booked against the open
        # item.  Two of them are enough for the scale to deadlock: the item
        # never advances, the same question is asked again, and the report
        # ends up with three answers out of nine.
        self._scale_unscored_turns: dict[tuple[str, int], int] = {}
        # Dimension -> how many turns have asked about it.  The pipeline keeps
        # this because the decision layer cannot know it: without a record the
        # focus question keeps choosing the topic that is easiest to see in the
        # transcript, and the interview circles it.
        self._focus_counts: dict[str, int] = {}
        # A safety disclosure holds for the rest of the session.  ``risk_marker``
        # is re-detected from each utterance, so a calm confirmation turn would
        # otherwise look like an ordinary moment and an invitation could land in
        # the middle of a risk conversation.
        self._risk_disclosed: bool = False
        # Read-only policy fact surfaced in TurnStateSnapshot.  It is distinct
        # from ReportService.completed_relaxation: an offer can be rejected or
        # media can fail after the proactive allowance has been consumed.
        self._proactive_relaxation_offered: bool = False
        # A completed scale may provide a typed candidate for a later policy
        # turn. It is an observation, not an approved intervention.
        self._proactive_relaxation_candidate: Optional[str] = None
        # The content actually being recommended right now ("breathing" /
        # "muscle" / "meditation").  The policy only carries an explicit
        # *participant* preference, so without this a proactive invitation
        # reaches the UI as ``None`` and the Center opens with nothing
        # highlighted.
        self._relaxation_recommendation: Optional[str] = None
        self._agent_route_cooldown: int = 0         # cooldown after agent route failure
        # Last decision-layer outcome, kept so the language layer can reflect
        # the decision model's own read of the turn (intent/emotion/confidence)
        # instead of discarding everything below the action.
        self._last_decision_outcome: Optional[Any] = None
        # Consecutive ultra-short replies ("哦哦", "好的") mean the participant
        # has run out of things to say about the current topic; tracked here
        # so the writer can be told to move on instead of asking again.
        self._minimal_reply_streak: int = 0
        # Questions already asked in recent turns.  Injected as an explicit
        # do-not-repeat list: the model otherwise copies its own previous
        # question when the scale hint pushes the same interview item again.
        self._recent_questions: list[str] = []
        # How many clarification turns an unanswered scale item has consumed.
        # Without a budget the same item is re-asked indefinitely.
        self._clarify_attempts: dict[tuple[str, int], int] = {}
        # Per-session participation counters for the decision layer.
        self._jev_stats: dict[str, int] = {
            "turns": 0, "accepted": 0, "abstained": 0, "fallback": 0,
        }

        # Shared executor for parallel intent / emotion classification.
        # Created once and reused across pipeline executions to avoid
        # spawning fresh worker threads on every user turn.
        self._executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="pipe-classify"
        )

    def shutdown(self):
        """Release shared workers, including the sentence flush watchdog."""
        try:
            self._shutdown_sentence_flush_watchdog()
        except Exception as e:
            logger.debug(f"sentence flush watchdog shutdown error: {e}")
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            logger.debug(f"executor shutdown error: {e}")
        try:
            self.delivery_queue.shutdown()
        except Exception as e:
            logger.debug(f"delivery queue shutdown error: {e}")
        self._guard_contexts.clear()
        self._guard_states.clear()
        self._guard_fallbacks.clear()

    def reset_session(self):
        """Reset per-session state without touching unrelated services."""
        stats = getattr(self, "_jev_stats", None)
        if stats and stats.get("turns"):
            logger.info(
                "[DecisionParticipation] turns=%s jev_accepted=%s jev_abstained=%s "
                "route_fallback=%s",
                stats["turns"], stats["accepted"], stats["abstained"], stats["fallback"],
            )
        self._jev_stats = {"turns": 0, "accepted": 0, "abstained": 0, "fallback": 0}
        self.scale_runtime.reset()
        self.symptom_scores = {
            name: 0 for name in self._scale_manager.get_scale_names()
        }
        self.symptom_turns = 0
        self._agent_route_cooldown = 0
        self._last_decision_outcome = None
        self._minimal_reply_streak = 0
        self._recent_questions = []
        self._clarify_attempts = {}
        self._scale_unscored_turns = {}
        self._scale_paused_turns = 0
        self._focus_counts = {}
        # Each session starts with an empty "already told them this" set, so a
        # second session in the same process can use the same entries again.
        reset_rag = getattr(self.rag, "reset_session", None)
        if callable(reset_rag):
            reset_rag()
        self._proactive_relaxation_offered = False
        self._proactive_relaxation_candidate = None
        self._relaxation_recommendation = None
        self._offer_ledger.reset()
        self._interview_state.reset()
        self._risk_disclosed = False
        self._clarification_pending = False
        self._clarifications_in_a_row = 0
        self._guard_contexts.clear()
        self._guard_states.clear()
        self._guard_fallbacks.clear()

    def _on_guard_generation_cancelled(self, event: Any) -> None:
        generation_id = getattr(event, "generation_id", None)
        if generation_id is not None:
            self._guard_contexts.pop(generation_id, None)
            self._guard_states.pop(generation_id, None)
            self._guard_fallbacks.pop(generation_id, None)

    def _build_pre_delivery_fallback(
        self,
        decision: TurnDecision,
        runtime: Any,
        user_text: str,
        semantic_target: str | None,
    ) -> str:
        """Build one deterministic, model-free fallback for first-sentence blocks."""
        if decision.action in (TurnAction.START_SCALE, TurnAction.CONTINUE_SCALE) and getattr(runtime, "active_scale", None):
            return self._make_scale_clarify_reply(
                runtime.active_scale,
                getattr(runtime, "current_item", None) or 1,
                user_text,
            )
        if decision.action is TurnAction.CLARIFY_INPUT:
            if semantic_target == "night_waking":
                return "我确认一下，你刚才说的是晚上中途不会醒，还是会醒？"
            if semantic_target == "frequency":
                return "我确认一下，你说的这个情况发生频率是怎样的？"
            return "我确认一下，你刚才这句话具体是指什么情况？"
        if decision.action is TurnAction.CONTINUE_SCALE and not getattr(runtime, "active_scale", None):
            return "这部分我们先记下来了。你可以继续说。"
        return "嗯，我在听着呢。[breath]你可以慢慢说。"

    def _set_guard_context(
        self,
        generation_id: Optional[int],
        decision: TurnDecision,
        runtime: Any,
        *,
        user_text: str = "",
        semantic_target: str | None = None,
    ) -> None:
        if generation_id is None:
            return
        self._guard_contexts[generation_id] = GuardContext(
            generation_id=generation_id,
            turn_action=decision.action,
            active_scale=getattr(runtime, "active_scale", None),
            current_scale_item=getattr(runtime, "current_item", None),
            semantic_target=semantic_target,
        )
        self._guard_states.setdefault(generation_id, GenerationGuardState())
        self._guard_fallbacks[generation_id] = self._build_pre_delivery_fallback(
            decision,
            runtime,
            user_text,
            semantic_target,
        )

    def _ensure_guard_state(self) -> None:
        """Lazily support lightweight pipeline doubles used by old adapters."""
        if not hasattr(self, "_pre_delivery_guard"):
            self._pre_delivery_guard = PreDeliveryGuard()
        if not hasattr(self, "_guard_contexts"):
            self._guard_contexts = {}
        if not hasattr(self, "_guard_states"):
            self._guard_states = {}
        if not hasattr(self, "_guard_fallbacks"):
            self._guard_fallbacks = {}

    def _session_state_name(self, configured: str = "CHATTING") -> str:
        """Return the current lifecycle state without owning that state."""
        try:
            value = self._session_state_provider() if self._session_state_provider else configured
            return getattr(value, "name", str(value).split(".")[-1]).upper()
        except Exception:
            return str(configured or "CHATTING").split(".")[-1].upper()

    def _completed_scale_names(self) -> tuple[str, ...]:
        """Read explicit completion from the Runtime snapshot."""
        return tuple(self.scale_runtime.snapshot().completed_scales)

    # -- invitation state helpers -----------------------------------------

    def _registered_scale_names(self) -> tuple[str, ...]:
        """Every scale the runtime can actually start, in registry order."""
        try:
            return tuple(self._scale_manager.get_scale_names())
        except Exception:
            return ()

    def _offer_state(self) -> OfferState:
        """Current invitation state, tolerating a partially built host.

        Adapters and tests sometimes construct the pipeline with ``__new__``
        and only the attributes they need; a missing ledger must read as "no
        invitations yet", never as an exception in the middle of a turn.
        """
        ledger = getattr(self, "_offer_ledger", None)
        if ledger is None:
            return OfferState()
        return ledger.state()

    def _scale_candidate_names(self) -> tuple[str, ...]:
        """Scales that may still be offered right now, best-evidenced first.

        Derived from the registry plus the runtime snapshot, so a candidate can
        never be a name the runtime would refuse to start.
        """
        return tuple(candidate.name for candidate in self._ranked_scale_candidates())

    def _scale_candidate_evidence(self, symptom_scores=None) -> dict[str, int]:
        """Evidence score behind the current candidate order."""
        return {
            candidate.name: candidate.evidence
            for candidate in self._ranked_scale_candidates(symptom_scores=symptom_scores)
        }

    def _ranked_scale_candidates(self, symptom_scores=None):
        """One ranking, shared by the name tuple and the evidence map.

        Both projections must come from the same ranking: if the names were
        ordered by evidence while the evidence map was built separately, the
        two could disagree and the policy would read a score that belongs to a
        different instrument.

        ``symptom_scores`` overrides the stored counters for the *uncommitted*
        turn.  The turn-time snapshot is built before this turn's symptom
        deltas are committed, and without the override the candidate order
        would lag one utterance behind the participant.
        """
        runtime = self.scale_runtime.snapshot()
        scores = (
            self.symptom_scores if symptom_scores is None else symptom_scores
        )
        return rank_scale_candidates(
            registered=self._registered_scale_names(),
            completed=getattr(runtime, "completed_scales", ()) or (),
            administered=getattr(runtime, "administered_scales", ()) or (),
            declined=self._offer_state().declined_scales(),
            symptom_scores=scores,
        )

    def _symptom_summary(self) -> str:
        """Deterministic description of the evidence seen so far.

        Only two things change here relative to the RC1-era version: the
        domains accumulate through the shared :class:`InterviewState`, and the
        summary now also carries duration, frequency, functional impact,
        explicit denials and contradictions.  A model deciding *when* to offer
        an assessment needs the denials at least as much as the complaints:
        without them it re-offers a questionnaire the participant just ruled
        out, and it reads a single anxious turn as a longitudinal picture.
        """
        state = getattr(self, "_interview_state", None)
        if state is None:
            return ""
        return state.symptom_summary()

    def _record_interview_turn(self, text: str, round_count: int = 0) -> None:
        """Accumulate one participant utterance into the shared state.

        Called *after* the decision, exactly like ``_update_symptom_domains``
        was: the current utterance is already handed to the model verbatim as
        ``user_text``, and folding it into the summary as well would make the
        same sentence appear twice while biasing the summary toward the present
        turn.  ``symptom_turns`` is counted by the scoring block below, so the
        shared state is not asked to count it again.
        """
        state = getattr(self, "_interview_state", None)
        if state is None:
            return
        try:
            active = bool(getattr(self.scale_runtime.snapshot(), "active_scale", None))
        except Exception:
            active = False
        state.record_user(text, round_count=int(round_count), assessment_active=active)

    def _begin_offer_round(self, round_count: int, user_text: str = "") -> None:
        """Retire invitations only when the conversation has really moved on.

        There is no answer timeout: participants routinely reply two or three
        turns later, and closing the invitation on a turn count alone would
        discard a consent that is still coming.  The ledger needs consecutive
        turns that neither answer nor even mention the invitation.
        """
        ledger = getattr(self, "_offer_ledger", None)
        if ledger is None:
            return
        pending = ledger.pending()
        if pending is None:
            return
        engaged = mentions_offer(user_text) or detect_offer_question(user_text)
        expired = ledger.expire_on_topic_drift(round_count, engaged=engaged)
        for record in expired:
            logger.warning(
                "[Offer] expired kind=%s target=%s made_round=%s last_touched=%s reason=topic_drift",
                record.kind.value,
                record.target,
                record.made_at_round,
                record.last_touched_round,
            )

    def _cancel_open_offers(self, round_count: int, reason: str) -> None:
        """Close every open invitation with an explicit cause."""
        ledger = getattr(self, "_offer_ledger", None)
        if ledger is None:
            return
        for record in ledger.cancel_all(round_count, reason):
            logger.warning(
                "[Offer] cancelled kind=%s target=%s reason=%s round=%s",
                record.kind.value,
                record.target,
                reason,
                round_count,
            )

    def _apply_offer_outcome(self, decision: TurnDecision, round_count: int) -> None:
        """Close an open invitation when the turn answered it.

        Consent bookkeeping lives here rather than in the policy because the
        policy is pure; the decision already carries the stable reason.
        """
        ledger = getattr(self, "_offer_ledger", None)
        if ledger is None:
            return

        # A participant who asks for an intervention has answered any related
        # invitation by acting on it.  That is a *new* request, but the open
        # record must not stay dangling while the module starts.
        if decision.reason == "user_scale_request":
            pending = ledger.pending()
            if pending is not None and pending.kind is OfferKind.SCALE:
                ledger.resolve_pending(OfferOutcome.ACCEPTED, round_count, note="explicit_request")
            elif pending is not None:
                ledger.resolve_pending(
                    OfferOutcome.CANCELLED, round_count, note="superseded_by_explicit_scale_request"
                )
            return
        if decision.reason == "user_relaxation_request":
            pending = ledger.pending()
            if pending is not None and pending.kind is OfferKind.RELAXATION:
                ledger.resolve_pending(OfferOutcome.ACCEPTED, round_count, note="explicit_request")
            elif pending is not None:
                ledger.resolve_pending(
                    OfferOutcome.CANCELLED,
                    round_count,
                    note="superseded_by_explicit_relaxation_request",
                )
            return

        outcome = OUTCOME_BY_REASON.get(decision.reason or "")
        if outcome is None:
            return
        if outcome is OfferOutcome.CLARIFYING:
            record = ledger.mark_clarifying(round_count)
        elif outcome is OfferOutcome.PENDING:
            record = ledger.touch_pending(round_count)
        else:
            record = ledger.resolve_pending(outcome, round_count)
        if record is not None:
            logger.warning(
                "[Offer] resolved kind=%s target=%s status=%s round=%s",
                record.kind.value,
                record.target,
                record.status.value,
                round_count,
            )

    def _build_turn_snapshot(
        self,
        *,
        round_count: int,
        time_limit_reached: bool,
        session_state: str = "CHATTING",
        risk_marker: bool = False,
        symptom_scores=None,
    ) -> TurnStateSnapshot:
        runtime = self.scale_runtime.snapshot()
        offer_fields = offer_snapshot_fields(self._offer_state())
        offer_fields["scale_candidates"] = tuple(
            candidate.name
            for candidate in self._ranked_scale_candidates(symptom_scores=symptom_scores)
        )
        offer_fields["scale_candidate_evidence"] = self._scale_candidate_evidence(
            symptom_scores=symptom_scores
        )
        return TurnStateSnapshot(
            session_state=self._session_state_name(session_state),
            round_count=max(0, int(round_count)),
            active_scale=runtime.active_scale,
            current_item=runtime.current_item,
            waiting_for_answer=runtime.waiting_for_answer,
            scale_paused=bool(runtime.paused),
            completed_scales=self._completed_scale_names(),
            relaxation_used=bool(getattr(self.report, "completed_relaxation", None)),
            proactive_relaxation_offered=self._proactive_relaxation_offered,
            clarifications_in_a_row=self._clarifications_in_a_row,
            game_active=False,
            time_limit_reached=bool(time_limit_reached),
            risk_marker=bool(risk_marker),
            risk_in_session=bool(getattr(self, "_risk_disclosed", False)),
            **offer_fields,
        )

    def _agent_deterministic_fallback(self, user_text: str) -> AgentObservation:
        """Build local observations without invoking a secondary Agent call."""
        stats = getattr(self, "_jev_stats", None)
        if stats is not None:
            stats["fallback"] += 1
        intent = "counseling"
        emotion = "neutral"
        intensity = 0.0
        if self.agent:
            keyword_intent = getattr(self.agent, "_keyword_classify", None)
            keyword_emotion = getattr(self.agent, "_keyword_detect_emotion", None)
            if callable(keyword_intent):
                try:
                    intent = str(keyword_intent(user_text).get("intent", intent))
                except Exception:
                    pass
            if callable(keyword_emotion):
                try:
                    observed = keyword_emotion(user_text)
                    emotion = str(observed.get("emotion", emotion))
                    intensity = float(observed.get("intensity", intensity) or intensity)
                except Exception:
                    pass
        proposal = RouterProposal(
            action=RouterAction.CHAT,
            emotion=emotion,
            intensity=max(0.0, min(1.0, intensity)),
            confidence=0.0,
            reason="agent_observation_fallback",
            needs_rag=False,
        )
        return AgentObservation(
            proposal=proposal,
            intent=intent,
            fallback_used=True,
            source="deterministic_fallback",
        )

    def _apply_agent_observation(self, observation: AgentObservation, result: PipelineResult) -> None:
        """Project one Agent observation to existing reporting consumers only."""
        result.intent = observation.intent
        result.emotion_result = {
            "emotion": observation.proposal.emotion,
            "intensity": observation.proposal.intensity,
        }
        self.session_emotions.append({"role": "user", **result.emotion_result})

    def _build_decision_request(self, user_text: str, current_rounds: int):
        """Snapshot the turn for the decision layer without exposing runtime objects.

        Field assembly lives in :func:`decision.request_builder.build_decision_request`
        so that this path and the offline timing replay cannot disagree about
        what "the state of the interview" means.  This method only gathers; it
        makes no decisions about content.
        """
        from decision.request_builder import build_decision_request

        try:
            snapshot = self.scale_runtime.snapshot()
        except Exception:
            snapshot = None

        def _safe(call, default):
            try:
                return call()
            except Exception:
                return default

        return build_decision_request(
            user_text=user_text,
            round_count=current_rounds,
            interview_state=getattr(self, "_interview_state", None),
            symptom_turns=int(getattr(self, "symptom_turns", 0) or 0),
            runtime=snapshot,
            offer_state=self._offer_state(),
            registered_scales=tuple(_safe(self._registered_scale_names, ()) or ()),
            session_state=str(_safe(self._session_state_name, "CHATTING") or "CHATTING"),
            clarification_pending=bool(getattr(self, "_clarification_pending", False)),
            relaxation_done=bool(_safe(self._get_relaxation_done, False)),
            relaxation_active=bool(
                getattr(snapshot, "active_scale", None) is None
                and getattr(getattr(self, "relaxation", None), "active", False)
            ),
            history=_safe(self._get_recent_dialogue_text, "") or "",
            history_mode="structured_history",
            risk_marker=detect_risk_marker(user_text),
            wants_to_talk=detect_wants_to_talk(user_text),
            focus_coverage=dict(getattr(self, "_focus_counts", {}) or {}),
            symptom_scores=dict(getattr(self, "symptom_scores", {}) or {}),
        )

    def _decision_coordinator(self):
        """Return the cached decision coordinator for the current agent."""
        cached = getattr(self, "_decision_coordinator_cache", None)
        if cached is not None and cached[0] is self.agent:
            return cached[1]

        from decision.registry import build_decision_coordinator

        try:
            from deployment.runtime_config import get_runtime_config

            decision_config = get_runtime_config().decision
        except Exception:
            # A broken/absent config must never block observation: fall back to
            # the RC1 path, which is what DecisionConfig() means.
            from deployment.runtime_config import DecisionConfig

            decision_config = DecisionConfig()

        coordinator = build_decision_coordinator(
            decision_config=decision_config,
            agent=self.agent,
        )
        self._decision_coordinator_cache = (self.agent, coordinator)
        return coordinator

    def _request_agent_observation(
        self,
        *,
        user_text: str,
        current_rounds: int,
    ) -> AgentObservation:
        """Obtain one non-executable observation from the configured Agent.

        The decision layer (``decision/``) owns *which* provider runs.  It never
        owns the outcome: whatever comes back is still a non-executable
        ``AgentObservation`` that ``TurnPolicy`` re-judges.
        """
        if not AGENT_ROUTE_ENABLED:
            logger.warning("[AgentRoute] skipped: AGENT_ROUTE_ENABLED=False")
            self._last_decision_outcome = None
            return self._agent_deterministic_fallback(user_text)
        if self._agent_route_cooldown > 0:
            self._agent_route_cooldown -= 1
            logger.warning(f"[AgentRoute] cooldown remaining={self._agent_route_cooldown}")
            self._last_decision_outcome = None
            return self._agent_deterministic_fallback(user_text)

        # The Jev/shadow decision layers own an independent primary backend
        # (e.g. Tacit-1.7B inprocess) and never consult the legacy agent, so
        # the agent endpoint being unreachable must not silence the decision
        # layer: with mode != legacy the coordinator runs without the agent
        # and its own escalation_mode handles failure.  (Previously this gate
        # skipped Jev/Tacit on every turn while the runtime contract said the
        # agent server is intentionally not started.)
        agent_needed = True
        try:
            from decision.contracts import DecisionMode

            agent_needed = (
                self._decision_coordinator().mode is DecisionMode.LEGACY
            )
        except Exception:
            agent_needed = True
        if agent_needed and (not self.agent or not self.agent.is_available()):
            self._last_decision_outcome = None
            return self._agent_deterministic_fallback(user_text)

        observation = self._observe_via_coordinator(user_text, current_rounds)
        if observation is not None:
            return observation
        self._last_decision_outcome = None
        return self._agent_deterministic_fallback(user_text)

    def _observe_via_coordinator(self, user_text: str, current_rounds: int):
        """Run one coordinator turn; returns None on failure (caller falls back)."""
        try:
            request = self._build_decision_request(user_text, current_rounds)
            outcome = self._decision_coordinator().observe_turn(request)
            observation = outcome.observation
            self._agent_route_cooldown = 0
            self._last_decision_outcome = outcome
            stats = getattr(self, "_jev_stats", None)
            if stats is not None:
                stats["turns"] += 1
                if getattr(outcome, "escalated", False):
                    stats["abstained"] += 1
                else:
                    stats["accepted"] += 1
            logger.warning(
                f"[AgentRoute] action={observation.proposal.action.value} "
                f"scale={observation.proposal.scale_name} confidence={observation.proposal.confidence} "
                f"intent={observation.intent!r} source={observation.source!r} "
                f"mode={outcome.mode.value} provider={outcome.provider} "
                f"escalated={outcome.escalated}"
            )
            return observation
        except Exception as exc:
            logger.warning(f"[AgentRoute] failed: {exc}")
            self._agent_route_cooldown = AGENT_ROUTE_COOLDOWN_ROUNDS
            self._last_decision_outcome = None
            return None

    def _request_router_proposal(
        self,
        *,
        user_text: str,
        current_rounds: int,
    ) -> tuple[RouterProposal, dict]:
        """Compatibility adapter for callers that only need the proposal."""
        observation = self._request_agent_observation(
            user_text=user_text,
            current_rounds=current_rounds,
        )
        return observation.proposal, observation.proposal.model_dump(mode="json")

    def _apply_turn_decision(
        self, decision: TurnDecision, result: PipelineResult, round_count: int = 0
    ) -> None:
        """Apply an already-authoritative decision; never choose another action."""
        ledger = getattr(self, "_offer_ledger", None)
        session_state = (
            self._session_state_name() if hasattr(self, "_session_state_name") else None
        )
        if decision.action is TurnAction.OFFER_SCALE:
            # An invitation changes no assessment state: it only asks.
            if ledger is not None:
                ledger.record_offer(
                    OfferKind.SCALE,
                    target=decision.scale_name,
                    round_count=round_count,
                    session_state=session_state,
                )
            logger.warning(
                "[Offer] scale invitation issued target=%s round=%s",
                decision.scale_name,
                round_count,
            )
            return
        if decision.action is TurnAction.OFFER_RELAXATION:
            if ledger is not None:
                ledger.record_offer(
                    OfferKind.RELAXATION,
                    target=None,
                    round_count=round_count,
                    session_state=session_state,
                )
            logger.warning("[Offer] relaxation invitation issued round=%s", round_count)
            return
        if decision.action is TurnAction.START_SCALE:
            update = self.scale_runtime.start(decision.scale_name or "")
            if not update.accepted:
                logger.warning(
                    f"[ScaleDebug] Runtime rejected start {decision.scale_name!r}: "
                    f"{update.reason or update.status}"
                )
            self.symptom_scores = {
                name: 0 for name in self._scale_manager.get_scale_names()
            }
            self.symptom_turns = 0
        elif decision.action is TurnAction.CONTINUE_SCALE:
            runtime = self.scale_runtime.snapshot()
            if runtime.active_scale and runtime.paused:
                self.scale_runtime.resume()
            elif runtime.active_scale:
                self.scale_runtime.present_current_item()
            self._scale_paused_turns = 0
        elif decision.action is TurnAction.PAUSE_SCALE:
            if self.scale_runtime.snapshot().active_scale:
                self.scale_runtime.pause()
            self._scale_paused_turns = 0
        elif decision.action is TurnAction.RECOMMEND_RELAXATION:
            self._proactive_relaxation_candidate = None
            self._relaxation_recommendation = (
                _normalize_relaxation_type(decision.intervention_type)
                or self._relaxation_recommendation
            )
            if decision.reason == "proactive_relaxation_accepted":
                self._proactive_relaxation_offered = True
                # The invitation has to survive into the next turn, otherwise
                # the participant's "好" resolves against nothing and the
                # Center never opens: the writer promises an exercise and the
                # screen stays where it was.  Recording it as an open offer is
                # also what makes the refusal, the "what is it?", and the
                # deferred answers behave correctly.
                if ledger is not None:
                    ledger.record_offer(
                        OfferKind.RELAXATION,
                        target=self._relaxation_recommendation,
                        round_count=round_count,
                        session_state=session_state,
                    )
            # The invitation does not own entry.  ScaleRuntime is paused by
            # the unified Center-entry helper only after the participant
            # actually chooses to enter the Center.
        elif decision.action is TurnAction.END_SESSION:
            result.end_type = {
                "time_limit": "time_limit",
                "user_explicit": "quit",
                "router_proposal": "quit",
            }.get(decision.end_reason or "", "quit")

    def _last_assistant_reply(self) -> str:
        """The reply we spoke last turn, used as evidence of what was asked.

        Scale answering needs this: a bare "没有" has no topic of its own, so
        whether it belongs to the active item can only be decided by looking
        at the question it answers.
        """
        history = getattr(self.llm, "conversation_history", None) if self.llm else None
        if not history:
            return ""
        for message in reversed(history):
            if str(message.get("role", "")) == "assistant":
                return str(message.get("content", "") or "")
        return ""

    def _reply_asks_item(self, reply_text: str, scale_name: str, item: int) -> bool:
        """True when the reply we just spoke names this item's symptom.

        The only in-band evidence that an old waiting item is active again.
        """
        if not reply_text:
            return False
        if scale_name == "PHQ-9":
            keywords = PHQ_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        elif scale_name == "GAD-7":
            keywords = GAD7_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        else:
            return False
        return any(keyword in reply_text for keyword in keywords)

    def _apply_scale_answer_transition(self, user_text: str, decision: TurnDecision, result: PipelineResult) -> None:
        """Commit an interpreted scale answer before building language context.

        The Runtime transition is a business commit owned by ScaleRuntime. It
        is intentionally independent from the later LLM/TTS generation, so a
        cancelled response cannot roll back an already-confirmed answer.
        """
        result.scale_tags = {}
        explicit_relaxation_pause = (
            decision.action is TurnAction.RECOMMEND_RELAXATION
            and decision.reason == "user_relaxation_request"
        )
        if decision.action is not TurnAction.CONTINUE_SCALE and not explicit_relaxation_pause:
            return
        runtime = self.scale_runtime.snapshot()
        if not runtime.active_scale or not runtime.current_item:
            return
        scale_name = runtime.active_scale
        current_item = runtime.current_item
        question_text = self._last_assistant_reply()
        # An item that has been "waiting" for turn after turn is not actually
        # being asked any more -- the conversation moved on while the runtime
        # kept the slot open.  Whatever the participant says next then belongs
        # to the new topic, and booking it against the stale item invents a
        # score.  Only a reply that visibly returns to the item can reopen it.
        waited = self._scale_wait_turns.get((scale_name, current_item), 0)
        if waited > self.SCALE_MAX_WAIT_TURNS and not self._reply_asks_item(
            question_text, scale_name, current_item
        ):
            # A stale slot must not absorb an unrelated denial -- but an answer
            # that states a frequency on its own is still the participant's
            # answer to the frequency question they were just asked.  Trying the
            # bounded rescue here matters: the guard returning early meant the
            # rescue below was never reached, so the guard and the deadlock fed
            # each other (no score -> no advance -> same question again).
            if self._rescue_unscored_item(scale_name, current_item, user_text, result):
                return
            logger.warning(
                "[ScaleGuard] ignoring answer for %s Q%s after %d waiting turns "
                "(reply no longer asks it): user=%r",
                scale_name, current_item, waited, user_text,
            )
            return
        interpretation = self.answer_interpreter.interpret(
            user_text,
            scale_name=scale_name,
            item=current_item,
            question_text=question_text,
        )
        if interpretation.status == "accepted" and interpretation.score is not None:
            update = self.scale_runtime.accept_answer(
                scale_name=scale_name,
                item=current_item,
                score=interpretation.score,
            )
            if update.accepted:
                self._scale_wait_turns.pop((scale_name, current_item), None)
                self._clarification_pending = False
                self._clarify_attempts.pop((scale_name, current_item), None)
                self._scale_unscored_turns.pop((scale_name, current_item), None)
                result.scale_tags = {scale_name: {current_item: interpretation.score}}
                if update.completed:
                    result.scale_completed = True
                    result.completed_scale_name = scale_name
                    result.all_scales_completed = bool(
                        set(update.snapshot.completed_scales)
                        >= set(update.snapshot.administered_scales)
                    )
                    rec_type = (
                        self._proactive_relaxation_candidate
                        or self._choose_post_scale_relaxation(scale_name)
                    )
                    if rec_type:
                        self._queue_relaxation_candidate(rec_type)
                return
        if self._rescue_unscored_item(scale_name, current_item, user_text, result):
            return
        if interpretation.status == "ambiguous" or interpretation.status in {
            "pause", "refusal", "unmatched"
        }:
            key = (scale_name, current_item)
            attempts = self._clarify_attempts.get(key, 0) + 1
            self._clarify_attempts[key] = attempts
            # Two failed clarifications are enough: keep asking the same item
            # and the participant hears one question forever.
            self._clarification_pending = attempts < 2
            if interpretation.status == "ambiguous":
                self.scale_runtime.request_clarification()
            elif not self.scale_runtime.snapshot().paused:
                self.scale_runtime.request_clarification()

    #: A frequency word is the participant's own statement of how often; it is
    #: the only thing a rescue may score from.
    _FREQUENCY_MARKERS = (
        "好几天", "几天", "偶尔", "很少", "难得", "有时",
        "一半以上", "大多数", "超过一半", "经常", "时常",
        "几乎每天", "每天", "天天", "基本每天", "一直", "总是", "老是",
    )

    def _rescue_unscored_item(
        self, scale_name: str, item: int, user_text: str, result
    ) -> bool:
        """Book a frequency answer that the interpreter refused on a technicality.

        The writer phrases each item in its own words and sometimes lags one
        slot behind: by the time the participant answers "有几天", the Runtime
        has already moved to the next item, so the interpreter cannot tie the
        answer to the open one and refuses it.  Nothing is recorded, the same
        question comes back, and after a couple of those turns the scale is
        dead -- the session ends with three answers out of nine and no score.

        After two unscored turns, an answer that states a frequency *itself* is
        booked against the open item and logged.  A bare "没有" is never
        rescued: it carries no topic, and scoring it here is exactly the silent
        deflation the topical check exists to prevent.
        """
        key = (scale_name, item)
        failures = self._scale_unscored_turns.get(key, 0) + 1
        self._scale_unscored_turns[key] = failures
        if failures < self.SCALE_RESCUE_AFTER_UNSCORED:
            return False
        answer = (user_text or "").strip()
        if not any(marker in answer for marker in self._FREQUENCY_MARKERS):
            return False

        from core.scoring import infer_scale_score_from_text

        score = infer_scale_score_from_text(answer, scale_name, item=None, question_text="")
        if score is None:
            return False
        update = self.scale_runtime.accept_answer(
            scale_name=scale_name, item=item, score=score
        )
        if not update.accepted:
            return False

        logger.warning(
            "[ScaleRescue] %s Q%s booked from a bare frequency answer after %d "
            "unscored turns (score=%s, user=%r)",
            scale_name, item, failures, score, answer,
        )
        self._scale_wait_turns.pop(key, None)
        self._clarify_attempts.pop(key, None)
        self._scale_unscored_turns.pop(key, None)
        self._clarification_pending = False
        result.scale_tags = {scale_name: {item: score}}
        if update.completed:
            result.scale_completed = True
            result.completed_scale_name = scale_name
            result.all_scales_completed = bool(
                set(update.snapshot.completed_scales)
                >= set(update.snapshot.administered_scales)
            )
            rec_type = (
                self._proactive_relaxation_candidate
                or self._choose_post_scale_relaxation(scale_name)
            )
            if rec_type:
                self._queue_relaxation_candidate(rec_type)
        return True

    def _decision_end_type(self, decision: TurnDecision | None) -> Optional[str]:
        if decision is None or decision.action is not TurnAction.END_SESSION:
            return None
        return {
            "time_limit": "time_limit",
            "user_explicit": "quit",
            "router_proposal": "quit",
        }.get(decision.end_reason or "", "quit")

    def get_active_scale_state(self) -> Optional[Dict[str, Any]]:
        """Return current active scale state for relaxation-interruption tracking."""
        runtime = self.scale_runtime.snapshot()
        if runtime.active_scale and runtime.current_item:
            return {
                "scale_name": runtime.active_scale,
                "item": runtime.current_item,
                "incomplete": True,
            }
        return None

    def get_active_scale_question_text(self) -> Optional[str]:
        """Get the natural question text for the current active scale item."""
        runtime = self.scale_runtime.snapshot()
        if not runtime.active_scale or not runtime.current_item:
            return None
        scale_def = self._scale_manager.get_scale_definition(runtime.active_scale)
        if not scale_def:
            return None
        q_text = scale_def.questions[runtime.current_item - 1]
        natural = NATURAL_SCALE_QUESTIONS.get(
            (runtime.active_scale, runtime.current_item), q_text
        )
        return natural

    def restore_active_scale(self, scale_name: str, item: int) -> Optional[str]:
        """Restore active scale after relaxation interruption.

        Returns the natural question phrasing for the resumed item, or None
        if scale is already complete.
        """
        del item  # Runtime derives the actual unanswered item from its answers.
        runtime = self.scale_runtime.snapshot()
        if runtime.active_scale and runtime.active_scale != scale_name:
            logger.warning(
                f"[ScaleDebug] restore rejected while {runtime.active_scale} is active"
            )
            return None
        if runtime.active_scale is None:
            update = self.scale_runtime.start(scale_name)
        elif runtime.paused:
            update = self.scale_runtime.resume()
        else:
            update = self.scale_runtime.present_current_item()
        if update.status == "rejected":
            logger.warning(
                f"[ScaleDebug] restore rejected: {scale_name} "
                f"{update.reason or update.status}"
            )
            return None
        return self.get_active_scale_question_text()

    def resume_scale_after_relaxation(self) -> Optional[str]:
        """Resume a paused Runtime scale and return its actual next question.

        This adapter is intentionally narrow: it resumes only when the
        Runtime itself reports a paused active scale.  It never accepts a UI
        item hint or infers a new scale, so the Runtime remains the sole
        questionnaire state owner.
        """
        runtime = self.scale_runtime.snapshot()
        if not runtime.active_scale or not runtime.paused:
            return None
        update = self.scale_runtime.resume()
        if update.status == "rejected":
            logger.warning(
                f"[ScaleDebug] post-relaxation resume rejected: {update.reason or update.status}"
            )
            return None
        return self.get_active_scale_question_text()

    def force_resume_incomplete_scale(self) -> Optional[Dict[str, Any]]:
        """Force-resume the first incomplete scale for forced completion mode.

        Returns {scale_name, item} or None if no incomplete scales.
        """
        incomplete = self.get_incomplete_scales()
        if not incomplete:
            return None
        first = incomplete[0]
        scale_name = first["scale_name"]
        remaining = first.get("remaining_nums", [])
        if not remaining:
            return None
        runtime = self.scale_runtime.snapshot()
        if runtime.active_scale is None:
            update = self.scale_runtime.start(scale_name)
        elif runtime.active_scale == scale_name and runtime.paused:
            update = self.scale_runtime.resume()
        elif runtime.active_scale == scale_name:
            update = self.scale_runtime.present_current_item()
        else:
            return None
        if update.status == "rejected":
            return None
        logger.warning(
            f"[ScaleDebug] force resume: {scale_name} Q{remaining[0]}, "
            f"answered={first['answered']}/{first['total']}"
        )
        current = self.scale_runtime.snapshot().current_item
        return {"scale_name": scale_name, "item": current or remaining[0]}

    def get_incomplete_scales(self) -> List[Dict[str, Any]]:
        """Return scales with unanswered questions.

        Checks administered_scales ∪ scale_answers.keys() ∪ active_scale
        to ensure active scale even without scores is included.

        Each entry: {scale_name, total, answered, remaining_questions,
                     remaining_nums}.
        """
        return [
            {
                "scale_name": item.scale_name,
                "total": item.total,
                "answered": item.answered,
                "remaining_questions": list(item.remaining_questions),
                "remaining_nums": list(item.remaining_nums),
            }
            for item in self.scale_runtime.get_incomplete_scales()
        ]

    def _record_focus(self, decision: Any) -> None:
        """Book the dimension this turn asked about, if it asked about one.

        Counted only when the turn actually ends in a question: an
        acknowledgement turn that names a dimension has not explored it, and
        counting it would let the interview look covered while it is not.
        """
        if getattr(decision, "action", None) is not TurnAction.CHAT:
            return
        focus = str(getattr(decision, "focus", "") or "").strip().lower()
        if not focus or focus not in FOCUS_NAMES_ZH:
            return
        strategy = str(getattr(decision, "strategy", "") or "").strip().lower()
        stance = str(getattr(decision, "stance", "") or "").strip().lower()
        if not self._posture_asks_question(strategy, stance):
            return
        self._focus_counts[focus] = self._focus_counts.get(focus, 0) + 1

    def get_interview_coverage(self) -> Dict[str, Any]:
        """What the interview has and has not asked about, for the report.

        Read from the pipeline's own turn log, never from the transcript: a
        report that guesses its coverage from a language model's summary is
        exactly the kind of figure that cannot be defended in review.
        """
        counts = {
            label: int(count)
            for label, count in self._focus_counts.items()
            if label in FOCUS_NAMES_ZH and int(count) > 0
        }
        covered = [
            {"focus": label, "name": _focus_name(label), "turns": counts[label]}
            for label in FOCUS_LABELS
            if label in counts
        ]
        uncovered = [_focus_name(label) for label in FOCUS_LABELS if label not in counts]
        return {
            "counts": counts,
            "covered": covered,
            "uncovered": uncovered,
            "total_turns": sum(counts.values()),
        }

    def get_scale_results(self) -> Dict[str, Any]:
        """Return structured scale scores for report and persistence.

        Each scale entry contains:
          scale_name, completed, answered, total_items, total_score,
          max_score, severity, items[{q_num, question, score, label, answered}]
        """
        raw_results = self.scale_runtime.get_results()
        results: Dict[str, Any] = {}

        for scale_name, raw_result in raw_results.items():
            scale_def = self._scale_manager.get_scale_definition(scale_name)
            if scale_def is None:
                continue

            answers = dict(
                self.scale_runtime.snapshot().answers_by_scale.get(scale_name, {})
            )
            total_items = scale_def.item_count
            completed = bool(raw_result["completed"])
            score_summary = raw_result

            items = []
            for i, question in enumerate(scale_def.questions, start=1):
                score = answers.get(i)
                label = None
                if score is not None:
                    for option_score, option_label in scale_def.options:
                        if option_score == score:
                            label = option_label
                            break
                items.append({
                    "q_num": i,
                    "question": question,
                    "score": score,
                    "label": label,
                    "answered": score is not None,
                })

            # For incomplete scales, don't report severity
            if completed:
                severity = score_summary["severity"]
                total_score_label = "总分"
            else:
                severity = "未完成，暂不判定"
                total_score_label = "当前累计分"

            missing_items = [i for i in range(1, total_items + 1) if i not in answers]

            results[scale_name] = {
                "scale_name": scale_def.title,
                "completed": completed,
                "answered": len(answers),
                "total_items": total_items,
                "total_score": raw_result["total_score"],
                "total_score_label": total_score_label,
                "max_score": raw_result["max_score"],
                "severity": severity,
                "missing_items": missing_items,
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
        parts = []
        for info in incomplete:
            scale_name = info["scale_name"]
            scale_def = self._scale_manager.get_scale_definition(scale_name)
            if scale_def is None:
                continue
            options_text = " / ".join(
                f"{score}-{label}" for score, label in scale_def.options
            )
            questions_text = "\n".join(
                f"  Q{info['remaining_nums'][i]}: {q}"
                for i, q in enumerate(info["remaining_questions"])
            )
            parts.append(
                f"【量表补问 - {scale_name}】\n"
                f"已答 {info['answered']}/{info['total']} 题，还需问以下题目：\n"
                f"{questions_text}\n"
                f"回答范围仅供系统内部解释：{options_text}\n"
                "请只自然地询问当前列出的第一项，不要输出评分或协议文本。"
            )
        return "\n\n".join(parts)

    def transcribe(self, audio_data: Any, emit: Callable[[str, Any], None]) -> str:
        """Convert one audio turn to text without starting dialogue processing.

        The coordinator owns the ordinary voice/text boundary between this
        operation and the rest of ``execute``.  Keeping ASR here preserves the existing STT
        provider and UI status callback while preventing duplicate audio work.
        """
        if audio_data is None or len(audio_data) == 0:
            emit("status", "未检测到语音")
            return ""
        emit("status", "正在转写...")
        metrics = get_metrics()
        with metrics.timer("stt.transcribe"):
            return self.stt.transcribe(audio_data)

    def _commit_user_turn(self, config: PipelineConfig, result: PipelineResult,
                          emit: Callable[[str, Any], None]) -> None:
        """Persist ordinary turn bookkeeping after the authoritative decision.

        Proposal, snapshot, signals, and policy remain read-only.  Round
        counters and user-message persistence are committed only after
        ``TurnDecision`` exists, so infrastructure bookkeeping cannot become a
        second action authority.
        """
        if self.report:
            self.report.increment_round()
            duration_getter = getattr(self.report, "get_session_duration_minutes", None)
            if (
                callable(duration_getter)
                and not (
                    result.turn_decision
                    and result.turn_decision.action is TurnAction.END_SESSION
                )
            ):
                emit("time_limit_check", float(duration_getter()))

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
        if config.transcribed_text:
            result.user_text = config.transcribed_text
        elif config.use_stt:
            asr_started = time.perf_counter()
            result.user_text = self.transcribe(config.audio_data, emit)
            result.timing["asr_ms"] = (time.perf_counter() - asr_started) * 1000.0
            if not result.user_text.strip():
                if config.audio_data is not None and len(config.audio_data) > 0:
                    emit("status", "无法识别内容")
                metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
                return result
        else:
            result.user_text = config.user_text

        # ASR post-processing: correct common speech recognition errors
        raw_text = result.user_text
        semantic_flags = inspect_input_semantics(raw_text)
        result.user_text, _asr_corrections = correct_asr_text(raw_text)
        # Engagement is judged on the corrected text, once per turn, before
        # any context is built for this turn's reply.
        self._update_engagement(result.user_text)
        if _asr_corrections:
            logger.warning(
                "[ASRCorrect] "
                f"{text_meta('raw', raw_text)} {text_meta('corrected', result.user_text)}"
            )

        emit("append_chat", ("user", result.user_text))

        # Evaluate the incoming turn number without mutating the report yet.
        current_rounds = (self.report.get_round_count() + 1) if self.report else 0

        # An invitation is retired only when the conversation has visibly moved
        # on -- never on a bare turn count.  A participant who answers three
        # turns later must still be able to consent.
        self._begin_offer_round(current_rounds, result.user_text)

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
        _normalized = result.user_text.strip("。！？!?,， ").strip()
        _is_greeting = (
            _normalized in _GREETING_INPUTS
            or ("你好" in _normalized and len(_normalized) <= 10)
            or (len(_normalized) <= 6 and _normalized.replace("呀", "").replace("啊", "").replace("哈", "") in {"你好", "嗨"})
        )
        if current_rounds <= 2 and _is_greeting:
            # Fast replies still pass through the same one-decision boundary;
            # the optimized branch only skips model generation.
            proposal = RouterProposal.fallback("greeting_fast_path")
            snapshot = self._build_turn_snapshot(
                round_count=current_rounds,
                # Timeout markers are owned by SessionEngine.  The pipeline
                # must not read ReportService's legacy marker as a second
                # action policy.
                time_limit_reached=False,
                session_state=config.session_state,
            )
            policy_started = time.perf_counter()
            decision = self.turn_policy.decide(
                user_text=result.user_text,
                proposal=proposal,
                snapshot=snapshot,
                signals=collect_turn_signals(
                    result.user_text,
                    snapshot,
                    semantic_ambiguity=semantic_flags.any_ambiguous,
                    semantic_target=semantic_flags.semantic_target,
                    semantic_reason=semantic_flags.reason,
                ),
            )
            result.timing["turn_policy_ms"] = (time.perf_counter() - policy_started) * 1000.0
            result.router_proposal = proposal
            result.turn_state_snapshot = snapshot
            result.turn_decision = decision
            self._apply_turn_decision(decision, result, current_rounds)
            self._commit_user_turn(config, result, emit)
            import random
            _GREETING_REPLIES = [
                "你好呀。[breath]今天感觉咋样？",
                "嗨，来了呀。[breath]最近怎么样？",
                "你好。[breath]在这儿呢，有啥想说的？",
            ]
            spoken = random.choice(_GREETING_REPLIES)
            analysis = "【情绪】平静【状态】开放【策略】破冰回应"
            # Keep the old analysis as a derived audit field, but send/store
            # only the language that the participant can hear.
            full = spoken
            result.full_response = full
            result.analysis_text = analysis
            result.spoken_text = spoken
            result.clean_spoken = clean_for_display(spoken)
            result.tts_text = clean_for_tts(spoken)
            result.intent = "counseling"
            if config.generation_id is None:
                emit("start_ai_message", None)
                emit("stream_text", result.clean_spoken)
                emit("finish_streaming", None)
            else:
                emit("start_ai_message", config.generation_id)
                self._emit_generation_text(
                    config.generation_id,
                    spoken,
                    emit,
                )
                emit("finish_streaming", config.generation_id)
            if self.data and config.generation_id is None:
                self.data.save_assistant_message(None, result.clean_spoken, sample_rate=48000)
            if self.llm and hasattr(self.llm, "conversation_history"):
                self.llm.conversation_history.append({"role": "user", "content": result.user_text})
                # Generation-scoped history is finalized after visible UI
                # commits; compatibility callers retain the old fast-path
                # append behavior.
                if config.generation_id is None:
                    self.llm.conversation_history.append({"role": "assistant", "content": clean_for_display(spoken)})
            if config.use_tts and self.tts and result.tts_text and config.generation_id is None:
                emit("status", "正在播放...")
                self._queue_complete_tts_compat(result.tts_text)
            metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
            return result

        # The Router proposal, snapshot, and pure signals are assembled before
        # any scale/session mutation.  RAG and LLM context are built only after
        # the authoritative decision exists.
        proposal = config.router_proposal
        agent_route = proposal.model_dump(mode="json") if proposal is not None else None
        agent_observation: AgentObservation | None = None
        if proposal is None:
            agent_started = time.perf_counter()
            agent_observation = self._request_agent_observation(
                user_text=result.user_text,
                current_rounds=current_rounds,
            )
            result.timing["agent_ms"] = (time.perf_counter() - agent_started) * 1000.0
            proposal = agent_observation.proposal
            agent_route = proposal.model_dump(mode="json")
            result.agent_observation = agent_observation
        else:
            result.agent_observation = AgentObservation(
                proposal=proposal,
                intent="counseling",
                fallback_used=False,
                source="caller_provided_proposal",
            )
        result.router_proposal = proposal
        result.agent_route = dict(agent_route or proposal.model_dump(mode="json"))

        # Cumulative symptoms and the optional hard detector are observations;
        # neither is allowed to start a scale at this point.
        deterministic_candidate = None
        from config import ENABLE_SCALE_HARD_TRIGGER
        before_runtime = self.scale_runtime.snapshot()
        if ENABLE_SCALE_HARD_TRIGGER and not before_runtime.active_scale:
            detected = self._deterministic_scale_trigger(result.user_text)
            if detected:
                deterministic_candidate = detected[0]
        pre_deltas, pre_reasons = ({}, [])
        projected = None
        if not before_runtime.active_scale:
            pre_deltas, pre_reasons = score_symptom_signals(
                result.user_text, self.symptom_scores
            )
            projected = {
                scale: self.symptom_scores.get(scale, 0) + delta
                for scale, delta in pre_deltas.items()
            }
            if current_rounds >= MIN_ROUNDS_BEFORE_SCALE:
                # Same ranking the policy reads, so the deterministic candidate
                # and the offered candidate can never be two different
                # instruments for the same evidence.  Sorting the raw score map
                # here used a different tie-break (alphabetical) than
                # ``rank_scale_candidates`` (registry order).
                for candidate in self._ranked_scale_candidates(
                    symptom_scores=projected
                ):
                    if candidate.evidence >= SCALE_EVIDENCE_THRESHOLD:
                        deterministic_candidate = (
                            deterministic_candidate or candidate.name
                        )
                        break

        snapshot = result.turn_state_snapshot
        if snapshot is None:
            snapshot = self._build_turn_snapshot(
                round_count=current_rounds,
                # Timeout markers are owned by SessionEngine; this turn path
                # only carries ordinary conversation policy inputs.
                time_limit_reached=False,
                session_state=config.session_state,
                # This turn's evidence has not been committed to the running
                # counters yet, so hand the ranking the projected totals.
                symptom_scores=projected,
            )
        result.turn_state_snapshot = snapshot
        signals = collect_turn_signals(
            result.user_text,
            snapshot,
            deterministic_scale_candidate=deterministic_candidate,
            proactive_relaxation_candidate=self._proactive_relaxation_candidate,
            semantic_ambiguity=semantic_flags.any_ambiguous,
            semantic_target=semantic_flags.semantic_target,
            semantic_reason=semantic_flags.reason,
        )
        decision = config.turn_decision
        if decision is None:
            policy_started = time.perf_counter()
            decision = self.turn_policy.decide(
                user_text=result.user_text,
                proposal=proposal,
                snapshot=snapshot,
                signals=signals,
            )
            result.timing["turn_policy_ms"] = (time.perf_counter() - policy_started) * 1000.0
        result.turn_decision = decision
        # The router's own choice is logged by the provider ([Decision]); this
        # line records what the *policy* finally decided.  Without it a session
        # that never offered an exercise is undiagnosable after the fact: the
        # provider log says chat because the router said chat, even when the
        # policy overrode it, and the reverse.
        logger.warning(
            "[TurnChoice] action=%s reason=%s round=%s proactive_candidate=%s",
            getattr(decision.action, "value", decision.action),
            decision.reason,
            current_rounds,
            self._proactive_relaxation_candidate,
        )
        # An explicit rest request can share one utterance with the answer to
        # the current scale item. Interpret that answer before the policy's
        # pause command, then let the same TurnDecision pause the next item.
        compound_relaxation_turn = bool(
            decision.action is TurnAction.RECOMMEND_RELAXATION
            and decision.reason == "user_relaxation_request"
            and snapshot.active_scale
        )
        if compound_relaxation_turn:
            self._apply_scale_answer_transition(result.user_text, decision, result)
        # A closed session or a safety escalation cancels every open
        # invitation: carrying one across either boundary would let a later
        # "好" start an assessment the participant never agreed to in context.
        if decision.action is TurnAction.END_SESSION or decision.reason == "session_ending":
            self._cancel_open_offers(current_rounds, "session_end")
        elif detect_risk_marker(result.user_text):
            self._cancel_open_offers(current_rounds, "risk")
            self._risk_disclosed = True
        self._apply_offer_outcome(decision, current_rounds)
        self._apply_turn_decision(decision, result, current_rounds)
        self._clarification_pending = decision.action is TurnAction.CLARIFY_INPUT
        if decision.action is TurnAction.CLARIFY_INPUT:
            self._clarifications_in_a_row += 1
        else:
            self._clarifications_in_a_row = 0
        self._record_interview_turn(result.user_text, current_rounds)
        self._commit_user_turn(config, result, emit)
        runtime = self.scale_runtime.snapshot()
        if not compound_relaxation_turn:
            self._apply_scale_answer_transition(result.user_text, decision, result)
        runtime = self.scale_runtime.snapshot()
        # Age the waiting slot once per turn.  Cleared on commit and reset when
        # the runtime presents another item.
        if runtime.active_scale and runtime.current_item:
            waiting_key = (runtime.active_scale, runtime.current_item)
            self._scale_wait_turns[waiting_key] = (
                self._scale_wait_turns.get(waiting_key, 0) + 1
            )
        # One guided exercise per participant is a programme invariant, not a
        # side effect of finishing a questionnaire.
        self._maybe_queue_relaxation(current_rounds)
        self._set_guard_context(
            config.generation_id,
            decision,
            runtime,
            user_text=result.user_text,
            semantic_target=semantic_flags.semantic_target,
        )
        if result.agent_observation is not None:
            self._apply_agent_observation(result.agent_observation, result)

        # Apply the one turn's symptom observations only after the decision.
        if not before_runtime.active_scale and pre_deltas:
            if any(value > 0 for value in pre_deltas.values()):
                self.symptom_turns += 1
                for scale_name, delta in pre_deltas.items():
                    if delta > 0:
                        self.symptom_scores[scale_name] = self.symptom_scores.get(scale_name, 0) + delta
            logger.warning(
                f"[ScaleTriggerScore] user={result.user_text!r} "
                f"deltas={pre_deltas} totals={self.symptom_scores} reasons={pre_reasons}"
            )

        # Build ordinary context only after the decision has been formed.
        # Retrieval is gated three ways now: the decision's needs_rag bit,
        # a plain-chat action, and the absence of a risk marker.  A task turn
        # (clarify / scale / invitation) must not carry topic knowledge --
        # the curated set includes an assessment-tools entry, which on a
        # scale turn is worse than silence.
        rag_enabled = (
            decision.needs_rag
            and decision.action is TurnAction.CHAT
            and not detect_risk_marker(result.user_text)
        )
        rag_started = time.perf_counter()
        with metrics.timer("rag.system_suffix"):
            system_suffix = self._build_system_suffix(
                result.user_text,
                # A risk disclosure is not a topic to look up intervention
                # techniques for.  Retrieval used to hand the writer a
                # breathing protocol on "活着没什么意思", which is exactly the
                # wrong move on a crisis turn.
                needs_rag=rag_enabled,
                round_count=current_rounds,
                allow_user_relaxation=(decision.reason == "user_relaxation_request"),
            )
        if rag_enabled:
            result.timing["rag_ms"] = (time.perf_counter() - rag_started) * 1000.0

        decision_context = self._build_decision_language_context(decision, runtime)
        # The decision layer's own read of the turn (intent/emotion/confidence)
        # travels with the task instruction so Jev participates in the wording,
        # not just in the action gate.
        jev_context = self._build_jev_judgement_context(decision)
        engagement_context = self._build_engagement_context()
        no_repeat_context = self._build_no_repeat_context()
        if decision_context or jev_context or engagement_context or no_repeat_context:
            system_suffix = "\n".join(
                part for part in (
                    jev_context, engagement_context, no_repeat_context,
                    decision_context, system_suffix,
                ) if part
            )

        # Booked after the prompt is assembled, so the writer's "already asked
        # N times" note describes the turns *before* this one.
        self._record_focus(decision)

        if runtime.active_scale and runtime.paused:
            # The questions are parked, not cancelled.  Without this note the
            # writer keeps steering back to assessment turns and the pause is
            # experienced as being ignored; with it, the parked scale stays
            # available for a single gentle re-invitation much later.
            self._scale_paused_turns += 1
            if self._scale_paused_turns <= 2:
                system_suffix += (
                    "\n【量表暂停】来访者刚说想先停一下问题。这一轮不要提问卷里的题目，"
                    "也不要再提量表这件事，顺着他说的往下聊。"
                )
            elif self._scale_paused_turns == 3:
                system_suffix += (
                    "\n【量表暂停】那几个问题还留着没问完。这一轮仍然不要主动提；"
                    "只有等他自己这一段说完了、语气松下来，才可以用一句很轻的话问"
                    "要不要把那几个问题说完，问一次就够，他不接就不再提。"
                )
            else:
                system_suffix += (
                    "\n【量表暂停】那几个问题先搁着，不要再提，也不要暗示还有题没答。"
                )
        elif runtime.active_scale and not runtime.paused:
            self._scale_paused_turns = 0

        if decision.action in (TurnAction.START_SCALE, TurnAction.CONTINUE_SCALE):
            if result.scale_completed:
                # The last item was just recorded, so the Runtime no longer
                # has a current item.  Falling through to the question hint
                # would address item 1 again -- literally asking the scale a
                # second time -- and the writer's only other option was the
                # filler "好，那咱们继续。"
                system_suffix += (
                    "\n【本轮收束】刚才那一组问题问完了。用一句自然的话把这件事收住"
                    "（例如“好，这几方面我都记下了”），然后就停下来，把下一句留给来访者。"
                    "不要回头问第一题，不要说“那咱们继续”，也不要在这一轮提放松、"
                    "报告或下一步安排。"
                )
            else:
                hint = self._build_scale_context_hint(
                    runtime.active_scale or decision.scale_name,
                    runtime.current_item or 1,
                    agent_route or {},
                    clarify=bool(getattr(self, "_clarification_pending", False)),
                )
                if hint:
                    system_suffix += hint
        elif decision.action is TurnAction.RECOMMEND_RELAXATION:
            recommendation_name = _RELAXATION_LABELS.get(
                self._relaxation_recommendation or "", ""
            )
            if recommendation_name:
                system_suffix += (
                    f"\n【放松邀请】邀请他试一次{recommendation_name}（就在左边“轻松一下”里，"
                    "点开就能开始）。这一轮说的话里要出现“"
                    f"{recommendation_name}”这几个字，不要只说“有些练习”。"
                    "由他自己决定做不做；不要自动播放，不要念步骤，也不要把话说成命令。"
                )
            elif decision.intervention_type:
                system_suffix += (
                    "\n【放松邀请】来访者明确提到一种放松偏好。可以自然邀请其打开放松中心，"
                    "但不要自动播放或把偏好说成系统决定。"
                )
            else:
                system_suffix += (
                    "\n【放松邀请】可以自然邀请来访者先到放松中心看一看，"
                    "由来访者自行决定是否休息和选择什么内容。不要指定具体活动。"
                )
        elif decision.action is TurnAction.RECOMMEND_GAME:
            system_suffix += (
                "\n【游戏入口】只引导来访者进入放松中心的游戏选择页，"
                "由来访者自行选择，不要指定或自动开始某个游戏。"
            )

        # Deterministic detectors have already been reduced to ``signals`` and
        # approved (or rejected) by TurnPolicy.  There is intentionally no
        # second trigger or eligibility check here.

        if self.emotion_tracker:
            hint = self.emotion_tracker.get_intervention_hint()
            if hint:
                system_suffix += "\n" + hint

        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

        # --- No programmatic scale questions ---
        # Scales are explored naturally by the LLM via subtle system_suffix
        # hints. Structured answer interpretation and Runtime transition have
        # already happened before this language-generation branch.

        # Append extra system context (e.g. remaining scale questions at exit)
        if config.extra_system_suffix:
            if final_suffix:
                final_suffix += "\n" + config.extra_system_suffix
            else:
                final_suffix = config.extra_system_suffix

        # --- Pre-LLM positive_pending check ---
        # If active scale item has positive symptoms but no frequency,
        # inject mandatory frequency follow-up BEFORE LLM generates.
        runtime = self.scale_runtime.snapshot()
        if runtime.active_scale and runtime.waiting_for_answer:
            answered = runtime.answers_by_scale.get(runtime.active_scale, {})
            if runtime.current_item not in answered:
                _pre_positive = self._is_positive_pending_frequency(
                    runtime.active_scale, runtime.current_item, result.user_text
                )
                if _pre_positive:
                    _item_hint = NATURAL_SCALE_QUESTIONS.get(
                        (runtime.active_scale, runtime.current_item), ""
                    )
                    freq_hint = f"""
【必须追问频率】用户已明确表达存在症状，但缺少频率信息。
当前量表：{runtime.active_scale}
当前题：Q{runtime.current_item}
当前维度：{_item_hint}
请自然追问最近两周频率。只能问这一件事。
不要说量表、评分、PHQ。不能结束，不能推荐放松。
"""
                    if final_suffix:
                        final_suffix += "\n" + freq_hint
                    else:
                        final_suffix = freq_hint
                    logger.warning(
                        f"[ScaleDebug] pre-LLM positive_pending: {runtime.active_scale} "
                        f"Q{runtime.current_item}, injecting frequency hint"
                    )

        # --- LLM stream ---
        # The single Agent observation was already collected before the
        # authoritative decision and language context. No secondary intent or
        # emotion model calls are started here.
        emit("start_ai_message", config.generation_id)

        try:
            with metrics.timer("llm.stream"):
                result.full_response, result.analysis_text, result.spoken_text = \
                    self._stream_llm(
                        result.user_text,
                        final_suffix,
                        emit,
                        generation_id=config.generation_id,
                        timing=result.timing,
                    )

            # Log raw LLM response before post-processing
            logger.warning(
                "[PipelineReplyRaw] "
                f"{text_meta('user', result.user_text)} "
                f"{text_meta('raw', result.full_response, limit=800)}"
            )

        except RuntimeError as e:
            if "LLM_NO_FINAL_CONTENT" in str(e):
                # The model ended the reply without any content.  Asking the
                # participant to "say that again" hands the failure back to
                # them, which is the wrong move in an interview: it reads as
                # "you were unclear" and costs the turn.  Keep holding the
                # conversation with what they just said instead.
                logger.warning("[Pipeline] LLM thinking-only, no content, holding the turn")
                result.spoken_text = make_safe_fallback_reply(result.user_text)
                result.analysis_text = ""
                result.full_response = result.spoken_text
                if config.generation_id is None:
                    emit("stream_text", clean_for_display(result.spoken_text))
                else:
                    self._emit_generation_text(config.generation_id, result.spoken_text, emit)
            else:
                logger.exception(f"[Pipeline] LLM RuntimeError: {e}")
                result.spoken_text = "刚才我这边卡了一下。[breath]你说的我都记着，你接着往下说就行。"
                result.analysis_text = ""
                result.full_response = result.spoken_text
                if config.generation_id is None:
                    emit("stream_text", clean_for_display(result.spoken_text))
                else:
                    self._emit_generation_text(config.generation_id, result.spoken_text, emit)
        except Exception as e:
            logger.exception(f"[Pipeline] LLM failed: {e}")
            result.spoken_text = "刚才我这边卡了一下。[breath]你说的我都记着，你接着往下说就行。"
            result.analysis_text = ""
            result.full_response = result.spoken_text
            if config.generation_id is None:
                emit("stream_text", clean_for_display(result.spoken_text))
            else:
                self._emit_generation_text(config.generation_id, result.spoken_text, emit)
        if config.generation_id is None:
            emit("finish_streaming", None)
        else:
            emit("finish_streaming", config.generation_id)
            self._guard_contexts.pop(config.generation_id, None)
            self._guard_states.pop(config.generation_id, None)
            self._guard_fallbacks.pop(config.generation_id, None)

        # --- Prepare TTS text immediately ---
        # Limit to one question per reply to prevent rapid-fire questioning
        result.spoken_text = limit_to_one_question(result.spoken_text)
        self._record_questions(result.spoken_text)

        # Check for internal strategy term leak before display/TTS
        if _contains_internal_leak(result.spoken_text):
            logger.warning(
                "[OutputClean] internal strategy leaked: "
                f"{text_meta('spoken', result.spoken_text)}"
            )
            if runtime.active_scale:
                result.spoken_text = self._make_scale_clarify_reply(
                    runtime.active_scale, runtime.current_item or 1, result.user_text
                )
            else:
                result.spoken_text = make_safe_fallback_reply(result.user_text)

        result.clean_spoken = clean_for_display(result.spoken_text)
        if config.generation_id is None:
            emit("clean_last_ai", result.clean_spoken)
        result.tts_text = clean_for_tts(result.spoken_text)

        # Model tags are no longer parsed on the live path.  A legacy tag is
        # cleaned defensively by ResponseBuilder, but it cannot affect an
        # already-formed decision or any mutable runtime owner.
        if result.turn_decision and result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION:
            result.relaxation_rec = (
                _normalize_relaxation_type(result.turn_decision.intervention_type)
                or self._relaxation_recommendation
            )
            logger.warning(f"[RelaxDebug] relaxation authorized by TurnDecision: {result.relaxation_rec}")
        # Preserve this turn's recommendation until the asynchronous intent
        # classification has completed below.
        game_recommended = bool(
            result.turn_decision
            and result.turn_decision.action is TurnAction.RECOMMEND_GAME
        )
        runtime_after = self.scale_runtime.snapshot()

        # Log pipeline output for debugging
        logger.warning(
            f"[PipelineReplyFinal] user={result.user_text!r} "
            f"spoken={result.spoken_text[:500]!r} "
            f"end_type={result.end_type} "
            f"relaxation={result.relaxation_rec} "
            f"scale_tags={result.scale_tags}"
        )
        logger.warning(
            f"[ScaleRuntime] active={runtime_after.active_scale} "
            f"item={runtime_after.current_item} "
            f"waiting={runtime_after.waiting_for_answer} "
            f"paused={runtime_after.paused} "
            f"tags={result.scale_tags}"
        )

        # --- Save assistant message ---
        if self.data and config.generation_id is None:
            self.data.save_assistant_message(None, result.clean_spoken, sample_rate=48000)

        # --- TTS post-processing ---
        # Skip normal response TTS when the authoritative decision ends the
        # session; the session-end flow owns the farewell audio.
        tts_future = None
        skip_normal_tts = bool(result.end_type)
        if skip_normal_tts:
            logger.info("[TTS] Skipping normal response TTS — authoritative session-end flow handles farewell.")
        if (
            config.use_tts
            and self.tts
            and result.tts_text
            and not skip_normal_tts
            and config.generation_id is None
        ):
            emit("status", "正在播放...")
            self._queue_complete_tts_compat(result.tts_text)
            latency_ms = (time.perf_counter() - pipeline_started) * 1000.0
            logger.warning(f"[Latency] input→TTS: {latency_ms:.0f}ms | spoken_len={len(result.spoken_text)}")

        if game_recommended:
            result.intent = "entertainment"
            logger.warning("[GameDebug] game recommendation from agent")

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

        logger.info(f"[Pipeline] RAG suffix: {bool(final_suffix)}")

        result.scale_active = bool(self.scale_runtime.snapshot().active_scale)
        metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
        return result

    def _queue_complete_tts_compat(self, text: str) -> Optional[int]:
        """Queue a complete compatibility response through the shared worker.

        Direct pipeline callers from before Phase 7 do not provide a delivery
        generation.  They still receive asynchronous whole-response audio,
        but the call now uses the same single sentence worker as the live
        generation path instead of creating an unscoped TTS thread/future.
        Compatibility audio is deliberately not written to the delivery
        ledger: those callers already persist their assistant text directly.
        """
        if not self.tts or not str(text or "").strip():
            return None
        record = self.delivery_controller.start_generation()
        self.delivery_queue.start()
        segmenter = SentenceSegmenter()
        events = segmenter.feed(record.generation_id, str(text))
        events.extend(segmenter.flush(record.generation_id))
        for event in events:
            self.delivery_queue.enqueue(event)
        return record.generation_id

    def _classify_intent_emotion(self, text: str) -> tuple[str, dict]:
        """Run the ordinary intent and emotion classifiers in parallel.

        The local keyword pass runs first and is a real answer, not a
        placeholder.  It used to be reachable only through the decision
        fallback, so with the agent endpoint down every single turn was
        classified as neutral / 0.0 -- and the writer, told to "name the
        feeling in one concrete sentence", had no feeling to name.  That is
        where "确实挺…的" came from.  The remote agent still has the last
        word when it is reachable, because it reads nuance keywords miss.
        """
        intent_result = {"intent": "counseling", "confidence": 1.0}
        emotion_result = {"emotion": "neutral", "intensity": 0.0}
        keyword_intent = keyword_emotion = None
        if self.agent:
            keyword_intent = getattr(self.agent, "_keyword_classify", None)
            keyword_emotion = getattr(self.agent, "_keyword_detect_emotion", None)
        if callable(keyword_intent):
            try:
                candidate = keyword_intent(text)
                if isinstance(candidate, dict) and candidate.get("intent"):
                    intent_result = candidate
            except Exception as e:
                logger.debug(f"keyword intent failed: {e}")
        if callable(keyword_emotion):
            try:
                candidate = keyword_emotion(text)
                if isinstance(candidate, dict) and candidate.get("emotion"):
                    emotion_result = candidate
            except Exception as e:
                logger.debug(f"keyword emotion failed: {e}")

        # Only pay for the network round-trip when the endpoint is actually up;
        # otherwise every turn waited on a connection that was never there.
        if self.agent and self.agent.is_available():
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
                except Exception as e:
                    logger.debug(f"{tag} detection unavailable, keeping local read: {e}")
        self.session_emotions.append({"role": "user", **emotion_result})
        intent = intent_result.get("intent", "counseling")
        logger.info(
            f"Intent: {intent} ({intent_result.get('confidence', 0):.2f}) "
            f"| Emotion: {emotion_result.get('emotion', 'neutral')} "
            f"({emotion_result.get('intensity', 0):.2f})"
            f" keywords={emotion_result.get('keywords', [])}"
        )
        return intent, emotion_result

    def _build_active_scale_prompt(self, scale_name: str, q_num: int,
                                    waiting_answer: bool) -> str:
        """Build a subtle system hint for natural symptom exploration.

        This does NOT tell the LLM to "ask question N" or "score this item".
        Instead, it hints which symptom area to naturally explore, and lets
        the LLM generate a conversational response. Background scoring has
        already passed through ScaleAnswerInterpreter and ScaleRuntime.
        """
        scale_def = self._scale_manager.get_scale_definition(scale_name)
        if scale_def is None:
            return ""

        q_text = scale_def.questions[q_num - 1]
        natural_q = NATURAL_SCALE_QUESTIONS.get((scale_name, q_num), q_text)

        # Map scale to a brief clinical hint
        scale_hints = {
            "PHQ-9": "抑郁倾向",
            "GAD-7": "焦虑倾向",
            "PCL-5": "创伤应激倾向",
        }
        clinical_hint = scale_hints.get(scale_name, "心理困扰")

        # Build a list of symptom areas still to explore
        answered = self.scale_runtime.snapshot().answers_by_scale.get(scale_name, {})
        remaining_areas = []
        for i, q in enumerate(scale_def.questions, start=1):
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
            # The answer is interpreted outside the language model.  The model
            # only keeps the current natural question in conversational view.
            return f"""
【当前表达任务】来访者正在回应：{natural_q}
请先承接这句话；如果频率或程度仍不清楚，只温和追问这一点。
不要自行打分、推进下一项或输出内部协议文本。
"""

    def _make_scale_clarify_reply(self, scale_name: str, item: int, user_text: str) -> str:
        """Generate a safe reply when internal strategy terms leak into spoken output."""
        import random
        if scale_name == "PHQ-9":
            item_replies = {
                1: "嗯，兴趣这块确实有变化。[breath]那最近两周，这种提不起劲是偶尔几天，还是大多数时候都这样？",
                2: "听起来这份低落感挺重的。[breath]那这种感觉最近是偶尔一阵，还是大多数时间都有？",
                3: "睡眠这块确实很重要。[breath]那最近两周，这种睡眠问题是偶尔几天，还是大多数时候都会有？",
                4: "嗯，累的感觉确实会很消耗人。[breath]那最近两周，这种疲惫是偶尔几天，还是大多数时间都有？",
                5: "吃饭这块也有变化是吧。[breath]那最近两周，这种食欲变化是偶尔几天，还是大多数时候？",
                6: "嗯，自责的感觉确实很沉重。[breath]那最近两周，这种感觉是偶尔出现，还是大多数时候都在？",
                7: "注意力这块也有影响。[breath]那最近两周，这种难以集中是偶尔几天，还是大多数时候？",
                8: "嗯，坐不住这种感觉确实挺折腾的。[breath]那最近两周，这种坐立不安是偶尔几天，还是大多数时间都会有？",
                9: "谢谢你愿意告诉我这个。[breath]当你说这些的时候，有没有出现过伤害自己的念头？",
            }
            if item in item_replies:
                return item_replies[item]
        return random.choice([
            "嗯，我听着呢。[breath]你接着说。",
            "你说的我都有在听。[breath]再往下说说？",
        ])

    @staticmethod
    def _reply_mentions_relaxation(spoken_text: str, rec_type: str) -> bool:
        """Check if AI's spoken reply actually mentions the recommended relaxation."""
        text = spoken_text or ""
        general = any(x in text for x in [
            "放松训练", "放松一下", "缓一缓", "调整一下", "左边", "按钮", "跟着做"
        ])
        if rec_type == "breathing":
            specific = any(x in text for x in ["呼吸", "深呼吸", "呼吸放松"])
        elif rec_type == "muscle":
            specific = any(x in text for x in ["肌肉", "身体放松", "绷紧再放松"])
        elif rec_type == "meditation":
            specific = any(x in text for x in ["冥想", "正念", "静一静"])
        else:
            specific = False
        return general and specific

    def _choose_post_scale_relaxation(self, scale_name: str) -> Optional[str]:
        """Choose relaxation type from the answers the instrument actually got.

        The mapping is deliberately symptom-led rather than fixed: a
        participant whose sleep and energy items are high is handed the body
        scan, while someone whose anxiety items dominate gets the muscle
        sequence.  Breathing stays the neutral default for everyone else.
        """
        answers = self.scale_runtime.snapshot().answers_by_scale.get(scale_name, {})
        if not answers:
            return self._relaxation_type_from_focus()

        if scale_name == "PHQ-9":
            # Q3 sleep / Q4 energy → body scan settles an aroused sleeper;
            # Q2 low mood → same exercise, different rationale.
            if answers.get(3, 0) >= 2 or answers.get(4, 0) >= 2:
                return "meditation"
            if answers.get(2, 0) >= 2:
                return "meditation"
            return "breathing"

        if scale_name == "GAD-7":
            # Somatic tension is what GAD answers point at, so release it
            # directly instead of asking an anxious person to sit still.
            if sum(answers.values()) >= 8:
                return "muscle"
            return "breathing"

        return "breathing"

    def _relaxation_type_from_focus(self) -> str:
        """Pick an exercise from what the interview has been circling."""
        counts = getattr(self, "_focus_counts", {}) or {}
        gad_total = 0
        scores = getattr(self, "symptom_scores", None)
        if isinstance(scores, dict):
            try:
                gad_total = int(scores.get("GAD-7", 0) or 0)
            except (TypeError, ValueError):
                gad_total = 0
        if gad_total >= 5 or counts.get("body", 0) >= 2:
            return "muscle"
        if counts.get("sleep", 0) + counts.get("energy", 0) + counts.get("mood", 0) >= 2:
            return "meditation"
        return "breathing"

    #: How long one instrument item may sit open before the questionnaire is
    #: treated as stalled.  Past this the session is no longer making progress
    #: on the scale, so the relaxation invitation is allowed to queue anyway.
    RELAXATION_SCALE_STALL_TURNS = 4

    def _maybe_queue_relaxation(self, round_count: int) -> None:
        """Queue the one relaxation invitation every session is entitled to.

        The candidate used to be produced *only* when a scale completed, so a
        session whose questionnaire stalled (or was never started) could run to
        the end without a single exercise being offered.  One guided exercise
        per participant is part of the interview, not a reward for finishing an
        instrument, so the queue is re-evaluated every turn against the
        session's own state.
        """
        if self._proactive_relaxation_offered or self._proactive_relaxation_candidate:
            return
        if getattr(self.report, "completed_relaxation", None):
            return
        if getattr(self, "_risk_disclosed", False):
            return
        if max(0, int(round_count)) < MIN_ROUNDS_FOR_RELAXATION:
            return
        runtime = self.scale_runtime.snapshot()
        if runtime.active_scale:
            waited = max(self._scale_wait_turns.values() or [0])
            if waited < self.RELAXATION_SCALE_STALL_TURNS:
                # The instrument owns the floor while it is still moving.
                return
            logger.warning(
                "[Relaxation] scale %s looks stalled (item waiting %s turns); "
                "queueing an exercise anyway",
                runtime.active_scale,
                waited,
            )
        rec = self._choose_relaxation_type()
        self._queue_relaxation_candidate(rec)
        logger.warning(
            "[Relaxation] proactive candidate queued type=%s round=%s",
            rec,
            round_count,
        )

    def _choose_relaxation_type(self) -> str:
        """The single exercise this session will recommend."""
        runtime = self.scale_runtime.snapshot()
        for name in runtime.completed_scales or ():
            rec = self._choose_post_scale_relaxation(name)
            if rec:
                return rec
        return self._relaxation_type_from_focus()

    def _queue_relaxation_candidate(self, rec: str) -> None:
        """Remember both the candidate and the concrete content to name.

        The candidate is what the policy reads; the recommendation is what the
        writer says out loud and what the Center highlights.  Keeping them in
        one place is what stops "去放松中心自己挑" from being spoken when the
        system already knows which exercise fits.
        """
        self._proactive_relaxation_candidate = rec
        self._relaxation_recommendation = rec

    def get_relaxation_recommendation(self) -> Optional[str]:
        """The exercise this session recommended, as a contract code.

        The report needs this to answer "was an exercise offered, and which
        one?" even when the participant never started it.  Deriving it from the
        transcript instead left the field empty whenever the writer phrased the
        invitation loosely, which is exactly what happened in a real session
        where MEDITATION was recommended and the report read "无".
        """
        return self._relaxation_recommendation

    def get_risk_disclosed(self) -> bool:
        """Whether any turn in this session carried an asserted risk marker.

        The report has to carry this as a record rather than a guess: in a real
        session the participant said "想结束这一切", the safety path blocked
        every invitation, and the researcher report's ``risk_assessment`` came
        back empty because the writing model simply did not fill it in.
        """
        return bool(self._risk_disclosed)

    def _deterministic_scale_trigger(self, text: str) -> Optional[tuple]:
        """Hard fallback: any symptom keyword from any scale item triggers that scale.

        Returns (scale_name, item) or None.
        """
        t = (text or "").strip()
        if not t:
            return None

        # PHQ-9: any symptom from any item → start PHQ-9
        phq9_symptoms = {
            1: ["没兴趣", "没意思", "提不起劲", "做什么都没劲", "不想做", "无聊"],
            2: ["心情不好", "不开心", "低落", "沮丧", "难受", "绝望", "悲伤", "想哭"],
            3: ["睡不着", "失眠", "睡不好", "早醒", "睡太多", "入睡困难", "半夜醒"],
            4: ["累", "没力气", "没劲", "疲惫", "乏力", "没精神", "撑不住"],
            5: ["没胃口", "吃不下", "吃太多", "食欲不好", "不想吃"],
            6: ["觉得自己很糟", "失败", "自责", "不够好", "让家人失望", "没用"],
            7: ["注意力", "集中不了", "看不进去", "专注不了", "分心"],
            8: ["坐不住", "坐立不安", "烦躁", "急躁", "动作变慢", "说话变慢"],
            9: ["不想活", "伤害自己", "自杀", "自残", "死了算了", "想死"],
        }
        for item, keywords in phq9_symptoms.items():
            if any(kw in t for kw in keywords):
                return ("PHQ-9", item)

        # GAD-7: any symptom from any item → start GAD-7
        gad7_symptoms = {
            1: ["紧张", "焦虑", "急切", "心慌", "不安"],
            2: ["停不下来", "控制不了担心", "控制不住"],
            3: ["担心", "担忧", "操心", "放心不下"],
            4: ["放松不了", "放松不下来", "静不下来"],
            5: ["坐不住", "坐立不安", "动来动去"],
            6: ["烦", "急躁", "容易生气", "不耐烦"],
            7: ["害怕", "恐惧", "觉得要出事", "总觉得不好"],
        }
        for item, keywords in gad7_symptoms.items():
            if any(kw in t for kw in keywords):
                return ("GAD-7", item)

        # PCL-5: any symptom from any item → start PCL-5
        pcl5_symptoms = {
            1: ["回忆", "闪回", "想起来", "反复想起"],
            2: ["噩梦", "做噩梦", "梦到"],
            3: ["避免", "不想提", "不去想", "回避"],
            4: ["避开", "不去", "躲开"],
            5: ["很坏", "很危险", "没希望", "世界很危险"],
            6: ["责怪", "自责", "怪自己", "怪别人"],
            7: ["警觉", "易受惊吓", "紧张", "害怕"],
            8: ["注意力", "集中不了", "分心"],
        }
        for item, keywords in pcl5_symptoms.items():
            if any(kw in t for kw in keywords):
                return ("PCL-5", item)

        return None

    def _is_positive_pending_frequency(self, scale_name: str, item: int, text: str) -> bool:
        """Check if user confirmed symptom exists but didn't provide frequency.

        Returns True when text contains item-specific positive keywords
        but no frequency words. Example: "会的，我感觉我坐不住" → Q8 positive,
        no frequency → should ask "偶尔几天还是大多数时间？"
        """
        t = text or ""
        if scale_name == "PHQ-9":
            positive_words = PHQ_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        elif scale_name == "GAD-7":
            positive_words = GAD7_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        else:
            return False
        has_positive = any(w in t for w in positive_words)
        has_frequency = any(w in t for w in FREQUENCY_WORDS)
        return has_positive and not has_frequency

    def _build_scale_context_hint(self, scale_name: str, item: int, agent_route: dict,
                                  clarify: bool = False) -> str:
        """Build a detailed scale context hint for the main model.

        Uses agent-provided scale_item_text/scoring_target/required_answer_axis
        if available, otherwise falls back to SCALE_ITEM_CORES dict.

        ``clarify`` marks a turn where the previous answer could not be scored
        (e.g. "对啊" without a frequency).  Without the explicit directive the
        model drifts to a different topic and the Runtime item stays pending
        forever, so the scale can never complete.
        """
        # Always use SCALE_ITEM_CORES as single source of truth
        cores = SCALE_ITEM_CORES.get(scale_name, {})
        core = cores.get(item, {})
        scale_item_text = core.get("scale_item_text", "")
        scoring_target = core.get("scoring_target", "")
        required_answer_axis = core.get("required_answer_axis", "")

        if not scale_item_text:
            # Last resort: use NATURAL_SCALE_QUESTIONS
            natural = NATURAL_SCALE_QUESTIONS.get((scale_name, item), "")
            if natural:
                return f"\n【隐性症状采样】如果语境自然，顺手了解：{natural}\n不要说量表、问卷、题目、评分。每轮最多一个问题。\n"
            return ""

        hint = f"""
【隐性症状采样】当前仍有一个状态点没了解完整。继续正常聊天。
[内部采样目标，不要直接暴露给用户]
量表：{scale_name}
题目：Q{item}
题目原意：{scale_item_text}
需要采集：{scoring_target}
回答轴：{required_answer_axis}
生成要求：
- 先用一句话承接用户情绪
- 然后自然问出这个症状问题
- 问句里必须出现这个症状的具体说法（例如“做事提不起劲”“心里低落”），不要用“这种不好受的状态”“这种感觉”这类含糊指代，否则来访者答的频率没法记在这一项上
- 不要说{scale_name}、量表、问卷、第几题、评分
- 不要把问题改成泛泛的"哪里不舒服""想不想聊聊"
- 不要同时问多个维度
- 本轮回复最后必须包含一个可回答的问题
"""
        if clarify:
            hint += (
                "- 上一轮回答还不足以判断程度或频率（例如只说了“有”“对”）。"
                "本轮只能围绕当前这个状态点追问：这段时间大概有几天是这样——"
                "几乎每天、一半以上、有几天，还是完全没有？"
                "不要换话题，不要同时问别的内容。\n"
            )
        return hint

    def _get_relaxation_done(self) -> bool:
        """Check if relaxation training was completed this session."""
        return bool(
            self._proactive_relaxation_offered
            or getattr(self.report, "completed_relaxation", None)
        )

    def _get_recent_dialogue_text(self, max_turns: int = 6) -> str:
        """Get recent dialogue text for agent context.

        The formatting (role labels, per-line cap, turn cap) lives in
        :func:`conversation.interview_state.format_history` so the replay
        harness renders a scripted dialogue exactly the way a live session is
        rendered.  Only the *source* differs: here it is the transcript the
        language model already owns.
        """
        if not self.llm or not hasattr(self.llm, 'conversation_history'):
            return ""
        return format_history(self.llm.conversation_history, max_turns=max_turns)

    # How many turns an item may stay "waiting" before a bare acknowledgement
    # stops counting as its answer.  Two covers the normal shape (ask, then one
    # clarifying follow-up) while cutting off the case where the conversation
    # drifted away ten turns ago and a stray "没有" lands on the stale slot.
    SCALE_MAX_WAIT_TURNS = 2
    # How many turns a single item may stay unscored before an explicit
    # frequency answer is booked against it anyway.  Without this the scale
    # deadlocks: the item cannot advance, the same question is asked again, and
    # the session ends with a partial score and no completion.
    SCALE_RESCUE_AFTER_UNSCORED = 2

    _MINIMAL_REPLY_WORDS = frozenset({        "嗯", "嗯嗯", "嗯呢", "哦", "哦哦", "噢", "好", "好的", "好吧", "好的吧",
        "行", "行吧", "可以", "还行", "对", "对啊", "是", "是的", "随便", "不知道",
        "没什么", "没事", "没事儿", "差不多", "就那样", "一般", "还好",
    })

    @classmethod
    def _is_minimal_reply(cls, text: str) -> bool:
        """True when the turn carries no new content (bare acknowledgement)."""
        cleaned = re.sub(r"[\s，,。.！!？?、；;~～…]+", "", str(text or ""))
        if not cleaned:
            return True
        if len(cleaned) <= 2:
            return True
        return cleaned in cls._MINIMAL_REPLY_WORDS

    def _update_engagement(self, user_text: str) -> None:
        if self._is_minimal_reply(user_text):
            self._minimal_reply_streak += 1
        else:
            self._minimal_reply_streak = 0

    def _build_engagement_context(self) -> str:
        """Tell the writer the current topic is exhausted."""
        streak = getattr(self, "_minimal_reply_streak", 0)
        if streak < 2:
            return ""
        if streak >= 4:
            return (
                "\n【话题已经聊完】来访者连续几轮只是应声，话题明显说尽了。"
                "本轮不要再追问原来的事情，也不要重复之前的建议；先简短回应一句，"
                "然后主动给出两个新方向让来访者挑（例如最近吃饭、身体状态、和同屋相处、"
                "平时怎么打发时间、家里人的事），或者邀请先做个放松休息一下。\n"
            )
        return (
            "\n【话题已经聊完】来访者连续两轮只是应声（如“哦哦”“好的”），说明刚才的话题说尽了。"
            "本轮不要再追问同一件事，也不要重复上一轮说过的话；先简短回应，"
            "然后换一个新方向，给一个来访者容易接话的具体问题。\n"
        )

    _QUESTION_RE = re.compile(r"[^。！？!?\n]*[？?]")

    def _record_questions(self, reply_text: str) -> None:
        """Remember the questions of one reply so they are not asked again."""
        questions = []
        for match in self._QUESTION_RE.findall(str(reply_text or "")):
            cleaned = re.sub(r"^[\s，,、。.!！?？~～…]+", "", match).strip()
            if len(cleaned) >= 6:  # ignore fragments like "不舒服吗？"
                questions.append(cleaned)
        if not questions:
            return
        merged = self._recent_questions + questions
        # keep order, drop duplicates.  The cap used to be three, which is
        # shorter than the distance between repeats in a real session: "最近有没有
        # 发生什么具体的事…让你觉得心里更堵得慌？" came back verbatim fourteen
        # turns later because the first asking had long been evicted from the
        # window that is supposed to suppress it.
        seen = []
        for q in merged:
            if q not in seen:
                seen.append(q)
        # 24 keeps a whole session's questions in view, not just the last few
        # turns: the verbatim repeat observed in the transcript had fourteen
        # turns between the two askings.
        self._recent_questions = seen[-24:]

    def _build_no_repeat_context(self) -> str:
        recent = getattr(self, "_recent_questions", []) or []
        if not recent:
            return ""
        lines = "\n".join(f"- {q}" for q in recent)
        return (
            "\n【不要重复提问】下面这些问题最近已经问过，来访者已回应或已表示不想说，"
            "本轮不要再问同一件事（换个说法再问一遍也算重复）：\n"
            f"{lines}\n"
            "要继续时只允许换一个具体角度（问影响、问当时的场景、问别人怎么看），"
            "否则直接换一个新方向。\n"
        )

    # The decision layer's intent/emotion says what the participant is doing
    # this turn, never how to answer it.  "Follow what they care about" gave the
    # writer nothing executable, so it kept falling back to a state label plus a
    # question -- which is exactly what a template sounds like out loud.  These
    # tables turn the read into one concrete landing point and one tone rule.
    _INTENT_LANDING = {
        "symptom": (
            "这一轮在说身体或症状。只挑一个维度往下问：什么时候开始的、最明显的一次"
            "是什么情形、影响到了哪件具体的事；不要一次问两个。"
        ),
        "emotion": (
            "这一轮在说感受。落到这些感受指向的那件事上；不劝、不安慰、不承诺会变好。"
        ),
        "help_seeking": (
            "这一轮在要办法。先给一个今天就能做、一句话能说清的小动作，再问他想不想试；"
            "不要只把问题反问回去。不要在这里教呼吸、放松或冥想的步骤，也不要给一串流程。"
        ),
        "chat": (
            "这一轮在闲聊。接住他提到的那件具体事物，顺着说一句；不追问隐私，"
            "也不急着把话题拐到症状上。"
        ),
        "scale": "这一轮在问问卷相关的事。把这件事说清楚就停，不展开别的方向。",
        "relaxation": (
            "这一轮提到想放松。先承认他现在绷着，再说清打开哪个练习、在左边哪里，"
            "由他决定做不做；不解释步骤，也不替他安排流程。"
        ),
        "game": "这一轮提到想玩点什么。只说游戏入口在哪，让他自己挑。",
        "end": "这一轮想收尾。简短收住，不安排新话题。",
    }
    _EMOTION_TONE = {
        "sad": "句子短、节奏慢；不追问“为什么难过”，也不把他的状态总结成一个词。",
        # "先给一点确定感" used to be read as "say one reassuring sentence
        # first", which is the preamble this turn is trying to remove.  The
        # reassurance is now placed in how the question is asked.
        "anxious": "问得具体、好回答，本身就是确定感；一轮最多一个问题。",
        "angry": "先承认他说的那件事本身，不解释、不劝。",
        "irritable": "先承认他说的那件事本身，不解释、不劝。",
        "positive": "可以正常往前推，不刻意放大他的好状态。",
    }
    #: Tone rules that mention asking have to be restated on a turn that may
    #: not ask.  "一轮最多一个问题" next to "这一轮不提问" is a permission the
    #: writer takes: measured on the fixed-text probe, an anxious ``reflect``
    #: turn still ended in a question until this line was conditioned.
    _EMOTION_TONE_NO_ASK = {
        "anxious": "说得具体、落到他刚讲的那件事上，本身就是确定感；这一轮不加问题。",
    }
    # The decision layer now answers what "continue the interview" means, not
    # just that it should continue.  Each label selects one paragraph and the
    # writer is told to do that and not the others -- a reference note was not
    # enough, because the general prompt already contains every one of these
    # moves and the writer kept choosing for itself.
    _STRATEGY_LANDING = {
        "explore": (
            "这一轮他已经自己开了一个新话题（小时候、以前的事、某个人、某件具体的事）。"
            "就着他刚提起的这个新话题问一件具体的事，不要拉回刚才那个旧话题，"
            "也不要借机问症状。"
        ),
        "deepen": (
            "这一轮不换话题。就着他刚提到的那一个细节再问一层：那是什么时候、"
            "当时身边有谁、他当时做了什么。问题只落在这个细节上，不要扩到别的事。"
        ),
        "hold": (
            "这一轮不推进话题，也不提问。用一句短话接住他刚说的那件事本身："
            "指出其中具体的东西（哪个人、什么时候、他当时做了什么），然后停住。"
            "不评价他的状态，不总结，不给建议。"
        ),
        "stabilize": (
            "这一轮把要求降到最低。用一句陈述句说明他可以先不回答，"
            "然后停下来；不要再问任何问题，也不要评价他的情绪。"
        ),
        "close": (
            "这一轮把这个话题收住：一句很短的话，然后停下，把话头留给他。"
            "不要开新话题，也不要总结成结论。"
        ),
    }
    _STANCE_RULE = {
        "probe": "这一轮必须有一个他能直接回答的问题，而且只问一个。",
        "reflect": (
            "这一轮用一句陈述句回应他刚说的那件事，句末不要有问号。"
            "也不要用“吗”“呢”“吧”这类疑问语气收尾——把问号换成句号不算做到。"
            "不要给他的状态下形容词，也不要把他的话换个说法重复一遍。"
        ),
        "summarize": "这一轮先用一句话把他说的几件事归到一起，然后停下，不要提问。",
        "quiet": "这一轮只用很短的一两句话回应，不提问、不总结、不推进。",
    }
    # The same moves, restated for a turn whose stance says "do not ask".
    # Only explore/deepen need a variant -- hold, stabilize and close never ask
    # in the first place.  Handing the writer "ask one layer deeper" together
    # with "end without a question mark" is not a trade-off it can make: on
    # Qwen3.8-27B the reply came back completely empty (immediate EOS, no
    # content and no thinking), which the pipeline then surfaced to the
    # participant as "sorry, could you say that again".
    _STRATEGY_LANDING_NO_ASK = {
        "explore": (
            "他刚提起一个新话题（小时候、以前的事、某个人、某件具体的事）。"
            "这一轮就着他刚提起的这件事说一句：点出其中具体的东西（哪个人、什么时候、"
            "他当时做了什么），不要拉回旧话题，也不要借机说症状；说完就停。"
        ),
        "deepen": (
            "这一轮不换话题。把他刚提到的那一个细节原样接住：说出那是什么时候、"
            "当时身边有谁、他当时做了什么，停在那个画面上；不要扩到别的事。"
        ),
    }
    # Which dimension the question lands on.  A strategy without a focus still
    # let the writer ask about whatever came to mind, so the interview could
    # circle "睡得怎么样" for ten turns.  Each entry is the concrete facts to
    # land on plus the abstract form to reject, because "睡眠怎么样" is the
    # phrasing a writer falls back on when it has nothing specific to say.
    #
    # The facts and the rejection are stored apart so the same dimension can be
    # rendered as a question or as a statement: "只问这个方面" next to "句末
    # 不要有问号" is the same cancelling pair that produced empty completions
    # when it was emitted for strategy wording.
    _FOCUS_LANDING = {
        "sleep": (
            "几点躺下、躺多久才睡着、半夜会不会醒、早上几点醒、白天困不困",
            "睡得怎么样",
        ),
        "mood": (
            "什么时候最难受、是发闷还是想哭、有没有突然烦起来",
            "心情好不好",
        ),
        "interest": (
            "具体是哪一件事、上一次做是什么时候、做的时候还有没有一点意思",
            "还有没有兴趣",
        ),
        "energy": (
            "一天里什么时候最没劲、做一件小事要不要歇一会儿、是不是睡醒了还是累",
            "累不累",
        ),
        "cognition": (
            "看书看不看得进去、会不会丢三落四、做决定难不难",
            "脑子不好使",
        ),
        "appetite": (
            "一天吃几顿、有没有不想吃、口味变没变、体重有没有变",
            "胃口怎么样",
        ),
        "body": (
            "头疼、胸闷、心慌、身上哪里疼或绷着，什么时候出现",
            "身体不舒服",
        ),
        "daily": (
            "一天怎么过、上班或上课还去不去、最近和谁说过话",
            "生活还正常吗",
        ),
    }

    @staticmethod
    def _posture_asks_question(strategy: str, stance: str) -> bool:
        """Does this turn end in a question?

        A "do not push" strategy and a "do not ask" stance say the same thing
        from two directions; the general instruction to open with a question
        would contradict both, so it is only emitted when the posture actually
        wants one.
        """
        if stance == "probe":
            return True
        if stance in ("reflect", "summarize", "quiet"):
            return False
        if strategy in ("hold", "close", "stabilize"):
            return False
        return True

    def _build_jev_judgement_context(self, decision: Optional[TurnDecision] = None) -> str:
        """Turn the decision model's read of the turn into wording constraints.

        The Jev/Tacit checkpoint answers four questions per turn.  Only its
        action used to survive into the reply, so the decision layer looked
        idle even when it ran; passing intent/emotion as bare "reference" was
        still too thin to change a sentence.  Each read now maps to a landing
        point and a tone rule, and the confidence travels along so a weak read
        is visibly weak rather than silently authoritative.

        ``decision`` is the *approved* action.  On a task turn (clarify, scale,
        invitation, ending) the task itself already dictates what to say, so
        only the tone rule is passed; handing the writer a conversational
        landing point there produced two conflicting instructions -- "he is
        chatting, follow his topic" next to "confirm what he just said".
        """
        outcome = getattr(self, "_last_decision_outcome", None)
        raw = getattr(outcome, "decision", None) if outcome is not None else None
        if raw is None:
            return ""

        intent_zh = {
            "chat": "闲聊",
            "symptom": "在描述症状或身体感受",
            "emotion": "在表达情绪",
            "help_seeking": "在寻求帮助或建议",
            "scale": "在问评估或问卷相关的事",
            "relaxation": "想放松一下",
            "game": "想玩点什么",
            "end": "想结束这次谈话",
        }
        emotion_zh = {
            "calm": "平静",
            "sad": "低落",
            "anxious": "焦虑",
            "angry": "愤怒",
            "irritable": "烦躁",
            "positive": "积极",
        }

        intent = str(getattr(raw, "intent_jev", "") or "").strip().lower()
        emotion = str(getattr(raw, "emotion", "") or "").strip().lower()
        confidence = float(getattr(raw, "confidence", 0.0) or 0.0)

        read: list[str] = []
        if intent and intent != "unclear":
            read.append(f"他说的话属于“{intent_zh.get(intent, intent)}”")
        if emotion and emotion not in ("uncertain", ""):
            read.append(f"语气偏{emotion_zh.get(emotion, emotion)}")
        if not read:
            return ""

        lines = [
            "",
            "【决策层对本轮的判断（内部参考，不要复述这段文字）】",
            "；".join(read) + f"（置信度 {confidence:.2f}）。",
        ]
        action = getattr(decision, "action", None)
        task_turn = action is not None and action is not TurnAction.CHAT
        strategy = str(getattr(decision, "strategy", "") or "").strip().lower()
        stance = str(getattr(decision, "stance", "") or "").strip().lower()
        focus = str(getattr(decision, "focus", "") or "").strip().lower()
        if not task_turn:
            asks_question = self._posture_asks_question(strategy, stance)
            lines.append("【这一轮怎么开口】")
            lines.append(
                "- 不要写“复述他的话 + 一句评价”这种句子，也不要先给他的状态一个评价"
                "再开口；开口第一句就落在那件事的具体细节上。"
            )
            lines.append(
                "- 判断标准：第一句里如果出现了“把他的情况概括成一个说法 + 一句评价”"
                "这个组合（例如以“那种……的感觉”起头，后面接一句“确实/挺……的”），"
                "就整句删掉，直接说那件事本身。"
            )
            strategy_rule = self._STRATEGY_LANDING.get(strategy)
            if strategy_rule and not asks_question:
                # "Ask one layer deeper" plus "do not ask anything" is not a
                # preference the writer can trade off -- it is two orders that
                # cancel out.  Measured on Qwen3.8-27B: the reply came back
                # empty (EOS immediately, no content, no thinking), which the
                # pipeline surfaced as "sorry, could you say that again".
                strategy_rule = self._STRATEGY_LANDING_NO_ASK.get(strategy, strategy_rule)
            stance_rule = self._STANCE_RULE.get(stance)
            if asks_question:
                lines.append(
                    "- 第一句不要复述他刚说过的话；直接问没提到的那个时间、场景、动作或身体感受。"
                )
            if strategy_rule:
                # Strategy outranks the intent landing: intent says what he is
                # doing, strategy says what this turn must do about it.  Passing
                # both gave the writer two competing plans.
                lines.append(f"- 本轮只做这一件事：{strategy_rule}")
            else:
                landing = self._INTENT_LANDING.get(intent)
                if landing:
                    lines.append(f"- {landing}")
            focus_rule = self._FOCUS_LANDING.get(focus)
            if focus_rule and asks_question:
                # A bullet among bullets was read as advice.  The dedicated
                # block is the pattern that worked for the scale close-out: it
                # is short, it has its own heading, and it names one thing.
                facts, abstract = focus_rule
                lines.append("")
                lines.append(f"【本轮问哪一方面】{_focus_name(focus)}")
                lines.append(f"- 只问这个方面：{facts}。")
                lines.append(
                    f"- 不要用“{abstract}”这种笼统问法代替；这一轮不要问别的方面。"
                )
                asked_before = self._focus_counts.get(focus, 0)
                if asked_before >= 2:
                    lines.append(
                        f"- 这个方面前面已经问过 {asked_before} 次了，这次要问一个新的角度，"
                        "不要重复上一回的问法。"
                    )
            if stance_rule:
                lines.append(f"- 收尾方式：{stance_rule}")
            if asks_question:
                lines.append(
                    "- 开头方式和上一轮错开：上一轮先问，这轮就先接一句；上一轮先接，这轮就直接问。"
                )
            else:
                lines.append(
                    "- 这一轮不抛问题；接着他说的内容往下说一句，说完就停。"
                )
        tone = self._EMOTION_TONE.get(emotion)
        if tone and not task_turn and not self._posture_asks_question(strategy, stance):
            tone = self._EMOTION_TONE_NO_ASK.get(emotion, tone)
        if tone:
            lines.append(f"- 语气：{tone}")
        if not task_turn and not self._posture_asks_question(strategy, stance):
            # The general prompt tells the writer that most chat turns must
            # move the topic forward.  Without an explicit precedence line that
            # rule wins and the posture is silently ignored -- which is how the
            # decision layer stayed decorative.
            lines.append(
                "- 这一轮不要替他把状态总结成一个形容词再评价它。"
                "要接就把话落在他说的那件事上（谁、什么时候、做了什么），"
                "不要落在他的状态上（多累、多难、多不容易）。"
            )
            lines.append(
                "- 优先级：上面这一轮的策略优先于一般聊天规则。即使一般规则要求"
                "“把话题往前带一步”，这一轮也不追问、不加问题。"
            )
        return "\n".join(lines) + "\n"

    def _build_decision_language_context(
        self, decision: TurnDecision, runtime: Any
    ) -> str:
        """Describe an approved action for language realization only."""
        action_text = {
            TurnAction.CHAT: "自然回应来访者",
            TurnAction.CLARIFY_INPUT: "先确认来访者刚才表达中的关键信息",
            TurnAction.OFFER_SCALE: "询问来访者是否愿意做一份简短的问卷了解",
            TurnAction.OFFER_RELAXATION: "询问来访者是否愿意尝试一次放松练习",
            TurnAction.START_SCALE: "自然提出系统选定的当前了解问题",
            TurnAction.CONTINUE_SCALE: "自然承接并继续系统选定的当前了解问题",
            TurnAction.PAUSE_SCALE: "温和确认暂停，不继续追问",
            TurnAction.RECOMMEND_RELAXATION: "自然邀请进入放松中心",
            TurnAction.RECOMMEND_GAME: "自然引导进入游戏选择页",
            TurnAction.END_SESSION: "按照会话流程自然完成结束表达",
        }.get(decision.action, "自然回应来访者")
        lines = [
            "【本轮已确定的表达任务】",
            f"系统已经确定：{action_text}。不要重新选择其他任务，也不要自行推进状态。",
        ]
        if decision.action is TurnAction.OFFER_SCALE:
            # The invitation must stay an invitation.  Starting the first item
            # here would skip the participant's consent entirely.
            lines.append(
                "只提出邀请并说明大概需要几分钟，然后等来访者回答。"
                "不要念出第一道题，不要说出量表名称或评分方式，不要假设对方已经同意。"
            )
            lines.append("如果来访者拒绝，就自然回到访谈，不再追问。")
            return "\n".join(lines)
        if decision.action is TurnAction.OFFER_RELAXATION:
            lines.append(
                "只提出邀请并等来访者回答，不要指定呼吸、肌肉或冥想等具体方式，"
                "不要描述练习步骤，不要假设对方已经同意。"
            )
            lines.append("如果来访者拒绝，就自然回到访谈，不再追问。")
            return "\n".join(lines)
        if decision.action in (TurnAction.START_SCALE, TurnAction.CONTINUE_SCALE):
            scale_name = runtime.active_scale or decision.scale_name or "当前了解内容"
            item = runtime.current_item
            if decision.action is TurnAction.CONTINUE_SCALE and not runtime.active_scale:
                lines.append("当前量表项目已经完成；只需简短承接完成，不要继续提出下一题。")
                return "\n".join(lines)
            question = NATURAL_SCALE_QUESTIONS.get((scale_name, item or 1), "")
            if question:
                lines.append(f"当前需要自然表达的问题是：{question}")
            lines.append("只围绕这个问题说话；回答的解释和进度由系统处理。")
        elif decision.action is TurnAction.CLARIFY_INPUT:
            target_text = {
                "night_waking": "晚上中途是否会醒",
                "frequency": "发生频率",
            }.get(decision.semantic_target or "", "刚才表达中的关键信息")
            lines.append(f"只确认：{target_text}。不要猜测、打分或推进其他业务任务。")
        elif decision.action is TurnAction.RECOMMEND_RELAXATION:
            if decision.intervention_type:
                lines.append(
                    "来访者明确提到了一种偏好；只把它作为进入 Relaxation Center 后的高亮偏好，"
                    "不要自动开始，也不要把它说成系统替来访者选择。"
                )
            else:
                lines.append(
                    "只邀请来访者进入 Relaxation Center 自己选择是否休息和选择内容，"
                    "不要指定呼吸、肌肉、冥想或其他具体活动。"
                )
        elif decision.action is TurnAction.RECOMMEND_GAME:
            lines.append(
                "只引导来访者进入 Relaxation Center 的 Games 页面，等待其自行选择，"
                "不要指定或自动开始某个游戏。"
            )
        elif decision.action is TurnAction.PAUSE_SCALE:
            lines.append("尊重来访者的暂停请求，保持简短，不要求回答当前问题。")
        elif decision.action is TurnAction.END_SESSION:
            lines.append("保持温和简短，不补问量表，不强行安排其他活动。")
        return "\n".join(lines)

    def _build_system_suffix(
        self,
        text: str,
        *,
        needs_rag: bool = True,
        round_count: int | None = None,
        allow_user_relaxation: bool = False,
    ) -> str:
        """Build decision-approved language context plus optional RAG text."""
        system_suffix = ""
        current_rounds = (
            int(round_count)
            if round_count is not None
            else (self.report.get_round_count() if self.report else 0)
        )

        # Eligibility and lifecycle rules belong to TurnPolicy/SessionEngine;
        # the language model receives no round-count policy warning.
        del current_rounds, allow_user_relaxation

        if needs_rag and self.rag:
            # Use multi-turn context for RAG retrieval
            rag_text = text
            if self.llm and hasattr(self.llm, 'conversation_history'):
                recent = [m["content"] for m in self.llm.conversation_history[-6:]
                          if m.get("role") == "user"]
                if recent:
                    # Deduplicate repeated greetings
                    cleaned_recent = []
                    for r in recent[-3:]:
                        normalized = r.strip("。！？!?,， ").lower()
                        if normalized in {"你好", "你好呀", "嗨", "哈喽"}:
                            if not any(c.strip("。！？!?,， ").lower() == normalized for c in cleaned_recent):
                                cleaned_recent.append(r)
                        else:
                            cleaned_recent.append(r)
                    rag_text = "\n".join(cleaned_recent + [text])
            try:
                rag_suffix = self.rag.get_system_suffix(rag_text, enabled=True)
            except TypeError:
                # Legacy test doubles/adapters still expose the one-argument
                # surface; the caller has already enforced the decision gate.
                rag_suffix = self.rag.get_system_suffix(rag_text)
            # Truncate RAG — tighter when active scale or positive_pending
            runtime = self.scale_runtime.snapshot()
            _rag_active = runtime.active_scale or (
                runtime.waiting_for_answer
                and runtime.active_scale
                and runtime.current_item
                and self._is_positive_pending_frequency(
                    runtime.active_scale, runtime.current_item, text
                )
            )
            max_rag = 200 if _rag_active else 1200
            if rag_suffix and len(rag_suffix) > max_rag:
                rag_suffix = rag_suffix[:max_rag] + "\n【知识库已截断】"
            query_hash = hashlib.sha256(rag_text.encode("utf-8")).hexdigest()[:16]
            logger.info(
                "[RagDebug] query_hash=%s rag_query_hash=%s query_length=%d rag_query_length=%d "
                "injected=%s suffix_length=%d",
                hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                query_hash,
                len(text),
                len(rag_text),
                bool(rag_suffix),
                len(rag_suffix) if rag_suffix else 0,
            )
            if rag_suffix:
                system_suffix += "\n" + rag_suffix

        return system_suffix

    def _ensure_sentence_flush_state(self) -> None:
        """Lazily initialize watchdog state for lightweight test doubles."""
        if not hasattr(self, "_sentence_segmenter_factory"):
            self._sentence_segmenter_factory = SentenceSegmenter
        if not hasattr(self, "_flush_condition"):
            self._flush_condition = threading.Condition()
        if not hasattr(self, "_active_flush_registration"):
            self._active_flush_registration = None
        if not hasattr(self, "_flush_watchdog_thread"):
            self._flush_watchdog_thread = None
        if not hasattr(self, "_flush_watchdog_shutdown"):
            self._flush_watchdog_shutdown = threading.Event()

    def _new_sentence_segmenter(self) -> SentenceSegmenter:
        """Build a segmenter, with a narrow factory seam for deadline tests."""
        factory = getattr(self, "_sentence_segmenter_factory", SentenceSegmenter)
        return factory()

    def _register_sentence_flush(
        self,
        generation_id: int,
        segmenter: SentenceSegmenter,
        emit: Callable[[str, Any], None],
        timing: Optional[dict] = None,
    ) -> _SentenceFlushRegistration:
        """Replace the one active watchdog registration by object identity."""
        self._ensure_sentence_flush_state()
        registration = _SentenceFlushRegistration(generation_id, segmenter, emit, timing=timing)
        with self._flush_condition:
            self._active_flush_registration = registration
            self._flush_condition.notify_all()
        return registration

    def _start_sentence_flush_watchdog(self) -> None:
        """Start the single watchdog only once buffered text needs a deadline."""
        self._ensure_sentence_flush_state()
        with self._flush_condition:
            if self._flush_watchdog_shutdown.is_set():
                return
            watchdog = self._flush_watchdog_thread
            if watchdog is None or not watchdog.is_alive():
                watchdog = threading.Thread(
                    target=self._run_sentence_flush_watchdog,
                    name="sentence-flush-watchdog",
                    daemon=True,
                )
                self._flush_watchdog_thread = watchdog
                watchdog.start()
            self._flush_condition.notify_all()

    def _is_active_flush_registration(
        self,
        registration: _SentenceFlushRegistration,
    ) -> bool:
        with self._flush_condition:
            return (
                self._active_flush_registration is registration
                and not self._flush_watchdog_shutdown.is_set()
            )

    def _retire_flush_registration(
        self,
        registration: _SentenceFlushRegistration,
    ) -> bool:
        """Clear only this registration; never clear a newer generation."""
        with self._flush_condition:
            if self._active_flush_registration is not registration:
                return False
            self._active_flush_registration = None
            self._flush_condition.notify_all()
            return True

    def _wake_sentence_flush_watchdog(self) -> None:
        with self._flush_condition:
            self._flush_condition.notify_all()

    @staticmethod
    def _set_flush_deadline_locked(
        registration: _SentenceFlushRegistration,
        now: Optional[float] = None,
    ) -> None:
        """Refresh the registration deadline from the segmenter's latest feed."""
        if registration.segmenter.buffered_text:
            current = time.monotonic() if now is None else now
            registration.next_deadline = (
                current + registration.segmenter.max_wait_ms / 1000.0
            )
        else:
            registration.next_deadline = None

    def _emit_registered_segment_events_locked(
        self,
        registration: _SentenceFlushRegistration,
        events: list[SentenceReady],
    ) -> None:
        """Serialize segmenter mutation and emission for one registration."""
        if events and registration.timing is not None and "first_sentence_ms" not in registration.timing:
            started = registration.timing.get("_dialogue_started_at")
            if started is not None:
                registration.timing["first_sentence_ms"] = (time.perf_counter() - started) * 1000.0
        for event in events:
            self._emit_generation_sentence(event, registration.emit)

    def _feed_sentence_registration(
        self,
        registration: _SentenceFlushRegistration,
        content: str,
    ) -> bool:
        """Feed one provider chunk under the registration's serialization lock."""
        with registration.lock:
            if not self._is_active_flush_registration(registration):
                return False
            if not self.delivery_controller.is_current(registration.generation_id):
                self._retire_flush_registration(registration)
                return False
            now = time.monotonic()
            events = registration.segmenter.feed(
                registration.generation_id,
                content,
            )
            self._emit_registered_segment_events_locked(registration, events)
            due_events = registration.segmenter.flush_if_due(
                registration.generation_id,
                now=now,
            )
            self._emit_registered_segment_events_locked(registration, due_events)
            self._set_flush_deadline_locked(registration, now)
            needs_watchdog = bool(registration.segmenter.buffered_text)
        if needs_watchdog:
            self._start_sentence_flush_watchdog()
        self._wake_sentence_flush_watchdog()
        return True

    def _run_sentence_flush_watchdog(self) -> None:
        """Enforce one active segmenter's deadline without provider threads."""
        while True:
            with self._flush_condition:
                if self._flush_watchdog_shutdown.is_set():
                    return
                registration = self._active_flush_registration
                if registration is None:
                    self._flush_condition.wait(timeout=0.5)
                    continue

            stale = False
            has_buffer = False
            deadline: Optional[float] = None
            with registration.lock:
                if not self._is_active_flush_registration(registration):
                    continue
                if not self.delivery_controller.is_current(registration.generation_id):
                    stale = True
                else:
                    has_buffer = bool(registration.segmenter.buffered_text)
                    if has_buffer:
                        if registration.next_deadline is None:
                            self._set_flush_deadline_locked(registration)
                        deadline = registration.next_deadline
                    else:
                        registration.next_deadline = None

            if stale:
                self._retire_flush_registration(registration)
                continue
            if not has_buffer or deadline is None:
                with self._flush_condition:
                    if self._flush_watchdog_shutdown.is_set():
                        return
                    if self._active_flush_registration is registration:
                        self._flush_condition.wait(timeout=0.5)
                continue

            wait_for = max(0.0, deadline - time.monotonic())
            if wait_for > 0:
                with self._flush_condition:
                    if self._flush_watchdog_shutdown.is_set():
                        return
                    if self._active_flush_registration is registration:
                        self._flush_condition.wait(timeout=wait_for)
                continue

            with registration.lock:
                if not self._is_active_flush_registration(registration):
                    continue
                if not self.delivery_controller.is_current(registration.generation_id):
                    self._retire_flush_registration(registration)
                    continue
                now = time.monotonic()
                events = registration.segmenter.flush_if_due(
                    registration.generation_id,
                    now=now,
                )
                self._emit_registered_segment_events_locked(registration, events)
                if registration.segmenter.buffered_text:
                    # A short (< min_stable_chars) buffer is intentionally not
                    # forced into TTS; retry on a bounded deadline instead of
                    # spinning when a test injects max_wait_ms=0.
                    retry_ms = max(registration.segmenter.max_wait_ms, 10)
                    registration.next_deadline = now + retry_ms / 1000.0
                else:
                    registration.next_deadline = None

    def _finalize_sentence_flush_registration(
        self,
        registration: _SentenceFlushRegistration,
    ) -> None:
        """Finalize one stream without clearing a replacement registration."""
        with registration.lock:
            if not self._is_active_flush_registration(registration):
                return
            if not self.delivery_controller.is_current(registration.generation_id):
                self._retire_flush_registration(registration)
                return
            self._retire_flush_registration(registration)
            events = registration.segmenter.flush(registration.generation_id)
            self._emit_registered_segment_events_locked(registration, events)

    def _shutdown_sentence_flush_watchdog(self, timeout: float = 2.0) -> None:
        """Stop the single pipeline watchdog with a bounded join."""
        self._ensure_sentence_flush_state()
        with self._flush_condition:
            self._flush_watchdog_shutdown.set()
            self._active_flush_registration = None
            watchdog = self._flush_watchdog_thread
            self._flush_condition.notify_all()
        if watchdog and watchdog is not threading.current_thread():
            watchdog.join(timeout=timeout)
        with self._flush_condition:
            if self._flush_watchdog_thread is watchdog and watchdog is not None:
                if not watchdog.is_alive():
                    self._flush_watchdog_thread = None

    def _ensure_delivery_generation(self, generation_id: int):
        record = self.delivery_controller.get_record(generation_id)
        if record is None:
            # Compatibility callers may provide an ID without constructing the
            # shared MainWindow controller first.  The controller remains the
            # only allocator; the supplied value is never treated as policy.
            record = self.delivery_controller.start_generation()
        starter = getattr(self.delivery_queue, "start", None)
        if callable(starter):
            starter()
        return record

    def _emit_generation_sentence(
        self,
        event: SentenceReady,
        emit: Callable[[str, Any], None],
    ) -> None:
        """Guard one stable sentence, then use it for both UI and TTS."""
        self._ensure_guard_state()
        context = self._guard_contexts.get(
            event.generation_id,
            GuardContext(generation_id=event.generation_id),
        )
        state = self._guard_states.setdefault(event.generation_id, GenerationGuardState())
        guard_result = self._pre_delivery_guard.evaluate(
            event.text,
            context=context,
            state=state,
        )
        if guard_result.status != "ALLOW":
            logger.warning(
                "[PreDeliveryGuard] blocked generation=%s seq=%s reason=%s",
                event.generation_id,
                event.seq,
                guard_result.reason,
            )
            if (
                state.delivered_sentence_count == 0
                and not state.fallback_emitted
                and self.delivery_controller.is_current(event.generation_id)
            ):
                fallback_text = self._guard_fallbacks.get(
                    event.generation_id,
                    "嗯，我在听着呢。[breath]你可以慢慢说。",
                )
                fallback_display = clean_for_display(fallback_text)
                fallback_tts = clean_for_tts(fallback_text)
                if fallback_display or fallback_tts:
                    fallback_event = SentenceReady(
                        event.generation_id,
                        event.seq,
                        fallback_display or fallback_tts,
                        fallback_tts,
                    )
                    if self.delivery_controller.is_current(event.generation_id) and self.delivery_queue.enqueue(fallback_event):
                        state.fallback_emitted = True
                        state.delivered_sentence_count += 1
                        emit("stream_text", fallback_event)
            return
        normalized_tts = clean_for_tts(guard_result.text)
        normalized_display = clean_for_display(guard_result.text)
        if not normalized_display and not normalized_tts:
            return
        normalized = SentenceReady(
            event.generation_id,
            event.seq,
            normalized_display or normalized_tts,
            normalized_tts,
        )
        if not self.delivery_controller.is_current(event.generation_id):
            return
        # Queue first so a worker cannot run against a sentence that was never
        # accepted by the generation owner.  UI commits visibility separately.
        if self.delivery_queue.enqueue(normalized):
            state.delivered_sentence_count += 1
            emit("stream_text", normalized)

    def _emit_generation_text(
        self,
        generation_id: int,
        text: str,
        emit: Callable[[str, Any], None],
    ) -> None:
        """Emit fallback text through the same sentence delivery path."""
        self._ensure_delivery_generation(generation_id)
        self.delivery_ledger.record_generated(generation_id, text)
        segmenter = self._new_sentence_segmenter()
        for event in segmenter.feed(generation_id, text):
            self._emit_generation_sentence(event, emit)
        for event in segmenter.flush(generation_id):
            self._emit_generation_sentence(event, emit)

    def _discard_cancelled_user_entry(
        self,
        generation_id: int,
        rollback_user_entry,
        *,
        deferred_history_supported: bool,
        stream_failed: bool = False,
    ) -> None:
        """Drop a pending user turn whose response never reached the participant.

        History policy per generation outcome:

        - delivered complete: ledger commits user + full assistant reply;
        - partial delivery (cancelled or failed mid-stream): ledger commits
          user + the delivered prefix as the assistant reply;
        - cancelled/failed with nothing delivered: the pending user entry is
          rolled back so no orphan user message pollutes later context;
        - a superseded (stale) generation never commits history once a newer
          generation exists (enforced by the delivery ledger).
        """
        if not deferred_history_supported or not callable(rollback_user_entry):
            return
        record = self.delivery_controller.get_record(generation_id)
        stale = record is None or record.cancelled or (
            not self.delivery_controller.is_current(generation_id)
        )
        if not (stale or stream_failed):
            return
        if self.delivery_ledger.delivered_text(generation_id):
            # Something was already spoken/shown: the ledger finalization will
            # commit the delivered prefix, so the user turn stays.
            return
        try:
            rollback_user_entry()
        except Exception:
            logger.warning("[Pipeline] failed to roll back cancelled user entry")

    def _stream_llm(
        self,
        text: str,
        system_suffix: Optional[str],
        emit: Callable[[str, Any], None],
        *,
        generation_id: Optional[int] = None,
        timing: Optional[dict] = None,
    ):
        """Stream provider text and normalize it without business parsing.

        Compatibility callers without ``generation_id`` retain the Phase 6
        complete-text event surface.  The live MainWindow path supplies an ID:
        chunks are then assembled into stable sentence events and queued for
        TTS before provider completion.
        """
        self._last_stream_generation_id = generation_id
        if generation_id is None:
            stream_started = time.perf_counter()
            generated_parts: list[str] = []
            for chunk in self.llm.chat(text, system_suffix=system_suffix):
                content = str(chunk or "")
                if content and timing is not None and "dialogue_ttft_ms" not in timing:
                    timing["dialogue_ttft_ms"] = (time.perf_counter() - stream_started) * 1000.0
                generated_parts.append(content)
            generated_text = "".join(generated_parts)
            built = ResponseBuilder.build(generated_text)
            if not built.tts_text.strip():
                logger.warning(
                    "generated text empty after normalization; "
                    f"{text_meta('head', generated_text, limit=300)}"
                )
                generated_text = make_safe_fallback_reply(text)
                built = ResponseBuilder.build(generated_text)
            if built.spoken_text:
                emit("stream_text", built.spoken_text)
            return generated_text, built.analysis_text, built.tts_text

        self._ensure_delivery_generation(generation_id)
        segmenter = self._new_sentence_segmenter()
        registration = self._register_sentence_flush(
            generation_id,
            segmenter,
            emit,
            timing=timing,
        )
        stream_started = time.perf_counter()
        if timing is not None:
            timing["_dialogue_started_at"] = stream_started
        generated_parts: list[str] = []
        deferred_history_supported = True
        history = getattr(self.llm, "conversation_history", None)
        history_before = len(history) if isinstance(history, list) else 0
        chunks = None
        rollback_user_entry = None
        stream_failed = False
        try:
            try:
                chunks = self.llm.chat(
                    text,
                    system_suffix=system_suffix,
                    commit_history=False,
                )
            except TypeError:
                # Older test/integration adapters may not expose the optional
                # history-defer keyword.  Their transport still remains usable;
                # delivery finalization below controls the production history.
                deferred_history_supported = False
                chunks = self.llm.chat(text, system_suffix=system_suffix)
            rollback_user_entry = getattr(chunks, "rollback_user_entry", None)
            try:
                for chunk in chunks:
                    if not self.delivery_controller.is_current(generation_id):
                        break
                    content = str(chunk or "")
                    if not content:
                        continue
                    if timing is not None and "dialogue_ttft_ms" not in timing:
                        timing["dialogue_ttft_ms"] = (time.perf_counter() - stream_started) * 1000.0
                    generated_parts.append(content)
                    self.delivery_ledger.record_generated(generation_id, content)
                    if not self._feed_sentence_registration(registration, content):
                        break
            finally:
                # Deterministically terminate the provider stream: closing the
                # generator raises GeneratorExit inside the adapter, releasing
                # the HTTP response and decoder.  This stops client-side
                # consumption only; whether the model server also stops
                # decoding is a server-side property and is not asserted here.
                close = getattr(chunks, "close", None)
                if callable(close):
                    close()
        except BaseException:
            stream_failed = True
            raise
        finally:
            self._finalize_sentence_flush_registration(registration)
            self._discard_cancelled_user_entry(
                generation_id,
                rollback_user_entry,
                deferred_history_supported=deferred_history_supported,
                stream_failed=stream_failed,
            )

        generated_text = "".join(generated_parts)
        if not deferred_history_supported and isinstance(history, list):
            # Compatibility adapters may append the full assistant response
            # themselves. Remove that unscoped projection; the UI ledger will
            # commit delivered_text exactly once at finish_streaming.
            while len(history) > history_before and history[-1].get("role") == "assistant":
                history.pop()
        built = ResponseBuilder.build(generated_text)
        if not built.tts_text.strip():
            logger.warning(
                "generated text empty after normalization; "
                f"{text_meta('head', generated_text, limit=300)}"
            )
            generated_text = make_safe_fallback_reply(text)
            self.delivery_ledger.record_generated(generation_id, generated_text)
            built = ResponseBuilder.build(generated_text)
            fallback_segmenter = self._new_sentence_segmenter()
            for event in fallback_segmenter.feed(generation_id, generated_text):
                self._emit_generation_sentence(event, emit)
            for event in fallback_segmenter.flush(generation_id):
                self._emit_generation_sentence(event, emit)

        return generated_text, built.analysis_text, built.tts_text
