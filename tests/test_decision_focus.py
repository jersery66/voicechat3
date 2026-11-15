"""The focus question: which dimension this turn's question lands on.

Strategy said what kind of move to make, which still left the writer free to ask
about anything -- and it reliably asked about whatever was easiest to see, so a
whole session could circle sleep.  Focus names the dimension, and the pipeline
keeps a per-dimension count so the next turn's decision can see what has already
been covered.  Together they are the only part of the decision layer that can
steer *content* rather than tone.

Forbidden by design: risk.  Self-harm is a rule and policy matter; a scored
option would read as a clinical claim.
"""

from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnSignals,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy
from decision.contracts import RawDecision, build_observation
from decision.posture import (
    FOCUS_LABELS,
    focus_name,
    normalize_focus,
    resolve_focus,
    resolve_posture,
)
from decision.providers.base import build_state_text
from decision.contracts import DecisionRequest
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_focus_accepts_label_option_text_and_chinese_name():
    assert normalize_focus("sleep") == "sleep"
    assert normalize_focus("SLEEP") == "sleep"
    assert normalize_focus("睡眠") == "sleep"
    assert normalize_focus("Sleep: getting to sleep, waking at night, waking early, daytime tiredness") == "sleep"


def test_an_unknown_focus_becomes_none_rather_than_a_guess():
    """A missing focus costs a generic question; a wrong one invents a topic."""
    assert normalize_focus("") is None
    assert normalize_focus(None) is None
    assert normalize_focus("whatever the model felt like") is None


def test_risk_is_not_a_focus():
    """Safety is rule-based; no option label may carry a clinical claim."""
    assert "risk" not in FOCUS_LABELS
    assert "suicide" not in FOCUS_LABELS


def test_focus_names_cover_every_label():
    for label in FOCUS_LABELS:
        assert focus_name(label) and focus_name(label) != label


# ---------------------------------------------------------------------------
# Coherence at the decision boundary
# ---------------------------------------------------------------------------


def test_a_question_turn_keeps_its_focus():
    assert resolve_focus("deepen", "probe", "sleep") == "sleep"
    assert resolve_focus("explore", "probe", "daily") == "daily"


def test_an_opening_turn_asks_even_when_the_model_said_not_to():
    """Deepen/explore plus reflect is repaired before it reaches the writer.

    Keeping the focus on such a turn was tried first: with no question to spend
    the dimension on, the writer filled the space with a truism ("压力大的时候，
    人往往连呼吸都会变浅") or produced an empty completion.  The pair is now
    resolved to probe, which is why the focus survives here.
    """
    assert resolve_posture("deepen", "reflect") == ("deepen", "probe")
    assert resolve_focus("deepen", "probe", "sleep") == "sleep"


def test_a_non_question_turn_drops_the_focus():
    """A hold, a close or a summary has no dimension to land on."""
    assert resolve_focus("hold", "reflect", "sleep") is None
    assert resolve_focus("close", "summarize", "sleep") is None
    assert resolve_focus("stabilize", "reflect", "sleep") is None
    assert resolve_focus("deepen", "summarize", "sleep") is None
    assert resolve_focus("deepen", "quiet", "sleep") is None


def test_a_task_turn_carries_no_focus():
    observation = build_observation(
        RawDecision(
            provider="fake",
            intervention="offer_scale",
            offer_target="PHQ-9",
            strategy="deepen",
            stance="probe",
            focus="sleep",
            confidence=0.8,
        ),
        source="jev",
    )
    assert observation.proposal.action is RouterAction.START_SCALE
    assert observation.proposal.focus is None


def test_a_clarify_turn_carries_no_focus():
    observation = build_observation(
        RawDecision(
            provider="fake",
            intervention="clarify_first",
            strategy="deepen",
            stance="probe",
            focus="sleep",
            confidence=0.8,
        ),
        source="jev",
    )
    assert observation.proposal.clarify is True
    assert observation.proposal.focus is None


def test_the_policy_carries_the_focus_without_inventing_one():
    proposal = RouterProposal(action=RouterAction.CHAT, strategy="deepen", stance="probe", focus="energy")
    decision = TurnPolicy().decide(
        user_text="白天一点劲都没有",
        proposal=proposal,
        snapshot=TurnStateSnapshot(round_count=3),
        signals=TurnSignals(),
    )
    assert decision.action is TurnAction.CHAT
    assert decision.focus == "energy"


def test_a_proposal_without_a_focus_produces_no_focus():
    """The policy is a carrier, never an author."""
    decision = TurnPolicy().decide(
        user_text="白天一点劲都没有",
        proposal=RouterProposal(action=RouterAction.CHAT, strategy="deepen", stance="probe"),
        snapshot=TurnStateSnapshot(round_count=3),
        signals=TurnSignals(),
    )
    assert decision.focus is None


# ---------------------------------------------------------------------------
# Coverage feedback into the decision model
# ---------------------------------------------------------------------------


def test_state_text_names_the_uncovered_dimensions():
    request = DecisionRequest(
        user_text="睡不好",
        focus_coverage={"sleep": 2, "mood": 1},
    )

    text = build_state_text(request)

    assert "【已问过的方面】" in text
    assert "睡眠×2" in text
    assert "情绪×1" in text
    assert "【还没问过的方面】" in text
    assert "精力与疲劳" in text
    # A covered dimension is not listed as missing.
    assert "还没问过的方面】睡眠" not in text


def test_state_text_omits_coverage_before_anything_is_asked():
    text = build_state_text(DecisionRequest(user_text="你好"))

    assert "已问过的方面" not in text


# ---------------------------------------------------------------------------
# Prompt: the focus is binding, and only on a turn that asks
# ---------------------------------------------------------------------------


def build_pipeline(policy=None, llm=None):
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=llm or FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
        turn_policy=policy,
    )
    if policy is not None:
        policy.pipeline = pipeline
    return pipeline


def chat_decision(focus="sleep", strategy="deepen", stance="probe"):
    """The decision the policy would have produced, supplied explicitly.

    Forcing it through ``PipelineConfig.turn_decision`` keeps the test about the
    wording contract: agent routing and the scale trigger would otherwise start
    a questionnaire mid-test and change the action under it.
    """
    return TurnDecision(
        action=TurnAction.CHAT, strategy=strategy, stance=stance, focus=focus
    )


def run_turn(pipeline, text, decision):
    return pipeline.execute(
        PipelineConfig(user_text=text, turn_decision=decision), lambda *_: None
    )


def test_the_focus_reaches_the_writer_as_one_dimension_only():
    llm = FakeLLM()
    pipeline = build_pipeline(llm=llm)
    try:
        run_turn(pipeline, "最近一直睡不好，三四点就醒了", chat_decision("sleep"))
    finally:
        pipeline.shutdown()

    suffix = llm.calls[-1]["system_suffix"]
    assert "【本轮问哪一方面】睡眠" in suffix
    assert "躺多久才睡着" in suffix
    assert "这一轮不要问别的方面" in suffix


def test_a_repeated_dimension_is_asked_from_a_new_angle():
    llm = FakeLLM()
    pipeline = build_pipeline(llm=llm)
    try:
        for _ in range(3):
            run_turn(pipeline, "还是睡不好", chat_decision("sleep"))
    finally:
        pipeline.shutdown()

    first, third = llm.calls[0]["system_suffix"], llm.calls[2]["system_suffix"]
    assert "已经问过" not in first
    assert "已经问过 2 次" in third
    assert "不要重复上一回的问法" in third


def test_a_non_question_turn_gets_no_dimension():
    llm = FakeLLM()
    pipeline = build_pipeline(llm=llm)
    try:
        run_turn(
            pipeline, "也没什么好说的",
            chat_decision("sleep", strategy="hold", stance="reflect"),
        )
    finally:
        pipeline.shutdown()

    suffix = llm.calls[-1]["system_suffix"]
    assert "本轮问哪一方面" not in suffix
    assert "落在他的状态上" in suffix


def test_a_hold_turn_still_gets_no_dimension_to_ask_about():
    """A hold is the only chat move that may not ask, so it lands on no
    dimension -- there would be no question to spend it on."""
    llm = FakeLLM()
    pipeline = build_pipeline(llm=llm)
    try:
        run_turn(
            pipeline, "三四点就醒了，躺着翻来覆去",
            chat_decision("sleep", strategy="hold", stance="reflect"),
        )
    finally:
        pipeline.shutdown()

    suffix = llm.calls[-1]["system_suffix"]
    assert "本轮问哪一方面" not in suffix
    assert "句末不要有问号" in suffix


# ---------------------------------------------------------------------------
# Coverage record
# ---------------------------------------------------------------------------


def test_coverage_counts_only_turns_that_ask():
    pipeline = build_pipeline()
    try:
        run_turn(pipeline, "睡不好", chat_decision("sleep"))
        run_turn(pipeline, "还是睡不好", chat_decision("sleep"))
        coverage = pipeline.get_interview_coverage()
    finally:
        pipeline.shutdown()

    assert coverage["counts"] == {"sleep": 2}
    assert coverage["covered"][0]["name"] == "睡眠"
    assert coverage["covered"][0]["turns"] == 2
    assert "睡眠" not in coverage["uncovered"]
    assert "食欲与体重" in coverage["uncovered"]


def test_an_acknowledgement_turn_does_not_mark_a_dimension_covered():
    pipeline = build_pipeline()
    try:
        run_turn(
            pipeline, "也没什么好说的",
            chat_decision("sleep", strategy="hold", stance="reflect"),
        )
        coverage = pipeline.get_interview_coverage()
    finally:
        pipeline.shutdown()

    assert coverage["counts"] == {}


def test_the_decision_request_carries_the_coverage_record():
    pipeline = build_pipeline()
    try:
        run_turn(pipeline, "睡不好", chat_decision("sleep"))
        request = pipeline._build_decision_request("还是睡不好", 2)
    finally:
        pipeline.shutdown()

    assert request.focus_coverage == {"sleep": 1}
    assert "睡眠×1" in build_state_text(request)


def test_reset_session_clears_the_coverage():
    pipeline = build_pipeline()
    try:
        run_turn(pipeline, "睡不好", chat_decision("sleep"))
        assert pipeline.get_interview_coverage()["counts"]
        pipeline.reset_session()
        coverage = pipeline.get_interview_coverage()
    finally:
        pipeline.shutdown()

    assert coverage["counts"] == {}
    assert len(coverage["uncovered"]) == len(FOCUS_LABELS)


# ---------------------------------------------------------------------------
# Choices made from measurement
# ---------------------------------------------------------------------------


def test_substance_is_not_a_focus():
    """Asked about a row with his mother, the 1.7B model picked "substances"
    (0.64) over "mood" -- the option text reads as a coping strategy and the
    small model took the bait.  Removed rather than patched: an off-target
    dimension is worse than none, because the writer is then told to ask it."""
    assert "substance" not in FOCUS_LABELS


def test_the_state_block_states_the_interview_objective():
    """Without it the stance question was answered against a generic
    conversational norm, so the model said "just listen" on most intake turns
    (1/6 asked anything).  With it, 4/6 turns ask and confidence roughly
    doubles."""
    text = build_state_text(DecisionRequest(user_text="睡不好"))

    assert "【访谈目标】" in text
    assert "摄入性访谈" in text
    # Listening is still allowed -- the objective is not "ask every turn".
    assert "只回应、不提问是合适的" in text
