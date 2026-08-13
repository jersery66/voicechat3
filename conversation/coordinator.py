"""Single conversation composition point with a compatibility adapter."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from uuid import uuid4

from conversation.contracts import PolicyDecision
from research.event_journal import EventJournal
from safety.safety_gate import SafetyGate
from safety.types import SafetyAction, SafetyDecision
from services.pipeline import PipelineConfig, PipelineResult


class LegacyPipeline(Protocol):
    """Compatibility protocol for the existing pipeline during migration."""

    def execute(self, config: PipelineConfig,
                emit: Callable[[str, Any], None]) -> PipelineResult: ...


class ConversationCoordinator:
    """Owns safety-before-dialogue and records structured policy outcomes.

    The existing pipeline stays behind this adapter until voice streaming,
    assessment runtime, and output guard are independently migrated. This
    makes the new entry point runnable without a flag-day UI rewrite.
    """

    def __init__(self, pipeline: LegacyPipeline, *, safety_gate: SafetyGate | None = None,
                 journal: EventJournal | None = None, session_id: str | None = None):
        self._pipeline = pipeline
        self._safety_gate = safety_gate or SafetyGate()
        self._journal = journal
        self._session_id = session_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def set_session_id(self, session_id: str | None) -> None:
        """Set a caller-provided non-identifying journal partition key."""
        self._session_id = session_id

    def start_research_session(self) -> str:
        """Create a random research-session key without using the subject ID."""
        self._session_id = uuid4().hex
        return self._session_id

    def decide_turn(self, user_text: str, agent_route: dict | None = None) -> tuple[SafetyDecision, PolicyDecision]:
        safety = self._safety_gate.assess_input(user_text)
        policy = PolicyDecision.from_agent_route(agent_route)
        self._record("safety_decision", safety)
        self._record("policy_decision", policy)
        return safety, policy

    def execute(self, config: PipelineConfig, emit: Callable[[str, Any], None]) -> PipelineResult:
        """Run one turn while preserving the legacy UI callback protocol."""
        if config.use_stt:
            transcript = self._pipeline.transcribe(config.audio_data, emit)
            if not transcript.strip():
                self._record("turn_completed", {"input_mode": "voice", "end_type": None})
                return PipelineResult()
            safe_config = PipelineConfig(
                use_stt=True,
                use_tts=config.use_tts,
                audio_data=config.audio_data,
                transcribed_text=transcript,
                extra_system_suffix=config.extra_system_suffix,
            )
            return self._execute_text_turn(safe_config, emit, input_mode="voice")

        return self._execute_text_turn(config, emit, input_mode="text")

    def assess_transcript(self, transcript: str, emit: Callable[[str, Any], None], *,
                          input_mode: str = "voice") -> PipelineResult | None:
        """Stop a non-dialogue voice branch when its transcript is high risk.

        Some compatibility UI flows consume a transcript without entering the
        normal dialogue pipeline.  They still must use this same authoritative
        boundary before displaying a participant-facing reply.
        """
        safety = self._safety_gate.assess_input(transcript)
        self._record("safety_decision", safety)
        if safety.action not in {SafetyAction.ESCALATE, SafetyAction.EMERGENCY}:
            return None
        payload = {
            "risk_level": safety.risk_level,
            "indicators": [e.text for e in safety.evidence_spans],
            "immediate_action": True,
        }
        emit("append_chat", ("user", transcript))
        emit("show_crisis", payload)
        result = PipelineResult(
            user_text=transcript,
            crisis_risk=safety.risk_level,
            crisis_indicators=payload["indicators"],
        )
        result.safety_payload = payload
        self._record("policy_decision", PolicyDecision())
        self._record("turn_completed", {"input_mode": input_mode, "end_type": "safety"})
        return result

    def _execute_text_turn(self, config: PipelineConfig, emit: Callable[[str, Any], None], *,
                           input_mode: str) -> PipelineResult:
        """Apply safety to a text turn, including a coordinator-owned transcript."""
        input_text = config.transcribed_text or config.user_text
        blocked = self.assess_transcript(input_text, emit, input_mode=input_mode)
        if blocked is not None:
            return blocked

        result = self._pipeline.execute(config, emit)
        # Router details can be personally revealing. Keep only the typed
        # action fields in research storage, never its free-text rationale.
        policy = PolicyDecision.from_agent_route(result.agent_route)
        self._record("policy_decision", policy.model_copy(update={"reason": ""}))
        self._record("turn_completed", {"input_mode": input_mode, "end_type": result.end_type})
        return result

    def _record(self, event_type: str, payload: Any) -> None:
        if self._journal is not None:
            self._journal.append(event_type, payload, session_id=self._session_id)
