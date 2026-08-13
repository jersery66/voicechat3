"""Tests for services.rag_service — synonym expansion and scoring."""

from pathlib import Path

import pytest
from services.rag_service import RAGService


@pytest.fixture
def rag_service(tmp_path):
    """Create a RAGService with a temporary knowledge base."""
    return RAGService(knowledge_base_path=str(tmp_path))


class TestExpandQuery:
    """_expand_query should expand colloquial terms via SYNONYM_MAP."""

    def test_expands_sleep_term(self, rag_service):
        expanded = rag_service._expand_query("睡不着")
        assert "失眠" in expanded
        assert "睡眠障碍" in expanded

    def test_expands_anxiety_term(self, rag_service):
        expanded = rag_service._expand_query("心里堵")
        assert "焦虑" in expanded

    def test_expands_addiction_term(self, rag_service):
        expanded = rag_service._expand_query("忍不住")
        assert "渴求" in expanded
        assert "戒断" in expanded

    def test_no_expansion_for_unknown(self, rag_service):
        expanded = rag_service._expand_query("xyz123")
        # Should at least contain the original query
        assert "xyz123" in expanded

    def test_caches_results(self, rag_service):
        r1 = rag_service._expand_query("睡不着")
        r2 = rag_service._expand_query("睡不着")
        assert r1 is r2  # Same object from cache


class TestScoreEntry:
    """_score_entry scores entries based on keyword/title/content matches."""

    def test_keyword_hit_scores_high(self, rag_service):
        entry = {
            "keywords": ["失眠", "睡眠"],
            "title": "失眠干预",
            "content": "针对失眠的干预方法",
        }
        expanded = {"失眠", "睡眠障碍"}
        score = rag_service._score_entry(entry, expanded, "睡不着")
        assert score >= 3.0  # At least one keyword hit

    def test_title_hit_scores_medium(self, rag_service):
        entry = {
            "keywords": ["无关"],
            "title": "焦虑情绪管理",
            "content": "内容",
        }
        expanded = {"焦虑"}
        score = rag_service._score_entry(entry, expanded, "焦虑")
        assert score >= 1.5

    def test_content_only_scores_low(self, rag_service):
        entry = {
            "keywords": ["无关"],
            "title": "无关标题",
            "content": "这里提到了焦虑情绪",
        }
        expanded = {"焦虑"}
        score = rag_service._score_entry(entry, expanded, "焦虑")
        # Content-only match should be penalized (no keyword/title hit)
        assert score < 1.0

    def test_no_match_scores_zero(self, rag_service):
        entry = {
            "keywords": ["无关"],
            "title": "无关标题",
            "content": "无关内容",
        }
        expanded = {"焦虑"}
        score = rag_service._score_entry(entry, expanded, "焦虑")
        assert score == 0.0

    def test_domain_boost_for_core_entries(self, rag_service):
        entry = {
            "id": "entry_1",  # core knowledge
            "keywords": ["失眠"],
            "title": "失眠干预",
            "content": "内容",
        }
        expanded = {"失眠"}
        score_core = rag_service._score_entry(entry, expanded, "失眠")

        entry_lazy = {
            "id": "cpsycounr_001",  # lazy-loaded
            "keywords": ["失眠"],
            "title": "失眠干预",
            "content": "内容",
        }
        score_lazy = rag_service._score_entry(entry_lazy, expanded, "失眠")

        assert score_core > score_lazy  # Core entries get higher domain boost


def test_core_rag_contains_no_crisis_specific_entry(tmp_path):
    source = Path("knowledge_base/knowledge.json")
    (tmp_path / "knowledge.json").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rag = RAGService(knowledge_base_path=str(tmp_path))

    assert all(entry["title"] != "危机干预与自杀预防" for entry in rag.knowledge_base)
    assert "危机干预" not in "\n".join(entry["content"] for entry in rag.knowledge_base)
