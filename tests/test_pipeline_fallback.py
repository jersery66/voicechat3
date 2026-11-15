"""What the pipeline says when the model says nothing.

An empty completion used to be surfaced as "不好意思，刚才没组织好语言。你能再说
一遍吗？".  In an interview that sentence does two bad things at once: it blames
the participant for a failure that was the model's, and it spends the turn on a
repetition instead of on the topic they just raised.  Measured on the 5-turn
fixed-text probe, the line appeared on 2 of 5 turns -- often enough to read as
the interviewer's habit rather than an accident.

The fallback now holds the turn: ``make_safe_fallback_reply`` answers with
something that follows what was just said, and a genuine provider failure says
so without asking for anything.
"""

from conversation.contracts import TurnAction, TurnDecision
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS


class EmptyCompletionLLM(FakeLLM):
    """Raises what ``LLMService`` raises when the stream ends with no content."""

    def chat(self, text, system_suffix="", *, commit_history=True):
        self.calls.append({"user_text": text, "system_suffix": system_suffix, "full": ""})
        self.conversation_history.append({"role": "user", "content": text})
        raise RuntimeError("LLM_NO_FINAL_CONTENT")
        yield ""  # pragma: no cover - keeps this a generator like the real client


class FailingLLM(EmptyCompletionLLM):
    def chat(self, text, system_suffix="", *, commit_history=True):
        self.calls.append({"user_text": text, "system_suffix": system_suffix, "full": ""})
        self.conversation_history.append({"role": "user", "content": text})
        raise RuntimeError("provider unavailable")
        yield ""  # pragma: no cover


def build_pipeline(llm):
    return ConversationPipeline(
        stt_service=None,
        llm_service=llm,
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
    )


def run_turn(pipeline, text):
    decision = TurnDecision(action=TurnAction.CHAT, strategy="deepen", stance="probe")
    return pipeline.execute(
        PipelineConfig(user_text=text, turn_decision=decision), lambda *_: None
    )


def test_an_empty_completion_holds_the_turn_instead_of_asking_for_a_repeat():
    llm = EmptyCompletionLLM()
    pipeline = build_pipeline(llm)
    try:
        result = run_turn(pipeline, "最近总睡不着，三四点就醒了")
    finally:
        pipeline.shutdown()

    assert "再说一遍" not in result.spoken_text
    assert "没组织好语言" not in result.spoken_text
    # The safe reply follows what he said rather than commenting on the model.
    assert "听" in result.spoken_text


def test_silence_with_no_speech_does_not_blame_the_participant():
    llm = EmptyCompletionLLM()
    pipeline = build_pipeline(llm)
    try:
        result = run_turn(pipeline, "")
    finally:
        pipeline.shutdown()

    assert "再说一遍" not in result.spoken_text
    assert "慢慢说" in result.spoken_text or "不着急" in result.spoken_text


def test_a_provider_failure_is_reported_as_ours_not_theirs():
    llm = FailingLLM()
    pipeline = build_pipeline(llm)
    try:
        result = run_turn(pipeline, "我说不太好")
    finally:
        pipeline.shutdown()

    assert "我这边" in result.spoken_text
    assert "再说一遍" not in result.spoken_text
