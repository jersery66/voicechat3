"""Single conversation composition point with a compatibility adapter."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Protocol
from uuid import uuid4

from conversation.contracts import PolicyDecision
from research.event_journal import EventJournal
from services.pipeline import PipelineConfig, PipelineResult


class LegacyPipeline(Protocol):
    """Compatibility protocol for the existing pipeline during migration."""

    def transcribe(self, audio_data: Any,
                   emit: Callable[[str, Any], None]) -> str: ...

    def execute(self, config: PipelineConfig,
                emit: Callable[[str, Any], None]) -> PipelineResult: ...


class ConversationCoordinator:
    """Coordinate text and voice turns around the existing pipeline.

    The coordinator owns the single transcript boundary for voice input and
    records only typed, de-identified policy outcomes in the research journal.
    """

    def __init__(self, pipeline: LegacyPipeline, *,
                 journal: EventJournal | None = None, session_id: str | None = None):
        self._pipeline = pipeline
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

    def decide_turn(self, agent_route: dict | None = None) -> PolicyDecision:
        """Translate a legacy router payload and journal its typed outcome."""
        policy = PolicyDecision.from_agent_route(agent_route)
        self._record("policy_decision", policy.model_copy(update={"reason": ""}))
        return policy

    def execute(self, config: PipelineConfig,
                emit: Callable[[str, Any], None]) -> PipelineResult:
        """Run one text or voice turn through the legacy pipeline."""
        input_mode = "voice" if config.use_stt else "text"
        if config.use_stt:
            transcript = self._pipeline.transcribe(config.audio_data, emit)
            if not transcript.strip():
                self._record("turn_completed", {"input_mode": "voice", "end_type": None})
                return PipelineResult()
            config = replace(config, transcribed_text=transcript)

        result = self._pipeline.execute(config, emit)
        policy = PolicyDecision.from_agent_route(result.agent_route)
        self._record("policy_decision", policy.model_copy(update={"reason": ""}))
        self._record("turn_completed", {"input_mode": input_mode, "end_type": result.end_type})
        return result

    def _record(self, event_type: str, payload: Any) -> None:
        if self._journal is not None:
            self._journal.append(event_type, payload, session_id=self._session_id)
