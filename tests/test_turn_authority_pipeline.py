"""Integration checks that the pipeline executes one authoritative decision."""

from conversation.contracts import RouterAction, TurnAction
from conversation.turn_policy import TurnPolicy
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS


class SpyPolicy(TurnPolicy):
    def __init__(self):
        self.calls = []
        self.state_at_call = []
        super().__init__()

    def decide(self, **kwargs):
        self.calls.append(kwargs)
        pipeline = getattr(self, "pipeline", None)
        if pipeline is not None:
            runtime = pipeline.scale_runtime.snapshot()
            self.state_at_call.append(
                (
                    runtime.active_scale,
                    runtime.waiting_for_answer,
                    runtime.paused,
                )
            )
        return super().decide(**kwargs)


def build_pipeline(agent=None, llm=None, report=None, policy=None):
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=llm or FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=agent or FakeAgent(),
        report_service=report or FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
        turn_policy=policy,
    )
    if policy is not None:
        policy.pipeline = pipeline
    return pipeline


def test_pipeline_attaches_one_decision_and_router_proposal():
    agent = FakeAgent()
    agent.route_script = [{
        "action": "start_scale",
        "scale": "PHQ-9",
        "confidence": 0.95,
        "reason": "symptoms",
    }]
    policy = SpyPolicy()
    pipeline = build_pipeline(agent=agent, policy=policy)
    try:
        result = pipeline.execute(PipelineConfig(user_text="最近睡不好"), lambda *_: None)
    finally:
        pipeline.shutdown()

    assert len(policy.calls) == 1
    assert result.router_proposal.action is RouterAction.START_SCALE
    assert result.turn_decision.action is TurnAction.START_SCALE
    assert result.turn_decision.scale_name == "PHQ-9"


def test_router_proposal_does_not_mutate_scale_before_policy():
    agent = FakeAgent()
    agent.route_script = [{"action": "chat", "confidence": 0.4}]
    policy = SpyPolicy()
    pipeline = build_pipeline(agent=agent, policy=policy)
    pipeline.scale_runtime.start("PHQ-9")
    pipeline.scale_runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)
    pipeline.scale_runtime.present_current_item()
    try:
        result = pipeline.execute(
            PipelineConfig(user_text="别问了，我想聊点别的"), lambda *_: None
        )
    finally:
        pipeline.shutdown()

    assert policy.state_at_call == [("PHQ-9", True, False)]
    assert result.turn_decision.action is TurnAction.PAUSE_SCALE


def test_legacy_end_and_relaxation_tags_cannot_override_chat_decision():
    llm = FakeLLM([
        "分析|||先陪你聊着。[END_GOAL_ACHIEVED][REC_BREATHING]"
    ])
    agent = FakeAgent()
    agent.route_script = [{"action": "chat", "confidence": 0.9}]
    pipeline = build_pipeline(agent=agent, llm=llm)
    try:
        result = pipeline.execute(PipelineConfig(user_text="我好多了，谢谢你"), lambda *_: None)
    finally:
        pipeline.shutdown()

    assert result.turn_decision.action is TurnAction.CHAT
    assert result.end_type is None
    assert result.relaxation_rec is None
    assert not hasattr(pipeline, "relaxation_used")


def test_explicit_end_does_not_enqueue_a_competing_timeout_check():
    class TimedReport(FakeReport):
        def get_session_duration_minutes(self):
            return 45.0

    pipeline = build_pipeline(report=TimedReport(start_round=8))
    events = []
    try:
        result = pipeline.execute(
            PipelineConfig(user_text="结束"),
            lambda kind, content: events.append((kind, content)),
        )
    finally:
        pipeline.shutdown()

    assert result.turn_decision.action is TurnAction.END_SESSION
    assert "time_limit_check" not in [kind for kind, _ in events]


def test_pause_lasts_until_the_participant_asks_to_return():
    """A paused scale must not resume just because the next turn exists.

    The real bug was one turn deep: "先别问了" paused it, the very next
    sentence resumed it and asked the next question again, so the participant
    never actually got to change the subject.
    """
    policy = SpyPolicy()
    pipeline = build_pipeline(policy=policy)
    pipeline.scale_runtime.start("PHQ-9")
    pipeline.scale_runtime.accept_answer(scale_name="PHQ-9", item=1, score=1)
    pipeline.scale_runtime.present_current_item()
    try:
        paused = pipeline.execute(
            PipelineConfig(user_text="先别问了，我想聊点别的"), lambda *_: None
        )
        chatting = pipeline.execute(
            PipelineConfig(user_text="这两天翻来覆去到天亮，白天一点精神都没有"),
            lambda *_: None,
        )
        state_while_chatting = pipeline.scale_runtime.snapshot()
        resumed = pipeline.execute(
            PipelineConfig(user_text="行，那你接着问吧"), lambda *_: None
        )
        state_after_resume = pipeline.scale_runtime.snapshot()
    finally:
        pipeline.shutdown()

    assert paused.turn_decision.action is TurnAction.PAUSE_SCALE
    assert chatting.turn_decision.action is TurnAction.CHAT
    assert chatting.turn_decision.reason == "active_scale_paused"
    # The unanswered item stayed parked through the chat turn.
    assert state_while_chatting.paused is True
    assert state_while_chatting.current_item == 2
    assert resumed.turn_decision.action is TurnAction.CONTINUE_SCALE
    assert resumed.turn_decision.reason == "active_scale_resume"
    assert state_after_resume.paused is False


def test_completing_turn_gets_a_closing_note_not_the_first_question():
    """The last answer must close the set, not loop back to item 1.

    The Runtime clears ``current_item`` on completion, so the question hint
    was built from ``current_item or 1`` -- the writer was handed item 1 again
    on the very turn the scale finished, and settled for "好，那咱们继续。"
    """
    llm = FakeLLM()
    pipeline = build_pipeline(llm=llm)
    pipeline.scale_runtime.start("GAD-7")
    for item in range(1, 7):
        if item > 1:
            pipeline.scale_runtime.present_current_item()
        pipeline.scale_runtime.accept_answer(scale_name="GAD-7", item=item, score=1)
    pipeline.scale_runtime.present_current_item()
    try:
        result = pipeline.execute(
            PipelineConfig(user_text="有几天会这样"), lambda *_: None
        )
    finally:
        pipeline.shutdown()

    assert result.scale_completed is True
    suffix = llm.calls[-1]["system_suffix"]
    assert "【本轮收束】" in suffix
    assert "当前需要自然表达的问题是" not in suffix
