"""A safety disclosure must survive the turn it was made in.

The holdout set exposed this: after a participant disclosed self-harm ideation
and then confirmed it in the shortest possible sentence ("有时候会这么想。"),
the turn carried no risk keyword of its own, so the gate opened and a
questionnaire was offered *inside a risk conversation*.

``risk_marker`` is re-detected from each utterance and therefore cannot carry
this.  ``risk_in_session`` does, and it is a gate — not a diagnosis: this layer
never concludes "no risk", it only refuses to switch activity while a
disclosure is open.
"""

from __future__ import annotations

from conversation.contracts import RouterAction, TurnStateSnapshot, TurnSignals
from conversation.turn_policy import TurnPolicy
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import (
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeTTS,
)

DISCLOSURE = "晚上一个人躺着的时候会想，不如就这么算了。"


def _observation(action: RouterAction, *, scale=None, offer=None):
    from conversation.agent_observation import AgentObservation
    from conversation.contracts import RouterProposal

    return AgentObservation(
        proposal=RouterProposal(
            action=action, scale_name=scale, offer=offer, confidence=0.95, reason="test"
        ),
        intent="counseling",
        source="jev",
    )


def _pipeline():
    return ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=3),
        data_manager=FakeData(),
        session_emotions=[],
    )


def _run(pipeline, text, observation):
    pipeline._request_agent_observation = lambda **_: observation  # type: ignore[assignment]
    return pipeline.execute(PipelineConfig(user_text=text), lambda *_: None)


def test_an_invitation_after_a_disclosure_is_refused_even_when_the_turn_is_calm():
    pipeline = _pipeline()
    try:
        _run(pipeline, DISCLOSURE, _observation(RouterAction.CHAT))
        assert pipeline._risk_disclosed is True

        result = _run(
            pipeline,
            "有时候会这么想。",
            _observation(RouterAction.START_SCALE, scale="PHQ-9", offer="scale"),
        )
        ledger = pipeline._offer_ledger.state()
        runtime = pipeline.scale_runtime.snapshot()
    finally:
        pipeline.shutdown()

    assert result.turn_decision.reason != "user_scale_request"
    assert result.turn_decision.reason == "offer_blocked_risk"
    assert ledger.records == ()
    assert runtime.active_scale is None


def test_the_session_flag_is_cleared_on_reset():
    pipeline = _pipeline()
    try:
        _run(pipeline, DISCLOSURE, _observation(RouterAction.CHAT))
        assert pipeline._risk_disclosed is True
        pipeline.reset_session()
        assert pipeline._risk_disclosed is False
    finally:
        pipeline.shutdown()


def test_the_policy_refuses_directly_on_the_session_flag():
    policy = TurnPolicy()
    decision = policy.decide(
        user_text="我最近睡得还行。",
        proposal=_observation(RouterAction.START_SCALE, scale="PHQ-9", offer="scale").proposal,
        snapshot=TurnStateSnapshot(round_count=7, risk_in_session=True),
        signals=TurnSignals(),
    )
    assert decision.reason == "offer_blocked_risk"


def test_the_holdout_crisis_trajectory_never_receives_an_offer():
    """Regression on the exact case that surfaced the defect."""
    import json
    from pathlib import Path

    from decision.providers.rule import RuleTimingProvider
    from decision.timing_eval import run_trajectories

    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "benchmarks"
        / "cases"
        / "interview_trajectories_holdout.jsonl"
    )
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    crisis = next(row for row in rows if row["id"] == "HOLD-11")

    report = run_trajectories(provider=RuleTimingProvider(), trajectories=[crisis])
    offers = [turn for turn in report["trajectories"][0]["turns"] if turn["offers"]]
    assert offers == []
