"""Phase 6 red/green tests for model output being non-authoritative."""

from __future__ import annotations

from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import (
    EmitCollector,
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeTTS,
)


def _pipeline(llm, agent):
    return ConversationPipeline(
        stt_service=None,
        llm_service=llm,
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=agent,
        report_service=FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
    )


def test_legacy_model_tags_cannot_score_end_or_start_media():
    agent = FakeAgent()
    agent.route_script = [{
        "action": "start_scale",
        "scale_name": "PHQ-9",
        "confidence": 0.95,
        "needs_rag": False,
    }]
    llm = FakeLLM([
        "普通回复[SCALE:PHQ-9:Q1:S3][REC_BREATHING][END_QUIT]",
    ])
    pipeline = _pipeline(llm, agent)
    try:
        result = pipeline.execute(
            PipelineConfig(use_stt=False, use_tts=False, user_text="最近睡不着"),
            EmitCollector(),
        )
        runtime = pipeline.scale_runtime.snapshot()

        assert runtime.active_scale == "PHQ-9"
        assert dict(runtime.answers_by_scale.get("PHQ-9", {})) == {}
        assert result.end_type is None
        assert result.relaxation_rec is None
    finally:
        pipeline.shutdown()
