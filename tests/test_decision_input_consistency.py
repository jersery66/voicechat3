"""Production and replay must build the *same* decision request.

This is the regression test for the bug that made every earlier timing number
uninterpretable: ``decision/timing_eval.py`` hard-coded ``recent_history=""``
and built the symptom summary from the current turn's test annotation, while
``services/pipeline.py`` passed a real transcript and accumulated evidence.
Two builders, one contract -- and the benchmark quietly measured the wrong one.

The tests here do not compare the two implementations field by field in the
abstract.  They run the real ``ConversationPipeline`` and the real
``TrajectoryRunner`` over the same dialogue and compare the requests those
paths actually produced.
"""

from __future__ import annotations

from typing import Any

import pytest

from decision.request_builder import build_decision_request
from decision.timing_eval import TrajectoryRunner
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import (
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeTTS,
)

#: The dialogue both paths replay.  Plain Chinese so nothing but the text is
#: responsible for the accumulated state.
USER_TURNS = [
    "最近总觉得累，干什么都没劲。",
    "晚上老是醒，三四点就醒了。",
    "已经一个多月了。",
    "白天上班记不住事，上周还被扣了分。",
    "心情也提不起来，什么都不想干。",
]

ASSISTANT_TURNS = [
    "这种累是从什么时候开始的？",
    "醒了之后还能睡着吗？",
    "这一个多月里最明显的是什么？",
    "被扣分的时候，你心里怎么想？",
    "听起来这段时间你一直绷着。",
]

NEXT_USER_TURN = "我是不是应该好好查一查了？"

ASSISTANT_REPLY = "我们先把你说的这些理一理。"


def _pipeline_with(replies):
    return ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(responses=list(replies)),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
    )


def _production_request():
    """Run the dialogue through the real pipeline, then build the next request."""
    from conversation.agent_observation import AgentObservation
    from conversation.contracts import RouterAction, RouterProposal

    pipeline = _pipeline_with([ASSISTANT_REPLY] * len(USER_TURNS))
    chat = AgentObservation(
        proposal=RouterProposal(action=RouterAction.CHAT, confidence=0.9, reason="test"),
        intent="counseling",
        source="jev",
    )
    pipeline._request_agent_observation = lambda **_: chat  # type: ignore[assignment]
    try:
        for text in USER_TURNS:
            pipeline.execute(PipelineConfig(user_text=text), lambda *_: None)
        request = pipeline._build_decision_request(NEXT_USER_TURN, len(USER_TURNS) + 1)
        history = pipeline.llm.conversation_history
    finally:
        pipeline.shutdown()
    return request, history


def _replay_request(history_pairs):
    """Run the same dialogue through the replay runner and keep the last request."""

    class _Recorder:
        name = "recorder"

        def __init__(self):
            self.requests = []

        def is_available(self):
            return True

        def describe(self):
            return {"provider": "recorder"}

        def decide(self, request):
            from decision.providers.fake import make_intervention_decision

            self.requests.append(request)
            return make_intervention_decision("continue_interview", provider="recorder")

    recorder = _Recorder()
    trajectory = {
        "id": "CONSISTENCY",
        "split": "dev",
        "windows": {},
        "turns": [
            {"round": index + 1, "text": text, "assistant": assistant}
            for index, (text, assistant) in enumerate(history_pairs)
        ]
        + [{"round": len(history_pairs) + 1, "text": NEXT_USER_TURN}],
    }
    TrajectoryRunner(provider=recorder).run(trajectory)
    return recorder.requests[-1]


def _pairs_from_history(history):
    """Pair each user message with the assistant message that followed it."""
    pairs = []
    pending = None
    for message in history:
        if message["role"] == "user":
            pending = message["content"]
        elif pending is not None:
            pairs.append((pending, message["content"]))
            pending = None
    return pairs


#: Fields that legitimately differ between a live session and a replay: the
#: replay has no relaxation service and no game, and it is always "CHATTING"
#: unless a trajectory says otherwise.
IGNORED_FIELDS = {"game_active"}


def test_the_two_paths_build_the_same_request_from_the_same_dialogue():
    production, history = _production_request()
    replay = _replay_request(_pairs_from_history(history))

    differing = {
        name: (getattr(production, name), getattr(replay, name))
        for name in production.model_dump()
        if name not in IGNORED_FIELDS
        and getattr(production, name) != getattr(replay, name)
    }
    assert differing == {}, f"production and replay disagree on: {differing}"


def test_the_replay_request_actually_carries_the_dialogue():
    """The bug was an empty history; assert it is not empty any more."""
    production, history = _production_request()
    replay = _replay_request(_pairs_from_history(history))

    for request in (production, replay):
        assert request.recent_history.strip(), "history must reach the decision model"
        assert USER_TURNS[0] in request.recent_history
        assert USER_TURNS[-1] in request.recent_history
        assert request.recent_history.count("来访者:") == 5


def test_the_accumulated_summary_lists_every_domain_mentioned():
    production, history = _production_request()
    replay = _replay_request(_pairs_from_history(history))

    for request in (production, replay):
        summary = request.symptom_summary
        assert "精力" in summary, summary
        assert "睡眠" in summary, summary
        assert "注意" in summary, summary
        assert "情绪" in summary, summary
        # duration and functional impact are part of the evidence, not extras
        assert "持续" in summary, summary
        assert "影响" in summary, summary


def test_the_replay_never_reads_the_trajectory_annotations():
    """A label in the prompt is a leak; ``domains`` must never reach the model."""
    from decision.providers.fake import make_intervention_decision

    captured: list[Any] = []

    class _Recorder:
        name = "recorder"

        def is_available(self):
            return True

        def describe(self):
            return {}

        def decide(self, request):
            captured.append(request)
            return make_intervention_decision("continue_interview", provider="recorder")

    trajectory = {
        "id": "LEAK",
        "split": "dev",
        "windows": {},
        "gold_scale": "PHQ-9",
        "turns": [
            {
                "round": 1,
                "text": "我今天在操场走了两圈。",
                "domains": ["睡眠", "情绪", "自责"],
                "expect": "continue_interview",
            }
        ],
    }
    TrajectoryRunner(provider=_Recorder()).run(trajectory)
    request = captured[-1]
    assert request.symptom_summary == ""
    assert request.symptom_turns == 0
    assert request.recent_history == ""


def test_the_three_history_modes_change_only_the_input():
    from conversation.interview_state import InterviewState
    from decision.timing import OfferState

    state = InterviewState()
    state.record_user("最近睡不着，已经一个多月了。", round_count=1)
    state.record_user("什么都没意思。", round_count=2)

    common = dict(
        user_text="我是不是该查一查？",
        round_count=3,
        interview_state=state,
        symptom_turns=2,
        offer_state=OfferState(),
    )
    arms = {
        mode: build_decision_request(**common, history_mode=mode)
        for mode in ("minimal", "structured", "structured_history")
    }

    assert arms["minimal"].recent_history == ""
    assert arms["minimal"].symptom_summary == ""
    assert arms["minimal"].symptom_turns == 0

    assert arms["structured"].recent_history == ""
    assert arms["structured"].symptom_summary
    assert arms["structured"].symptom_turns == 2

    assert arms["structured_history"].recent_history
    assert arms["structured_history"].symptom_summary == arms["structured"].symptom_summary

    # Everything that is not the input varies identically across arms.
    for mode in ("structured", "structured_history"):
        assert arms[mode].current_round == 3
        assert arms[mode].user_text == common["user_text"]


def test_an_unknown_history_mode_is_rejected_instead_of_silently_defaulting():
    with pytest.raises(ValueError):
        build_decision_request(user_text="hi", round_count=1, history_mode="best")
