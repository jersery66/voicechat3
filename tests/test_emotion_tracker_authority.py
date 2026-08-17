"""Final pre-hardware authority and session-statistics closure tests."""

from __future__ import annotations

from conversation.contracts import RouterAction, RouterProposal, TurnAction
from services.emotion_tracker import EmotionTracker
from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS


def _tracker_with_negative_streak() -> EmotionTracker:
    tracker = EmotionTracker()
    for _ in range(3):
        tracker.add_emotion({"emotion": "anxious", "intensity": 0.8})
    return tracker


def test_emotion_hint_is_style_only_and_cannot_recommend_intervention():
    hint = _tracker_with_negative_streak().get_intervention_hint()

    assert hint is not None
    assert "放慢节奏" in hint
    for forbidden in ("深呼吸", "放松训练", "冥想", "肌肉放松", "推荐"):
        assert forbidden not in hint


def test_chat_decision_does_not_receive_concrete_relaxation_from_tracker():
    tracker = _tracker_with_negative_streak()
    llm = FakeLLM(["合成回应。"])
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=llm,
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=None,
        report_service=FakeReport(start_round=0),
        data_manager=FakeData(),
        session_emotions=[],
        emotion_tracker=tracker,
    )
    try:
        record = pipeline.delivery_controller.start_generation()
        result = pipeline.execute(
            PipelineConfig(
                user_text="合成输入",
                router_proposal=RouterProposal(action=RouterAction.CHAT, needs_rag=False),
                generation_id=record.generation_id,
            ),
            lambda *_: None,
        )
        suffix = llm.calls[0]["system_suffix"]
        assert result.turn_decision.action is TurnAction.CHAT
        for forbidden in ("深呼吸", "放松训练", "冥想", "肌肉放松"):
            assert forbidden not in suffix
    finally:
        pipeline.shutdown()


def test_session_peak_statistics_survive_a_later_neutral_turn():
    tracker = EmotionTracker()
    for _ in range(5):
        tracker.add_emotion({"emotion": "anxious", "intensity": 0.8})
    tracker.add_emotion({"emotion": "neutral", "intensity": 0.1})

    current = tracker.get_emotion_data()
    session = tracker.get_session_emotion_data()

    assert current["negative_streak"] == 0
    assert current["peak_intensity"] == 0.0
    assert session["negative_streak_peak"] == 5
    assert session["peak_intensity"] == 0.8


def test_reset_returns_session_peaks_and_clears_them_for_next_session():
    tracker = _tracker_with_negative_streak()
    previous = tracker.reset()

    assert previous["negative_streak_peak"] == 3
    assert previous["peak_intensity"] == 0.8
    fresh = tracker.get_session_emotion_data()
    assert fresh["negative_streak_peak"] == 0
    assert fresh["peak_intensity"] == 0.0
