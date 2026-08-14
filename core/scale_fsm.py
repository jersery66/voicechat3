# core.scale_fsm — legacy scale-state compatibility helpers.
#
# Production scale administration is owned by assessment.ScaleRuntime.  This
# module remains importable for historical integrations and pure legacy tests,
# but it is deliberately not imported by ConversationPipeline and cannot be a
# production source of active-item, answer, pause, or completion state.
#
# If this module ever grows transition methods, they must mirror the
# pipeline's invariants exactly (e.g. "at most one active scale", "resume
# only after a soft pause") and be guarded by unit tests.

from typing import Dict, List, Optional

from services.scales import SCALES

SCALE_NAMES = tuple(SCALES.keys())


def fresh_symptom_scores() -> Dict[str, int]:
    """Zeroed cumulative symptom-signal counters, one per scale."""
    return {name: 0 for name in SCALE_NAMES}


class ScaleState:
    """Legacy value container retained for compatibility-only callers.

    Field groups mirror the historical API for compatibility tests only.

    - core FSM: administered / answers / active_scale / active_item /
      waiting_answer / queue / pause_turns / soft_paused / resume_item
    - conversational flow bookkeeping: scale_active / scale_name /
      scale_current_item / scale_completed / scale_refused_rounds /
      scale_defer_until_round / last_scale_ask_round / consecutive_scale_asks
    - cumulative symptom signals: symptom_scores / symptom_turns /
      last_scale_trigger_round / scale_trigger_cooldown
    - resume bookkeeping: pending_scale_resume / last_bot_asked_scale /
      last_bot_asked_item
    """

    def __init__(self) -> None:
        # Init-only constant: legacy reset_session() never touched it, so
        # reset() below must not either.
        self.scale_trigger_cooldown: int = 3
        self.reset()

    def reset(self) -> None:
        """Reset to the start-of-session state (legacy reset_session semantics)."""
        # --- core FSM ---
        self.administered: set = set()
        self.answers: Dict[str, Dict[int, int]] = {}
        self.active_scale: Optional[str] = None
        self.active_item: int = 1
        self.waiting_answer: bool = False
        self.queue: List[str] = []
        self.pause_turns: int = 0
        self.soft_paused: bool = False
        self.resume_item: int = 1
        # --- conversational flow bookkeeping ---
        self.scale_active: bool = False
        self.scale_name: Optional[str] = None
        self.scale_current_item: int = 0
        self.scale_completed: bool = False
        self.scale_refused_rounds: int = 0
        self.scale_defer_until_round: int = 0
        self.last_scale_ask_round: int = -999
        self.consecutive_scale_asks: int = 0
        # --- cumulative symptom signals ---
        self.symptom_scores: Dict[str, int] = fresh_symptom_scores()
        self.symptom_turns: int = 0
        self.last_scale_trigger_round: int = -999
        # NOTE: scale_trigger_cooldown is deliberately NOT reset here
        # (legacy reset_session never touched it); it is set in __init__.
        # --- resume bookkeeping ---
        self.pending_scale_resume: bool = False
        self.last_bot_asked_scale: Optional[str] = None
        self.last_bot_asked_item: int = 0


def delegate_property(state_attr: str):
    """Build a property that reads/writes ScaleState.<state_attr>.

    Used by ConversationPipeline to keep its legacy attribute names
    (e.g. `_active_scale`, `symptom_scores`) working while the actual
    state lives in self._scale_state. Container fields (set/dict/list)
    are returned live, so `.clear()` / `.add()` / `[k] = v` keep working.
    """
    return property(
        lambda self: getattr(self._scale_state, state_attr),
        lambda self, value: setattr(self._scale_state, state_attr, value),
    )
