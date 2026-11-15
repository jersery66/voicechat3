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


# --- Curated entries are written as "how to read it", not as protocols ---

COLLOQUIAL_HITS = [
    ("总觉得有人在背后说话，回头又没人", "幻觉应对策略"),
    ("老是听见有人叫我名字", "幻觉应对策略"),
    ("干什么都提不起劲，以前爱打的球也不想去了", "抑郁情绪干预"),
    ("有时候觉得脑子空的，不知道自己刚才在干嘛", "解离症状应对"),
    ("昨天差点跟他动手", "愤怒情绪管理"),
    ("有没有办法能静下来，脑子停不下来", "放松训练技术"),
]


@pytest.mark.parametrize("utterance,expected_title", COLLOQUIAL_HITS)
def test_colloquial_utterance_reaches_its_entry(utterance, expected_title):
    """Participants describe symptoms colloquially; retrieval must still land.

    These are the phrasings that used to return nothing: "有人在背后说话"
    never contains 幻听, "提不起劲" is not 提不起精神.  Without the synonym
    entries the curated text is unreachable even when the turn is clearly
    about that topic.
    """
    rag = RAGService(knowledge_base_path="knowledge_base")
    context = rag.get_context(utterance, enabled=True) or ""
    titles = [line.strip("【】") for line in context.split("\n") if line.startswith("【")]

    assert expected_title in titles


def test_curated_entries_are_not_numbered_protocols():
    rag = RAGService(knowledge_base_path="knowledge_base")

    for entry in rag.knowledge_base:
        content = entry["content"]
        assert "典型表现" in content, entry["title"]
        assert "怎么接" in content, entry["title"]
        assert "转出去" in content, entry["title"]
        assert not any(
            line.strip()[:2] in ("1.", "2.", "3.", "4.", "5.") for line in content.split("\n")
        ), entry["title"]


def test_missing_knowledge_file_seeds_from_curated_source_without_writing(tmp_path):
    """An empty base must not resurrect the old inline protocol list.

    _create_default_knowledge_base used to hold its own copy of the numbered
    exercises and write it to disk, which silently undid any curation.
    """
    rag = RAGService(knowledge_base_path=str(tmp_path))

    assert rag.knowledge_base, "seeded from the curated file"
    assert all("典型表现" in entry["content"] for entry in rag.knowledge_base)
    assert not (tmp_path / "knowledge.json").exists()


# --- Repetition gate -------------------------------------------------------
# Ten curated entries, one participant, maybe twenty turns: at most one entry
# per turn, and never the same entry twice.  Otherwise the same paragraph is
# paraphrased back at them two turns later.

def test_same_topic_is_injected_once_per_session():
    rag = RAGService(knowledge_base_path="knowledge_base")

    first = rag.get_context("最近一直睡不着，三四点就醒了", enabled=True)
    second = rag.get_context("昨晚又熬到三点，还是睡不着", enabled=True)

    assert first and "失眠" in first
    assert second is None


def test_a_different_topic_still_gets_its_own_entry():
    rag = RAGService(knowledge_base_path="knowledge_base")
    rag.get_context("最近一直睡不着，三四点就醒了", enabled=True)

    context = rag.get_context("有时候突然心慌，喘不上气", enabled=True)

    assert context and "焦虑" in context


def test_reset_session_allows_the_same_entry_again():
    rag = RAGService(knowledge_base_path="knowledge_base")
    assert rag.get_context("最近一直睡不着，三四点就醒了", enabled=True)
    assert rag.get_context("昨晚又熬到三点，还是睡不着", enabled=True) is None

    rag.reset_session()

    assert rag.get_context("昨晚又熬到三点，还是睡不着", enabled=True)


def test_one_weak_match_does_not_get_injected():
    """A bare acknowledgement must not drag in a topic entry.

    MIN_SCORE requires a curated keyword hit; a title-only 4.5 stays out.
    """
    rag = RAGService(knowledge_base_path="knowledge_base")

    assert rag.get_context("嗯", enabled=True) is None


def test_at_most_one_entry_per_turn():
    rag = RAGService(knowledge_base_path="knowledge_base")

    context = rag.get_context("心里空得慌，什么都不想干，也提不起劲，还总睡不着", enabled=True) or ""

    assert context.count("【") <= 1


# --- Repetition gate -------------------------------------------------------
# Ten curated entries, one participant, maybe twenty turns: at most one entry
# per turn, and never the same entry twice.  Otherwise the same paragraph is
# paraphrased back at them two turns later.

def test_same_topic_is_injected_once_per_session():
    rag = RAGService(knowledge_base_path="knowledge_base")

    first = rag.get_context("最近一直睡不着，三四点就醒了", enabled=True)
    second = rag.get_context("昨晚又熬到三点，还是睡不着", enabled=True)

    assert first and "失眠" in first
    assert second is None


def test_a_different_topic_still_gets_its_own_entry():
    rag = RAGService(knowledge_base_path="knowledge_base")
    rag.get_context("最近一直睡不着，三四点就醒了", enabled=True)

    context = rag.get_context("有时候突然心慌，喘不上气", enabled=True)

    assert context and "焦虑" in context


def test_reset_session_allows_the_same_entry_again():
    rag = RAGService(knowledge_base_path="knowledge_base")
    assert rag.get_context("最近一直睡不着，三四点就醒了", enabled=True)
    assert rag.get_context("昨晚又熬到三点，还是睡不着", enabled=True) is None

    rag.reset_session()

    assert rag.get_context("昨晚又熬到三点，还是睡不着", enabled=True)


def test_one_weak_match_does_not_get_injected():
    """A bare acknowledgement must not drag in a topic entry.

    MIN_SCORE requires a curated keyword hit; a title-only 4.5 stays out.
    """
    rag = RAGService(knowledge_base_path="knowledge_base")

    assert rag.get_context("嗯", enabled=True) is None


def test_at_most_one_entry_per_turn():
    rag = RAGService(knowledge_base_path="knowledge_base")

    context = rag.get_context("心里空得慌，什么都不想干，也提不起劲，还总睡不着", enabled=True) or ""

    assert context.count("【") <= 1
