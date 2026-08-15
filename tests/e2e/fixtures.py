"""Deterministic, test-only boundaries for Phase 8 acceptance scenarios.

The harness deliberately wires real policy, pipeline, scale-runtime, delivery,
and session-engine components.  Scripted doubles are limited to external
model, audio, media, storage, and report seams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any, Iterable, Optional

from app.engine import SessionEngine
from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnDecision,
    TurnStateSnapshot,
)
from conversation.delivery import GenerationController, SentenceReady
from conversation.turn_policy import TurnPolicy
from core.types import EndType
from services.pipeline import ConversationPipeline, PipelineConfig


@dataclass
class ScenarioTrace:
    """Ordered observations used by scenario assertions."""

    events: list[tuple[str, Any]] = field(default_factory=list)
    router_proposals: list[RouterProposal] = field(default_factory=list)
    policy_calls: list[dict[str, Any]] = field(default_factory=list)
    turn_decisions: list[TurnDecision] = field(default_factory=list)
    runtime_snapshots: list[Any] = field(default_factory=list)
    rag_queries: list[str] = field(default_factory=list)
    llm_prompts: list[dict[str, Any]] = field(default_factory=list)
    provider_chunks: list[str] = field(default_factory=list)
    generation_events: list[Any] = field(default_factory=list)
    visible_sentences: list[SentenceReady] = field(default_factory=list)
    tts_calls: list[str] = field(default_factory=list)
    tts_stop_calls: int = 0
    history_writes: list[dict[str, str]] = field(default_factory=list)
    data_manager_writes: list[dict[str, str]] = field(default_factory=list)
    report_events: list[str] = field(default_factory=list)
    engine_commands: list[Any] = field(default_factory=list)
    engine_events: list[Any] = field(default_factory=list)
    finalized_generations: list[tuple[int, str]] = field(default_factory=list)


class TracePolicy(TurnPolicy):
    """Real TurnPolicy with an append-only invocation trace."""

    def __init__(self, trace: ScenarioTrace):
        super().__init__()
        self.trace = trace

    def decide(self, **kwargs):
        self.trace.policy_calls.append(dict(kwargs))
        snapshot = kwargs["snapshot"]
        self.trace.runtime_snapshots.append(snapshot)
        decision = super().decide(**kwargs)
        self.trace.turn_decisions.append(decision)
        return decision


class ScriptedLLM:
    """Chunked provider double that supports Phase 7 deferred history."""

    def __init__(self, responses: Optional[Iterable[str]] = None, *, chunk_size: int = 8,
                 trace: Optional[ScenarioTrace] = None):
        self.responses = list(responses or [])
        self.chunk_size = max(1, int(chunk_size))
        self.trace = trace
        self.calls: list[dict[str, Any]] = []
        self.conversation_history: list[dict[str, str]] = []
        self.history_context = ""

    def chat(self, text: str, system_suffix: str = "", *, commit_history: bool = True):
        response = self.responses.pop(0) if self.responses else "我在听着，你可以慢慢说。"
        call = {
            "user_text": text,
            "system_suffix": system_suffix,
            "response": response,
            "commit_history": commit_history,
        }
        self.calls.append(call)
        if self.trace is not None:
            self.trace.llm_prompts.append(call)
        self.conversation_history.append({"role": "user", "content": text})
        for index in range(0, len(response), self.chunk_size):
            chunk = response[index:index + self.chunk_size]
            if self.trace is not None:
                self.trace.provider_chunks.append(chunk)
            yield chunk
        if commit_history:
            self.conversation_history.append({"role": "assistant", "content": response})

    def reset_conversation(self, clear_context: bool = False):
        self.conversation_history = []
        if clear_context:
            self.history_context = ""

    def set_history_context(self, context: str):
        self.history_context = context


class ScriptedAgent:
    """External Router/classifier seam; proposals are injected by the test."""

    def __init__(self):
        self.route_calls: list[dict[str, Any]] = []
        self.intent_calls = 0
        self.emotion_calls = 0
        self.route_error: Optional[Exception] = None

    def is_available(self) -> bool:
        return self.route_error is None

    def route_conversation_actions(self, **kwargs):
        self.route_calls.append(dict(kwargs))
        if self.route_error is not None:
            raise self.route_error
        return {"action": "chat", "confidence": 0.5, "reason": "fixture"}

    def classify_intent(self, _text: str):
        self.intent_calls += 1
        return {"intent": "counseling", "confidence": 0.9}

    def detect_emotion(self, _text: str):
        self.emotion_calls += 1
        return {"emotion": "neutral", "intensity": 0.1}


class TraceRAG:
    def __init__(self, suffix: str = "【知识库】支持性建议"):
        self.suffix = suffix
        self.calls: list[tuple[str, bool]] = []

    def get_system_suffix(self, query: str, *, enabled: bool = False) -> str:
        self.calls.append((query, enabled))
        if enabled:
            return self.suffix
        return ""


class TraceReport:
    def __init__(self, start_round: int = 0):
        self.round_count = start_round
        self.completed_relaxation = False
        self.time_limit_prompt_shown = False
        self.continued_after_time_limit = False
        self.activity_log: list[Any] = []

    def start_session(self):
        self.round_count = 0
        self.completed_relaxation = False
        self.time_limit_prompt_shown = False
        self.continued_after_time_limit = False

    def increment_round(self):
        self.round_count += 1

    def get_round_count(self) -> int:
        return self.round_count

    def get_session_duration_minutes(self) -> float:
        return 0.0

    def should_warn_time_limit(self):
        return False, ""

    def is_over_limit(self) -> bool:
        return False


class TraceData:
    def __init__(self, trace: ScenarioTrace):
        self.trace = trace
        self.current_subject_id: Optional[str] = None

    def set_user_id(self, user_id: str):
        self.current_subject_id = user_id

    def save_user_message(self, _audio, text: str):
        self.trace.data_manager_writes.append({"role": "user", "text": text})
        return None, None

    def save_assistant_message(self, _audio, text: str, sample_rate: int = 48000):
        self.trace.data_manager_writes.append({"role": "assistant", "text": text})
        return None, None


class TraceTTS:
    def __init__(self, trace: ScenarioTrace, *, fail_on: Optional[str] = None):
        self.trace = trace
        self.fail_on = fail_on
        self.started = Event()
        self.release = Event()
        self.block = False
        self._lock = Lock()

    def generate_and_play(self, text: str):
        with self._lock:
            self.trace.tts_calls.append(text)
        self.started.set()
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("scripted TTS failure")
        if self.block:
            self.release.wait(timeout=2.0)

    def stop_playing(self):
        self.trace.tts_stop_calls += 1
        self.release.set()


class ScenarioHarness:
    """Own all resources needed by one deterministic acceptance scenario."""

    def __init__(self, *, responses: Optional[Iterable[str]] = None,
                 start_round: int = 0, rag_suffix: str = "【知识库】支持性建议",
                 tts_fail_on: Optional[str] = None, chunk_size: int = 8):
        self.trace = ScenarioTrace()
        self.controller = GenerationController()
        self.llm = ScriptedLLM(responses, chunk_size=chunk_size, trace=self.trace)
        self.agent = ScriptedAgent()
        self.rag = TraceRAG(rag_suffix)
        self.report = TraceReport(start_round=start_round)
        self.data = TraceData(self.trace)
        self.tts = TraceTTS(self.trace, fail_on=tts_fail_on)
        self.policy = TracePolicy(self.trace)
        self.pipeline = ConversationPipeline(
            stt_service=None,
            llm_service=self.llm,
            tts_service=self.tts,
            rag_service=self.rag,
            agent_service=self.agent,
            report_service=self.report,
            data_manager=self.data,
            session_emotions=[],
            turn_policy=self.policy,
            delivery_controller=self.controller,
        )

    def emit(self, kind: str, content: Any):
        self.trace.events.append((kind, content))
        if kind == "stream_text" and isinstance(content, SentenceReady):
            if self.pipeline.delivery_ledger.commit_visible(content):
                self.trace.visible_sentences.append(content)
        elif kind == "finish_streaming" and isinstance(content, int):
            delivered = self.pipeline.delivery_ledger.finalize_history(
                content, self.llm, self.data
            )
            self.trace.finalized_generations.append((content, delivered))

    def run_turn(self, text: str, proposal: Optional[RouterProposal] = None):
        record = self.controller.start_generation()
        config = PipelineConfig(
            user_text=text,
            use_stt=False,
            use_tts=False,
            router_proposal=proposal,
            generation_id=record.generation_id,
        )
        result = self.pipeline.execute(config, self.emit)
        return result, record.generation_id

    def new_generation(self) -> int:
        return self.controller.start_generation().generation_id

    def shutdown(self):
        self.pipeline.shutdown()


def proposal(action: RouterAction = RouterAction.CHAT, *, scale: str | None = None,
             intervention: str | None = None, needs_rag: bool = False,
             confidence: float = 0.99, reason: str = "fixture") -> RouterProposal:
    return RouterProposal(
        action=action,
        scale_name=scale,
        intervention_type=intervention,
        needs_rag=needs_rag,
        confidence=confidence,
        reason=reason,
    )


def snapshot(**overrides) -> TurnStateSnapshot:
    values = {
        "session_state": "CHATTING",
        "round_count": 8,
        "active_scale": None,
        "current_item": None,
        "waiting_for_answer": False,
        "completed_scales": (),
        "relaxation_used": False,
        "proactive_relaxation_offered": False,
        "game_active": False,
        "time_limit_reached": False,
    }
    values.update(overrides)
    return TurnStateSnapshot(**values)


def start_session(events: list[Any], subject_id: str = "P8") -> SessionEngine:
    engine = SessionEngine(emit=events.append)
    from app.contracts import StartSessionCommand, SubjectInfo

    engine.process_command(StartSessionCommand(subject=SubjectInfo(subject_id=subject_id)))
    return engine


__all__ = [
    "ScenarioHarness",
    "ScenarioTrace",
    "ScriptedAgent",
    "ScriptedLLM",
    "TraceData",
    "TraceRAG",
    "TraceReport",
    "TraceTTS",
    "TurnPolicy",
    "proposal",
    "snapshot",
    "start_session",
]
