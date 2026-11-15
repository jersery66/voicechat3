"""Production call chain: does consent actually move the real modules?

Every other test asserts what ``TurnPolicy`` returns.  That is necessary but
not sufficient -- a policy can return ``START_SCALE`` while the execution layer
does nothing, and the bug looks like "the model refused to start a scale".

These tests drive the real ``ConversationPipeline.execute`` path with a
scripted *observation* (the only thing that cannot run offline) and assert on
the real ``ScaleRuntime`` state, the real ``OfferLedger``, and the emitted
events.  Nothing here re-implements the chain.
"""

from __future__ import annotations

from conversation.agent_observation import AgentObservation
from conversation.contracts import RouterAction, RouterProposal, TurnAction
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import (
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeTTS,
)


def _observation(action: RouterAction, *, scale=None, offer=None, confidence=0.95):
    return AgentObservation(
        proposal=RouterProposal(
            action=action,
            scale_name=scale,
            offer=offer,
            confidence=confidence,
            reason="test_observation",
        ),
        intent="counseling",
        source="jev",
    )


def _build(report=None):
    return ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=report or FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
    )


def _run(pipeline, text, observation):
    """Execute one turn with a scripted observation, bypassing only the model."""
    pipeline._request_agent_observation = lambda **_: observation  # type: ignore[assignment]
    return pipeline.execute(PipelineConfig(user_text=text), lambda *_: None)


def test_an_invitation_turn_starts_nothing():
    pipeline = _build()
    try:
        result = _run(
            pipeline,
            "最近总是睡不好，心里发慌。",
            _observation(RouterAction.START_SCALE, scale="GAD-7", offer="scale"),
        )
        runtime = pipeline.scale_runtime.snapshot()
        ledger = pipeline._offer_ledger.state()
    finally:
        pipeline.shutdown()

    assert result.turn_decision.action is TurnAction.OFFER_SCALE
    assert runtime.active_scale is None
    assert ledger.pending is not None
    assert ledger.pending.target == "GAD-7"
    assert ledger.pending.status.value == "pending"


def test_accepting_the_invitation_really_starts_the_scale_runtime():
    pipeline = _build()
    report = pipeline.report
    try:
        _run(
            pipeline,
            "最近总是睡不好。",
            _observation(RouterAction.START_SCALE, scale="GAD-7", offer="scale"),
        )
        assert pipeline.scale_runtime.snapshot().active_scale is None

        report.increment_round()
        result = _run(pipeline, "可以，那就做一下吧。", _observation(RouterAction.CHAT))

        runtime = pipeline.scale_runtime.snapshot()
        ledger = pipeline._offer_ledger.state()
    finally:
        pipeline.shutdown()

    assert result.turn_decision.action is TurnAction.START_SCALE
    assert result.turn_decision.scale_name == "GAD-7"
    assert runtime.active_scale == "GAD-7"
    assert runtime.waiting_for_answer is True
    assert ledger.pending is None
    assert ledger.accepted_scales() == ("GAD-7",)


def test_refusing_the_invitation_leaves_the_runtime_untouched():
    pipeline = _build()
    try:
        _run(
            pipeline,
            "最近总是睡不好。",
            _observation(RouterAction.START_SCALE, scale="GAD-7", offer="scale"),
        )
        pipeline.report.increment_round()
        result = _run(pipeline, "不用了，我现在不想做。", _observation(RouterAction.CHAT))
        runtime = pipeline.scale_runtime.snapshot()
        ledger = pipeline._offer_ledger.state()
    finally:
        pipeline.shutdown()

    assert result.turn_decision.action is TurnAction.CHAT
    assert runtime.active_scale is None
    assert ledger.declined_scales() == ("GAD-7",)


def test_asking_about_the_invitation_keeps_it_open_in_the_real_ledger():
    pipeline = _build()
    try:
        _run(
            pipeline,
            "最近总是睡不好。",
            _observation(RouterAction.START_SCALE, scale="GAD-7", offer="scale"),
        )
        pipeline.report.increment_round()
        first = _run(pipeline, "可以先说一下是什么吗？", _observation(RouterAction.CHAT))
        mid = pipeline._offer_ledger.state()

        pipeline.report.increment_round()
        second = _run(pipeline, "好，那就做吧。", _observation(RouterAction.CHAT))
        runtime = pipeline.scale_runtime.snapshot()
    finally:
        pipeline.shutdown()

    assert first.turn_decision.reason == "offer_clarifying"
    assert mid.pending_status == "clarifying"
    assert mid.pending is not None
    assert second.turn_decision.action is TurnAction.START_SCALE
    assert runtime.active_scale == "GAD-7"


def test_a_crisis_turn_cancels_the_invitation_and_starts_nothing():
    pipeline = _build()
    try:
        _run(
            pipeline,
            "最近总是睡不好。",
            _observation(RouterAction.START_SCALE, scale="GAD-7", offer="scale"),
        )
        pipeline.report.increment_round()
        result = _run(pipeline, "我不想活了。", _observation(RouterAction.CHAT))
        runtime = pipeline.scale_runtime.snapshot()
        records = pipeline._offer_ledger.state().records
    finally:
        pipeline.shutdown()

    assert result.turn_decision.reason == "offer_blocked_risk"
    assert runtime.active_scale is None
    assert records[0].status.value == "cancelled"
    assert records[0].resolution_note == "risk"


def test_an_explicit_end_cancels_the_invitation():
    pipeline = _build()
    try:
        _run(
            pipeline,
            "最近总是睡不好。",
            _observation(RouterAction.START_SCALE, scale="GAD-7", offer="scale"),
        )
        pipeline.report.increment_round()
        result = _run(pipeline, "结束吧", _observation(RouterAction.CHAT))
        records = pipeline._offer_ledger.state().records
    finally:
        pipeline.shutdown()

    assert result.turn_decision.action is TurnAction.END_SESSION
    assert records[0].status.value == "cancelled"
    assert records[0].resolution_note == "session_end"


def test_a_relaxation_invitation_is_not_a_session_until_accepted():
    # Relaxation has its own programme floor (MIN_ROUNDS_FOR_RELAXATION), so the
    # session starts late enough for the invitation to be legal.
    pipeline = _build(report=FakeReport(start_round=9))
    try:
        _run(
            pipeline,
            "我最近总是很紧张。",
            _observation(RouterAction.RECOMMEND_RELAXATION, offer="relaxation"),
        )
        pending = pipeline._offer_ledger.state().pending
        before = pipeline._proactive_relaxation_offered

        pipeline.report.increment_round()
        result = _run(pipeline, "好，那就试试吧。", _observation(RouterAction.CHAT))
    finally:
        pipeline.shutdown()

    assert pending is not None and pending.kind.value == "relaxation"
    assert before is False
    assert result.turn_decision.action is TurnAction.RECOMMEND_RELAXATION
    assert result.turn_decision.reason == "offer_relaxation_accepted"
