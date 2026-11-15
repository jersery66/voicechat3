"""Phase 6 red/green tests for curated, decision-gated production RAG."""

from __future__ import annotations

import json
from pathlib import Path

from services.rag_service import RAGService


ROOT = Path(__file__).resolve().parents[1]


def test_production_rag_has_no_lazy_converted_corpora():
    assert RAGService.CORE_FILES == ["knowledge.json"]
    assert RAGService.LAZY_FILES == []

    source = (ROOT / "services" / "rag_service.py").read_text(encoding="utf-8")
    for marker in ("cpsycounr_converted", "psyqa_converted", "emollm_single_turn", "emollm_multi_turn"):
        assert marker not in source


def test_rag_does_not_call_second_model_for_retrieval(monkeypatch, tmp_path):
    (tmp_path / "knowledge.json").write_text(
        json.dumps([
            {
                "keywords": ["普通"],
                "title": "普通支持",
                "content": "先从当下感受聊起。",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    rag = RAGService(knowledge_base_path=str(tmp_path))
    called = []

    class UnexpectedAgent:
        def classify_rag_intent(self, text):
            called.append(text)
            raise AssertionError("production RAG must not call a second model")

    monkeypatch.setattr(
        "services.agent_service.get_agent_service",
        lambda: UnexpectedAgent(),
    )

    suffix = rag.get_system_suffix("这是一句没有专业关键词的普通表达", enabled=True)

    assert called == []
    assert "普通支持" in (suffix or "")
    assert "|||" not in (suffix or "")


def test_rag_gate_can_disable_retrieval_without_keyword_heuristics(tmp_path):
    (tmp_path / "knowledge.json").write_text(
        json.dumps([
            {
                "keywords": ["焦虑"],
                "title": "焦虑支持",
                "content": "先关注呼吸和当下。",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    rag = RAGService(knowledge_base_path=str(tmp_path))

    assert rag.get_system_suffix("我很焦虑", enabled=False) is None
    rag._simple_search = lambda query, top_k=3: [{
        "title": "焦虑支持",
        "content": "先关注呼吸和当下。",
    }]
    assert "焦虑支持" in (rag.get_system_suffix("随便聊聊", enabled=True) or "")


def test_rag_is_disabled_without_an_explicit_decision_gate(tmp_path):
    (tmp_path / "knowledge.json").write_text(
        json.dumps([{"keywords": ["焦虑"], "title": "焦虑支持", "content": "内容"}], ensure_ascii=False),
        encoding="utf-8",
    )
    rag = RAGService(knowledge_base_path=str(tmp_path))

    assert rag.get_system_suffix("我很焦虑") is None


def test_production_rag_does_not_reference_safety_resources():
    source = (ROOT / "services" / "rag_service.py").read_text(encoding="utf-8")

    assert "safety/resources" not in source.replace("\\", "/")
    assert "crisis_knowledge.json" not in source


def test_rag_reduces_protocol_entries_to_a_topic_signal(tmp_path):
    """A numbered exercise protocol must not reach the writer verbatim.

    knowledge.json is written as "1. ... 2. ..." intervention protocols; read
    out loud they turn a conversation turn into an exercise walkthrough, which
    the participant prompt forbids.
    """
    (tmp_path / "knowledge.json").write_text(
        json.dumps([{"keywords": ["失眠"], "title": "失眠干预技术", "content": "占位"}], ensure_ascii=False),
        encoding="utf-8",
    )
    rag = RAGService(knowledge_base_path=str(tmp_path))
    rag._simple_search = lambda query, top_k=3: [{
        "title": "失眠干预技术",
        "content": "针对失眠：\n1. 睡眠卫生教育\n2. 睡前做腹式呼吸\n3. 渐进式肌肉放松",
    }]

    suffix = rag.get_system_suffix("随便聊聊", enabled=True) or ""

    assert "失眠干预技术" in suffix
    assert "这个话题可以识别" in suffix
    assert "腹式呼吸" not in suffix
    assert "肌肉放松" not in suffix
    assert "1." not in suffix


def test_rag_keeps_plain_prose_entries_verbatim(tmp_path):
    (tmp_path / "knowledge.json").write_text(
        json.dumps([{"keywords": ["家庭"], "title": "家庭沟通", "content": "占位"}], ensure_ascii=False),
        encoding="utf-8",
    )
    rag = RAGService(knowledge_base_path=str(tmp_path))
    rag._simple_search = lambda query, top_k=3: [{
        "title": "家庭沟通",
        "content": "先听他说完，不急着给建议。",
    }]

    suffix = rag.get_system_suffix("随便聊聊", enabled=True) or ""

    assert "先听他说完" in suffix
    assert "这个话题可以识别" not in suffix


def test_task_turns_never_retrieve_even_when_the_decision_says_so():
    """Only plain chat may carry topic knowledge.

    The curated set contains an assessment-tools entry; injecting it on a
    scale turn is at best noise and at worst invites the writer to talk about
    the questionnaire.  This pins the action gate, not just the needs_rag bit.
    """
    from conversation.contracts import TurnAction
    from conversation.turn_policy import TurnPolicy
    from services.pipeline import ConversationPipeline, PipelineConfig
    from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS

    class RagAlwaysOnPolicy(TurnPolicy):
        def decide(self, **kwargs):
            return super().decide(**kwargs).model_copy(update={"needs_rag": True})

    rag = FakeRAG()
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=rag,
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
        turn_policy=RagAlwaysOnPolicy(),
    )
    try:
        pipeline.scale_runtime.start("PHQ-9")
        pipeline.scale_runtime.present_current_item()
        scale_turn = pipeline.execute(
            PipelineConfig(user_text="前两天有点睡不着"), lambda *_: None
        )
    finally:
        pipeline.shutdown()

    assert scale_turn.turn_decision.action is TurnAction.CONTINUE_SCALE
    # The scale turn asked for retrieval and still got none.
    assert rag.queries == []


def test_plain_chat_is_the_only_turn_that_retrieves():
    """Counterpart of the test above: the same gate is open on a chat turn."""
    from conversation.contracts import TurnAction
    from conversation.turn_policy import TurnPolicy
    from services.pipeline import ConversationPipeline, PipelineConfig
    from tests.integration.fakes import FakeAgent, FakeData, FakeLLM, FakeRAG, FakeReport, FakeTTS

    class RagAlwaysOnPolicy(TurnPolicy):
        def decide(self, **kwargs):
            return super().decide(**kwargs).model_copy(update={"needs_rag": True})

    rag = FakeRAG()
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=rag,
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=6),
        data_manager=FakeData(),
        session_emotions=[],
        turn_policy=RagAlwaysOnPolicy(),
    )
    try:
        turn = pipeline.execute(
            PipelineConfig(user_text="这两天翻来覆去到天亮，白天一点力气都没有"),
            lambda *_: None,
        )
    finally:
        pipeline.shutdown()

    assert turn.turn_decision.action is TurnAction.CHAT
    assert rag.queries == [turn.user_text]
