# Pipeline - Unified conversation pipeline and shared constants

import re
import time
import traceback
from typing import Optional, Any, Callable, List, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger import get_logger
from services.metrics import get_metrics
from config import (
    MIN_ROUNDS_BEFORE_SCALE,
    AGENT_ROUTE_ENABLED,
    AGENT_ROUTE_COOLDOWN_ROUNDS,
)

logger = get_logger(__name__)


_RELAXATION_TYPE_ALIASES = {
    "breathing": "breathing",
    "mindfulness": "meditation",
    "meditation": "meditation",
    "muscle": "muscle",
    "muscle_relaxation": "muscle",
    "progressive_muscle_relaxation": "muscle",
}


def _normalize_relaxation_type(value) -> str:
    """Map agent intervention labels to the UI/media contract."""
    return _RELAXATION_TYPE_ALIASES.get(str(value or "").strip().lower(), "breathing")


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
    score_symptom_signals,
)

# Scale administration state is owned by assessment.ScaleRuntime.  This
# module only re-exports pure scoring helpers and keeps observation counters.
from assessment import ScaleAnswerInterpreter, ScaleRuntime
from services.scales import get_scale_manager
from conversation.contracts import (
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import collect_turn_signals


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

# Common ASR errors in psychological counseling context
# Maps (wrong_text, context_hint) → corrected_text
_ASR_CORRECTIONS = {
    # Frequency errors
    "进场": "经常",
    "进场很": "经常很",
    "金蝉": "经常",
    "井场": "经常",
    # Duration errors
    "带三个星期": "大概三个星期",
    "带两周": "大概两周",
    "代两周": "大概两周",
    # Sleep errors
    "中途不醒": "中途会醒",
    "中途部醒": "中途会醒",
    "不睡不着": "睡不着",
    # General
    "叫我了": "焦虑了",
    "久了": "久了",  # keep as-is, context dependent
    "心慌": "心慌",  # keep as-is
}


def correct_asr_text(raw_text: str, recent_context: str = "") -> tuple:
    """Post-process ASR output to fix common speech recognition errors.

    Returns (corrected_text, corrections_list).
    """
    if not raw_text:
        return raw_text, []

    corrected = raw_text
    corrections = []

    # Apply known corrections
    for wrong, right in _ASR_CORRECTIONS.items():
        if wrong == right:
            continue
        if wrong in corrected:
            corrected = corrected.replace(wrong, right)
            corrections.append(f"{wrong}→{right}")

    # Context-aware corrections
    # Single "又" in short response → likely "有" (yes/have)
    if corrected.strip() == "又":
        corrected = "有"
        corrections.append("又→有")

    if corrections:
        logger.warning(f"[ASRCorrect] raw={raw_text!r} corrected={corrected!r} fixes={corrections}")

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
    relaxation_rec: Optional[str] = None    # 'breathing', 'muscle', 'meditation', 'game'
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
                 session_emotions: list, emotion_tracker=None,
                 turn_policy: TurnPolicy | None = None,
                 session_state_provider: Callable[[], Any] | None = None):
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
        self._post_scale_relaxation_done: bool = False  # True after post-scale relaxation recommended
        self._relaxation_recommended_this_session: set = set()  # track which types recommended
        self._game_recommended_this_session: bool = False  # track if game was recommended
        self._pending_relaxation_after_scale: Optional[str] = None  # hold relaxation until scale done
        self._relaxation_candidate: Optional[str] = None  # agent-proposed candidate for current turn
        self._game_candidate: bool = False  # agent-proposed game for current turn
        self._agent_route_cooldown: int = 0         # cooldown after agent route failure

        # Shared executor for parallel intent / emotion classification.
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
        """Reset per-session state without touching unrelated services."""
        self.scale_runtime.reset()
        self.symptom_scores = {
            name: 0 for name in self._scale_manager.get_scale_names()
        }
        self.symptom_turns = 0
        self._agent_route_cooldown = 0
        self._post_scale_relaxation_done = False
        self._relaxation_recommended_this_session.clear()
        self._game_recommended_this_session = False
        self._pending_relaxation_after_scale = None
        self._relaxation_candidate = None
        self._game_candidate = False

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

    def _build_turn_snapshot(
        self,
        *,
        round_count: int,
        time_limit_reached: bool,
        session_state: str = "CHATTING",
    ) -> TurnStateSnapshot:
        runtime = self.scale_runtime.snapshot()
        return TurnStateSnapshot(
            session_state=self._session_state_name(session_state),
            round_count=max(0, int(round_count)),
            active_scale=runtime.active_scale,
            current_item=runtime.current_item,
            waiting_for_answer=runtime.waiting_for_answer,
            completed_scales=self._completed_scale_names(),
            relaxation_used=bool(getattr(self.report, "completed_relaxation", None)),
            game_active=False,
            time_limit_reached=bool(time_limit_reached),
        )

    def _request_router_proposal(
        self,
        *,
        user_text: str,
        current_rounds: int,
    ) -> tuple[RouterProposal, dict]:
        """Obtain one non-executable proposal from the configured Router."""
        fallback = RouterProposal.fallback("router_fallback")
        if not AGENT_ROUTE_ENABLED:
            logger.warning("[AgentRoute] skipped: AGENT_ROUTE_ENABLED=False")
            return fallback, fallback.model_dump(mode="json")
        if self._agent_route_cooldown > 0:
            self._agent_route_cooldown -= 1
            logger.warning(f"[AgentRoute] cooldown remaining={self._agent_route_cooldown}")
            return fallback, fallback.model_dump(mode="json")
        if not self.agent or not self.agent.is_available():
            return fallback, fallback.model_dump(mode="json")

        try:
            kwargs = {
                "user_text": user_text,
                "recent_history": self._get_recent_dialogue_text(),
                "current_round": current_rounds,
                "active_scale": self.scale_runtime.snapshot().active_scale,
                "collected_scales": {
                    name: dict(answers)
                    for name, answers in self.scale_runtime.snapshot().answers_by_scale.items()
                },
                "relaxation_done": self._get_relaxation_done(),
            }
            if hasattr(self.agent, "route_proposal"):
                raw = self.agent.route_proposal(**kwargs)
            else:
                raw = self.agent.route_conversation_actions(**kwargs)
            proposal = raw if isinstance(raw, RouterProposal) else RouterProposal.from_legacy_route(raw)
            self._agent_route_cooldown = 0
            logger.warning(
                f"[AgentRoute] user={user_text!r} action={proposal.action.value} "
                f"scale={proposal.scale_name} confidence={proposal.confidence} "
                f"reason={proposal.reason[:60]!r}"
            )
            return proposal, proposal.model_dump(mode="json")
        except Exception as exc:
            logger.warning(f"[AgentRoute] failed: {exc}")
            self._agent_route_cooldown = AGENT_ROUTE_COOLDOWN_ROUNDS
            return fallback, fallback.model_dump(mode="json")

    def _apply_turn_decision(self, decision: TurnDecision, result: PipelineResult) -> None:
        """Apply an already-authoritative decision; never choose another action."""
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
        elif decision.action is TurnAction.PAUSE_SCALE:
            if self.scale_runtime.snapshot().active_scale:
                self.scale_runtime.pause()
        elif decision.action is TurnAction.RECOMMEND_RELAXATION:
            self._relaxation_candidate = _normalize_relaxation_type(decision.intervention_type)
            self._pending_relaxation_after_scale = None
        elif decision.action is TurnAction.RECOMMEND_GAME:
            self._game_candidate = True
            self._game_recommended_this_session = True
        elif decision.action is TurnAction.END_SESSION:
            result.end_type = {
                "time_limit": "time_limit",
                "user_explicit": "quit",
                "router_proposal": "quit",
            }.get(decision.end_reason or "", "quit")

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
                f"评分标准：{options_text}\n"
                f"记录规则：以 [SCALE:{scale_name}:Q题号:S分数] 格式嵌入回复末尾。"
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
            if callable(duration_getter):
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
            result.user_text = self.transcribe(config.audio_data, emit)
            if not result.user_text.strip():
                if config.audio_data is not None and len(config.audio_data) > 0:
                    emit("status", "无法识别内容")
                metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
                return result
        else:
            result.user_text = config.user_text

        # ASR post-processing: correct common speech recognition errors
        raw_text = result.user_text
        result.user_text, _asr_corrections = correct_asr_text(raw_text)
        if _asr_corrections:
            logger.warning(f"[ASRCorrect] raw={raw_text!r} corrected={result.user_text!r}")

        emit("append_chat", ("user", result.user_text))

        # Evaluate the incoming turn number without mutating the report yet.
        current_rounds = (self.report.get_round_count() + 1) if self.report else 0

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
                time_limit_reached=bool(self.report and self.report.is_over_limit()),
                session_state=config.session_state,
            )
            decision = self.turn_policy.decide(
                user_text=result.user_text,
                proposal=proposal,
                snapshot=snapshot,
                signals=collect_turn_signals(result.user_text, snapshot),
            )
            result.router_proposal = proposal
            result.turn_state_snapshot = snapshot
            result.turn_decision = decision
            self._apply_turn_decision(decision, result)
            self._commit_user_turn(config, result, emit)
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

        # The Router proposal, snapshot, and pure signals are assembled before
        # any scale/session mutation.  RAG and LLM context are built only after
        # the authoritative decision exists.
        proposal = config.router_proposal
        agent_route = proposal.model_dump(mode="json") if proposal is not None else None
        if proposal is None:
            proposal, agent_route = self._request_router_proposal(
                user_text=result.user_text,
                current_rounds=current_rounds,
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
        if not before_runtime.active_scale:
            pre_deltas, pre_reasons = score_symptom_signals(
                result.user_text, self.symptom_scores
            )
            projected = {
                scale: self.symptom_scores.get(scale, 0) + delta
                for scale, delta in pre_deltas.items()
            }
            if current_rounds >= MIN_ROUNDS_BEFORE_SCALE:
                eligible = [
                    (scale, score)
                    for scale, score in projected.items()
                    if score >= 3 and scale not in before_runtime.administered_scales
                ]
                if eligible:
                    eligible.sort(key=lambda pair: (-pair[1], pair[0]))
                    deterministic_candidate = deterministic_candidate or eligible[0][0]

        snapshot = result.turn_state_snapshot
        if snapshot is None:
            snapshot = self._build_turn_snapshot(
                round_count=current_rounds,
                time_limit_reached=bool(self.report and self.report.is_over_limit()),
                session_state=config.session_state,
            )
        result.turn_state_snapshot = snapshot
        signals = collect_turn_signals(
            result.user_text,
            snapshot,
            deterministic_scale_candidate=deterministic_candidate,
            legacy_relaxation_candidate=self._pending_relaxation_after_scale,
        )
        decision = config.turn_decision
        if decision is None:
            decision = self.turn_policy.decide(
                user_text=result.user_text,
                proposal=proposal,
                snapshot=snapshot,
                signals=signals,
            )
        result.turn_decision = decision
        self._apply_turn_decision(decision, result)
        self._commit_user_turn(config, result, emit)
        runtime = self.scale_runtime.snapshot()

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

        # Build ordinary context only after the decision has been formed.  The
        # decision's needs_rag bit is the sole RAG gate for this turn.
        with metrics.timer("rag.system_suffix"):
            system_suffix = self._build_system_suffix(
                result.user_text,
                needs_rag=decision.needs_rag,
                round_count=current_rounds,
            )

        if decision.action in (TurnAction.START_SCALE, TurnAction.CONTINUE_SCALE):
            hint = self._build_scale_context_hint(
                runtime.active_scale or decision.scale_name,
                runtime.current_item or 1,
                agent_route or {},
            )
            if hint:
                system_suffix += hint
        elif decision.action is TurnAction.RECOMMEND_RELAXATION:
            relax_hint = {
                "breathing": "你可以试试旁边的呼吸放松训练，跟着做几分钟，身体会松一些。",
                "muscle": "你可以试试旁边的肌肉放松训练，让身体缓一缓。",
                "meditation": "你可以试试旁边的冥想训练，静一静对睡眠也有帮助。",
            }.get(self._relaxation_candidate or "breathing", "你可以试试旁边的放松训练。")
            system_suffix += f"\n【建议放松】{relax_hint} 把这句话自然地融入你的回复里，不要原样照搬。"
        elif decision.action is TurnAction.RECOMMEND_GAME:
            system_suffix += "\n【建议游戏】你可以试试旁边的小游戏，换换心情。把这句话自然地融入你的回复里，不要原样照搬。"

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
        # hints. Structured answer interpretation happens in the pure
        # ScaleAnswerInterpreter after the response, not in the LLM branch.

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

        # --- LLM stream + Agent classification (concurrent) ---
        # Start LLM immediately with RAG/scale context; agent runs in parallel.
        # This saves 1-3s of agent wait time before first LLM token.
        emit("start_ai_message", None)
        agent_future = self._executor.submit(
            self._classify_intent_emotion, result.user_text
        )

        try:
            with metrics.timer("llm.stream"):
                result.full_response, result.analysis_text, result.spoken_text = \
                    self._stream_llm(result.user_text, final_suffix, emit)

            # Log raw LLM response before post-processing
            logger.warning(
                f"[PipelineReplyRaw] user={result.user_text!r} "
                f"raw={result.full_response[:800]!r}"
            )

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

        # Check for internal strategy term leak before display/TTS
        if _contains_internal_leak(result.spoken_text):
            logger.warning(f"[OutputClean] internal strategy leaked: {result.spoken_text!r}")
            if runtime.active_scale:
                result.spoken_text = self._make_scale_clarify_reply(
                    runtime.active_scale, runtime.current_item or 1, result.user_text
                )
            else:
                result.spoken_text = make_safe_fallback_reply(result.user_text)

        result.clean_spoken = clean_for_display(result.spoken_text)
        emit("clean_last_ai", result.clean_spoken)
        result.tts_text = clean_for_tts(result.spoken_text)

        # --- Tag detection ---
        raw_end_type = detect_tag(result.full_response, END_PATTERNS)
        # Legacy tags are response metadata only.  They never create or
        # replace the authoritative decision formed before the 72B call.
        if raw_end_type and result.turn_decision and result.turn_decision.action is not TurnAction.END_SESSION:
            logger.warning(
                f"[EndDebug] ignored non-authoritative END tag {raw_end_type!r} "
                f"for decision={result.turn_decision.action.value}"
            )

        # A relaxation event is emitted only when the Decision authorized it;
        # an LLM REC tag by itself is ignored.
        llm_rec = detect_tag(result.full_response, REC_TAGS)
        if result.turn_decision and result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION:
            result.relaxation_rec = self._relaxation_candidate or _normalize_relaxation_type(
                result.turn_decision.intervention_type
            )
            logger.warning(f"[RelaxDebug] relaxation authorized by TurnDecision: {result.relaxation_rec}")
        elif llm_rec:
            logger.warning(
                f"[RelaxDebug] ignored non-authoritative REC tag {llm_rec!r} "
                f"for decision={result.turn_decision.action.value if result.turn_decision else 'none'}"
            )
        self._relaxation_candidate = None

        # Preserve this turn's recommendation until the asynchronous intent
        # classification has completed below.
        game_recommended = self._game_candidate
        self._game_candidate = False
        parsed_scale_tags = parse_scale_tags(result.full_response)
        runtime = self.scale_runtime.snapshot()
        allowed_scale = (
            runtime.active_scale
            if result.turn_decision and result.turn_decision.action in (
                TurnAction.START_SCALE,
                TurnAction.CONTINUE_SCALE,
            )
            else None
        )
        result.scale_tags = {}
        if parsed_scale_tags:
            logger.warning(
                f"[ScaleDebug] SCALE tags are candidates only: {parsed_scale_tags}"
            )
        accepted_update = None
        accepted_item = None
        accepted_score = None
        if allowed_scale and runtime.current_item:
            current_item = runtime.current_item
            tag_score = parsed_scale_tags.get(allowed_scale, {}).get(current_item)
            if tag_score is not None and self._scale_manager.validate_answer(
                allowed_scale, item=current_item, score=tag_score
            ):
                accepted_update = self.scale_runtime.accept_answer(
                    scale_name=allowed_scale,
                    item=current_item,
                    score=tag_score,
                )
                if accepted_update.accepted:
                    accepted_item, accepted_score = current_item, tag_score
                    result.scale_tags = {allowed_scale: {current_item: tag_score}}
            elif parsed_scale_tags.get(allowed_scale):
                logger.warning(
                    f"[ScaleDebug] ignored tag not matching current Runtime item: "
                    f"{parsed_scale_tags.get(allowed_scale)}"
                )

            if accepted_update is None or not accepted_update.accepted:
                interpretation = self.answer_interpreter.interpret(
                    result.user_text,
                    scale_name=allowed_scale,
                    item=current_item,
                )
                if interpretation.status == "accepted" and interpretation.score is not None:
                    accepted_update = self.scale_runtime.accept_answer(
                        scale_name=allowed_scale,
                        item=current_item,
                        score=interpretation.score,
                    )
                    if accepted_update.accepted:
                        accepted_item, accepted_score = current_item, interpretation.score
                        result.scale_tags = {
                            allowed_scale: {current_item: interpretation.score}
                        }
                elif interpretation.status == "ambiguous":
                    self.scale_runtime.request_clarification()
                elif interpretation.status in {"pause", "refusal", "unmatched"}:
                    if not self.scale_runtime.snapshot().paused:
                        self.scale_runtime.request_clarification()

        # Runtime is the only transition owner for answer acceptance and
        # progression.  Clarification text is derived from the unchanged
        # snapshot; it never creates a pending score in Pipeline.
        runtime_after = self.scale_runtime.snapshot()
        if accepted_update is not None and accepted_update.accepted:
            completed_name = allowed_scale
            if accepted_update.completed and completed_name:
                result.scale_completed = True
                result.completed_scale_name = completed_name
                result.all_scales_completed = bool(
                    set(runtime_after.completed_scales)
                    >= set(runtime_after.administered_scales)
                )
                logger.warning(
                    f"[ScaleDebug] completed {completed_name}; "
                    f"completed={runtime_after.completed_scales}"
                )
                if not self._post_scale_relaxation_done:
                    rec_type = (
                        self._pending_relaxation_after_scale
                        or self._choose_post_scale_relaxation(completed_name)
                    )
                    self._pending_relaxation_after_scale = None
                    if rec_type:
                        self._pending_relaxation_after_scale = rec_type
            elif runtime_after.active_scale:
                logger.warning(
                    f"[ScaleDebug] accepted {completed_name} Q{accepted_item}="
                    f"{accepted_score}; next Q{runtime_after.current_item}"
                )
        elif runtime_after.active_scale and runtime_after.current_item:
            current_q = runtime_after.current_item
            _strong = any(x in result.user_text for x in [
                "非常", "很", "特别", "极其", "沮丧", "绝望", "难受", "低落", "焦虑"
            ])
            _positive_pending = self._is_positive_pending_frequency(
                runtime_after.active_scale, current_q, result.user_text
            )
            if _strong or _positive_pending:
                _item_hint = NATURAL_SCALE_QUESTIONS.get(
                    (runtime_after.active_scale, current_q), ""
                )
                system_suffix += f"""
【必须追问频率】用户已明确表达存在症状，但缺少频率信息。
当前量表：{runtime_after.active_scale}
当前题：Q{current_q}
当前维度：{_item_hint}
不要再问"有没有这个症状"。
请自然追问最近两周频率。
示例："这种感觉最近是偶尔几天，还是大多数时间都会有？"
只能问这一件事。不能结束，不能推荐放松，不能泛泛咨询。
"""
            else:
                hint = self._build_scale_context_hint(
                    runtime_after.active_scale, current_q, {}
                )
                if hint:
                    system_suffix += hint
            logger.warning(
                f"[ScaleDebug] item Q{current_q} remains pending; "
                f"strong={_strong}, positive_pending={_positive_pending}"
            )

        # Refresh final_suffix after all scale hints are added
        final_suffix = system_suffix if system_suffix and system_suffix.strip() else None

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

        # Agent results + emotion tracking (parallel with TTS)
        try:
            agent_done = agent_future.result(timeout=10)
            result.intent, result.emotion_result = agent_done
        except Exception as e:
            logger.warning(f"Agent classification failed: {e}")
            result.intent = "counseling"
            result.emotion_result = {"emotion": "neutral", "intensity": 0.0}

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

        # Don't block pipeline waiting for TTS — let it play in background.
        # Use a callback to log errors without holding up the UI.
        if tts_future is not None:
            tts_future.add_done_callback(
                lambda f: logger.warning(f"TTS error: {f.exception()}") if f.exception() else None
            )

        result.scale_active = bool(self.scale_runtime.snapshot().active_scale)
        metrics.record("pipeline.total", (time.perf_counter() - pipeline_started) * 1000.0)
        return result

    def _play_tts(self, text: str):
        """Generate and play TTS. Runs on a worker thread."""
        try:
            with get_metrics().timer("tts.play"):
                self.tts.generate_and_play(text)
        except Exception as e:
            logger.warning(f"TTS error: {e}")

    def _classify_intent_emotion(self, text: str) -> tuple[str, dict]:
        """Run the ordinary intent and emotion classifiers in parallel."""
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

    def _build_active_scale_prompt(self, scale_name: str, q_num: int,
                                    waiting_answer: bool) -> str:
        """Build a subtle system hint for natural symptom exploration.

        This does NOT tell the LLM to "ask question N" or "score this item".
        Instead, it hints which symptom area to naturally explore, and lets
        the LLM generate a conversational response. Background scoring
        happens via the pure ScaleAnswerInterpreter after the turn.
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
            # Already asked — the LLM should continue the conversation naturally.
            # Background scoring will happen via [SCALE:] tags.
            # Hint: if user's response clearly maps to a score, output the tag.
            options_text = " / ".join(
                f"{score}-{label}" for score, label in scale_def.options
            )
            return f"""
【后台提示】来访者正在回应关于"{natural_q}"的了解。
如果回答足够判断频率/程度，在回复末尾输出 [SCALE:{scale_name}:Q{q_num}:S分数]。
评分标准：{options_text}
如果回答模糊，不要猜分数，自然追问一句。
口语回复严禁出现"量表""问卷""题""评分""分数""PHQ-9""GAD-7""PCL-5""接下来"。
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
        """Choose relaxation type based on completed scale results."""
        answers = self.scale_runtime.snapshot().answers_by_scale.get(scale_name, {})
        if not answers:
            return "breathing"

        if scale_name == "PHQ-9":
            # High sleep score (Q3) or fatigue (Q4) → meditation
            if answers.get(3, 0) >= 2 or answers.get(4, 0) >= 2:
                return "meditation"
            return "breathing"

        if scale_name == "GAD-7":
            return "breathing"

        return "breathing"

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

    def _build_scale_context_hint(self, scale_name: str, item: int, agent_route: dict) -> str:
        """Build a detailed scale context hint for the main model.

        Uses agent-provided scale_item_text/scoring_target/required_answer_axis
        if available, otherwise falls back to SCALE_ITEM_CORES dict.
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

        return f"""
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
- 不要说{scale_name}、量表、问卷、第几题、评分
- 不要把问题改成泛泛的"哪里不舒服""想不想聊聊"
- 不要同时问多个维度
- 本轮回复最后必须包含一个可回答的问题
"""

    def _get_relaxation_done(self) -> bool:
        """Check if relaxation training was completed this session."""
        return bool(self._relaxation_recommended_this_session)

    def _get_recent_dialogue_text(self, max_turns: int = 6) -> str:
        """Get recent dialogue text for agent context."""
        if not self.llm or not hasattr(self.llm, 'conversation_history'):
            return ""
        recent = self.llm.conversation_history[-max_turns * 2:]
        lines = []
        for msg in recent:
            role = "来访者" if msg["role"] == "user" else "小薇"
            content = msg.get("content", "")[:150]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _build_system_suffix(
        self,
        text: str,
        *,
        needs_rag: bool = True,
        round_count: int | None = None,
    ) -> str:
        """Build system suffix with round warning + RAG context."""
        from config import MIN_ROUNDS_FOR_RELAXATION

        system_suffix = ""
        current_rounds = (
            int(round_count)
            if round_count is not None
            else (self.report.get_round_count() if self.report else 0)
        )

        if current_rounds < MIN_ROUNDS_FOR_RELAXATION:
            system_suffix = (
                f"【系统警告】当前仅第{current_rounds}轮对话"
                f"（少于{MIN_ROUNDS_FOR_RELAXATION}轮）。"
                f"无论用户说了什么，你绝对禁止推荐放松训练！"
                f"继续通过对话建立关系。"
            )

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

        llm_gen = self.llm.chat(text, system_suffix=system_suffix)

        for chunk in llm_gen:
            full_response += chunk

        if '|||' in full_response:
            parts = full_response.split('|||', 1)
            left = parts[0].strip()
            right = parts[1].strip()

            # Detect reversed format: if left side has no analysis tags but right does,
            # the LLM output is spoken|||analysis instead of analysis|||spoken
            _analysis_tags = ['【情绪识别】', '【状态评估】', '【变革话语】', '【策略选择】']
            left_has_analysis = any(t in left for t in _analysis_tags)
            right_has_analysis = any(t in right for t in _analysis_tags)

            if not left_has_analysis and right_has_analysis:
                # Reversed format: spoken on left, analysis on right
                analysis_text = right
                spoken_text = left
            else:
                # Normal format: analysis on left, spoken on right
                analysis_text = left
                spoken_text = right

            # If LLM duplicated output, take only the first spoken segment
            if '|||' in spoken_text:
                spoken_text = spoken_text.split('|||', 1)[0].strip()
            # Also truncate if duplicate analysis tags appear in spoken text
            for _tag in _analysis_tags:
                if _tag in spoken_text:
                    spoken_text = spoken_text.split(_tag)[0].strip()
        else:
            spoken_text = full_response.strip()

        # Final safety: if spoken_text is still empty or only contains analysis
        # tags that will be stripped by clean_for_display, use a safe fallback.
        if not clean_for_display(spoken_text).strip():
            logger.warning(
                f"spoken_text empty after final cleaning. full_response_head={full_response[:300]!r}"
            )
            spoken_text = make_safe_fallback_reply(text)

        # The response protocol permits the model to produce spoken|||analysis
        # in reverse. Buffer a tagged response until that orientation is known;
        # otherwise private analysis could briefly appear in the UI before the
        # final cleanup pass corrects it.
        display_text = clean_for_display(spoken_text)
        if display_text:
            emit("stream_text", display_text)

        return full_response, analysis_text, spoken_text
