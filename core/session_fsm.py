# core.session_fsm — session lifecycle state machine (pure logic).
#
# Extracted from services/session_orchestrator.py without behavior change.
# The only adaptations for the core/ boundary:
#   - uses stdlib logging directly (services.logger is a thin wrapper over it,
#     so log records are identical once setup_logging() configures the root)
# services/session_orchestrator.py re-exports every name for compatibility.

import logging
from enum import Enum, auto
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """会话生命周期状态"""
    IDLE = auto()                    # 未开始/已结束，等待新会话
    CHATTING = auto()                # 正常对话中
    RELAXATION_RECOMMENDED = auto()  # LLM 推荐了放松训练，等待用户点击
    VIDEO_PLAYING = auto()           # 放松视频播放中
    POST_RELAXATION = auto()         # 放松完成，等待用户继续/结束
    SESSION_ENDING = auto()          # 正在生成报告
    SESSION_ENDED = auto()           # 会话结束，显示结果


@dataclass
class SessionContext:
    """Holds all mutable session state that was previously scattered across MainWindow."""
    state: SessionState = SessionState.IDLE
    session_emotions: list = field(default_factory=list)
    current_relaxation_type: Optional[str] = None
    post_relaxation_timed_out: bool = False


class SessionOrchestrator:
    """
    Owns the session state machine. Validates transitions and provides
    decision logic for session end flow.

    Pure logic class — no Qt, no threads, no UI.
    """

    VALID_TRANSITIONS = {
        SessionState.IDLE: {SessionState.CHATTING},
        SessionState.CHATTING: {
            SessionState.RELAXATION_RECOMMENDED,
            SessionState.VIDEO_PLAYING,
            SessionState.SESSION_ENDING,
            SessionState.IDLE,
        },
        SessionState.RELAXATION_RECOMMENDED: {
            SessionState.VIDEO_PLAYING,
            SessionState.CHATTING,
            SessionState.SESSION_ENDING,
        },
        # Core relaxation exits through POST_RELAXATION.  Catalog-owned
        # leisure content returns directly to CHATTING after its active-media
        # slot is released.
        SessionState.VIDEO_PLAYING: {SessionState.POST_RELAXATION, SessionState.CHATTING},
        SessionState.POST_RELAXATION: {
            SessionState.CHATTING,
            SessionState.SESSION_ENDING,
        },
        SessionState.SESSION_ENDING: {SessionState.SESSION_ENDED},
        SessionState.SESSION_ENDED: {SessionState.IDLE},
    }

    def __init__(self):
        self.ctx = SessionContext()

    def transition_to(self, new_state: SessionState) -> bool:
        """Validate and execute state transition. Returns True if valid."""
        valid = self.VALID_TRANSITIONS.get(self.ctx.state, set())
        if new_state not in valid:
            logger.warning(f"Invalid transition: {self.ctx.state} -> {new_state}")
            return False
        self.ctx.state = new_state
        return True

    @property
    def state(self) -> SessionState:
        return self.ctx.state

    def can_start_pipeline(self) -> bool:
        """Check if a conversation pipeline can be started."""
        return self.ctx.state in (SessionState.CHATTING,)

    def can_play_video(self) -> bool:
        """Check if a relaxation video can be played."""
        return self.ctx.state in (
            SessionState.CHATTING,
            SessionState.RELAXATION_RECOMMENDED,
        )

    def is_session_active(self) -> bool:
        """Check if session is in an active (non-ended) state."""
        return self.ctx.state not in (
            SessionState.SESSION_ENDING,
            SessionState.SESSION_ENDED,
        )

    def evaluate_session_end(self, end_type, relaxation_tag=None, relaxation_used: bool = False):
        """Transition an already-approved end request to report generation.

        ``TurnPolicy`` owns intervention eligibility.  This compatibility
        facade therefore ignores legacy relaxation arguments and only applies
        the lifecycle transition for callers that still use the old method.
        """
        del end_type, relaxation_tag, relaxation_used
        self.transition_to(SessionState.SESSION_ENDING)
        return ("generate_reports", {})

    def reset(self):
        """Reset for new session."""
        self.ctx = SessionContext()
        self.ctx.state = SessionState.CHATTING
