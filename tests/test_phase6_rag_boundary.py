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
