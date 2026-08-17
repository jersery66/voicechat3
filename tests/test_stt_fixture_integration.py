"""Synthetic STT boundary integration; real FunASR remains NOT RUN."""

from __future__ import annotations

from dataclasses import dataclass

from services.pipeline import ConversationPipeline
from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS


@dataclass
class SyntheticSTT:
    final_text: str = ""
    partial_text: str = ""
    error: Exception | None = None
    calls: int = 0

    def transcribe(self, _audio):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.final_text


def _pipeline(stt):
    return ConversationPipeline(
        stt_service=stt,
        llm_service=FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(),
        data_manager=FakeData(),
        session_emotions=[],
    )


def test_fixture_normal_utterance_hands_off_one_final_text():
    stt = SyntheticSTT(final_text="最近心里有点累")
    pipeline = _pipeline(stt)
    try:
        events = []
        assert pipeline.transcribe([1, 2, 3], lambda kind, value: events.append((kind, value))) == "最近心里有点累"
        assert stt.calls == 1
        assert [value for kind, value in events if kind == "status"] == ["正在转写..."]
    finally:
        pipeline.shutdown()


def test_fixture_preserves_negation_frequency_duration_quantity_and_symptom_text():
    text = "不是每天，大概两三天，持续了四周，主要是睡不着。"
    stt = SyntheticSTT(final_text=text, partial_text="不是每天")
    pipeline = _pipeline(stt)
    try:
        assert pipeline.transcribe([1], lambda *_: None) == text
        assert stt.calls == 1
    finally:
        pipeline.shutdown()


def test_silence_and_empty_final_do_not_create_final_input():
    stt = SyntheticSTT(final_text="")
    pipeline = _pipeline(stt)
    try:
        events = []
        assert pipeline.transcribe([], lambda kind, value: events.append((kind, value))) == ""
        assert stt.calls == 0
        assert pipeline.transcribe([1], lambda kind, value: events.append((kind, value))) == ""
        assert stt.calls == 1
    finally:
        pipeline.shutdown()


def test_provider_failure_does_not_become_normal_final_text():
    stt = SyntheticSTT(error=RuntimeError("fixture STT failure"))
    pipeline = _pipeline(stt)
    try:
        try:
            pipeline.transcribe([1], lambda *_: None)
        except RuntimeError as exc:
            assert "fixture STT failure" in str(exc)
        else:
            raise AssertionError("provider failure was swallowed")
    finally:
        pipeline.shutdown()


def test_fixture_capture_emits_final_once_and_never_partial_text():
    stt = SyntheticSTT(final_text="最终完整句子。", partial_text="最终")
    final_outputs = []
    frames = [[0.0] * 1600, [0.0] * 1600]
    # The fixture models an endpointed utterance: only the final provider
    # result crosses the production handoff boundary.
    for _frame in frames:
        pass
    final_outputs.append(stt.transcribe(frames))
    assert final_outputs == ["最终完整句子。"]
    assert stt.partial_text not in final_outputs
