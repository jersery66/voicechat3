"""Tests for core.scoring — scale scoring & symptom signal detection.

Locks the canonical import path and covers the regression-baseline items
F14 (cumulative symptom scoring) and F17 (scale answer parsing fallback)
from docs/refactor/01_feature_inventory.md.
"""

import pytest

from core.scoring import (
    FREQUENCY_WORDS,
    GAD7_POSITIVE_KEYWORDS_BY_ITEM,
    PHQ_POSITIVE_KEYWORDS_BY_ITEM,
    detect_phq_item_from_text,
    infer_scale_score_from_text,
    is_scale_interruption_text,
    is_user_explicit_end_text,
    score_symptom_signals,
)


class TestInferScaleScore:
    def test_denial_scores_zero(self):
        assert infer_scale_score_from_text("没有", "PHQ-9") == 0
        assert infer_scale_score_from_text("完全不会", "GAD-7") == 0

    def test_frequency_scores(self):
        assert infer_scale_score_from_text("好几天", "PHQ-9") == 1
        assert infer_scale_score_from_text("一半以上的时间", "PHQ-9") == 2
        assert infer_scale_score_from_text("几乎每天", "GAD-7") == 3

    def test_item_aware_guard_blocks_mismatched_symptom(self):
        # Regression case from the original docstring: mentions tension every
        # day but that is not PHQ-9 Q5 (appetite). Must NOT score Q5.
        text = "没有，在戒毒所里面基本每天都很紧张"
        assert infer_scale_score_from_text(text, "PHQ-9", item=5) is None

    def test_item_aware_allows_matching_symptom(self):
        text = "睡不着，几乎每天"
        assert infer_scale_score_from_text(text, "PHQ-9", item=3) == 3

    def test_pcl5_severity(self):
        assert infer_scale_score_from_text("有一点", "PCL-5") == 1
        assert infer_scale_score_from_text("中等程度", "PCL-5") == 2
        assert infer_scale_score_from_text("相当严重", "PCL-5") == 3
        assert infer_scale_score_from_text("极度严重", "PCL-5") == 4

    def test_empty_returns_none(self):
        assert infer_scale_score_from_text("", "PHQ-9") is None


class TestDetectPhqItem:
    def test_sleep_maps_to_q3(self):
        assert detect_phq_item_from_text("最近总是失眠") == 3

    def test_suicidal_maps_to_q9(self):
        assert detect_phq_item_from_text("有时候不想活了") == 9

    def test_no_match(self):
        assert detect_phq_item_from_text("今天天气不错") is None


class TestExplicitEnd:
    def test_weak_response_not_end(self):
        assert is_user_explicit_end_text("好吧") is False
        assert is_user_explicit_end_text("嗯") is False

    def test_explicit_end(self):
        assert is_user_explicit_end_text("结束吧") is True
        assert is_user_explicit_end_text("今天不聊了") is True

    def test_empty_not_end(self):
        assert is_user_explicit_end_text("") is False


class TestSymptomSignals:
    def test_depression_signal_hits_phq(self):
        deltas, reasons = score_symptom_signals("最近一直睡不着，很累")
        assert deltas["PHQ-9"] >= 2
        assert deltas["GAD-7"] < deltas["PHQ-9"]

    def test_anxiety_signal_hits_gad(self):
        deltas, _ = score_symptom_signals("心里很紧张，心慌")
        assert deltas["GAD-7"] >= 2

    def test_trauma_signal_hits_pcl(self):
        deltas, _ = score_symptom_signals("老是做噩梦，不敢想那件事")
        assert deltas["PCL-5"] >= 2

    def test_evasive_only_counts_when_signal_exists(self):
        deltas_fresh, _ = score_symptom_signals("不知道", {})
        deltas_with_signal, _ = score_symptom_signals("不知道", {"PHQ-9": 1, "GAD-7": 0, "PCL-5": 0})
        assert deltas_fresh["PHQ-9"] == 0
        assert deltas_with_signal["PHQ-9"] >= 1

    def test_empty_text(self):
        deltas, reasons = score_symptom_signals("")
        assert deltas == {} and reasons == []

    def test_rehab_context_boost(self):
        deltas, _ = score_symptom_signals("在戒毒所里很孤独，心里难受")
        assert deltas["PHQ-9"] >= 2  # low mood + rehab context


class TestScaleInterruption:
    def test_resistance_detected(self):
        assert is_scale_interruption_text("别问了，换个话题") is True
        assert is_scale_interruption_text("我不是来做问卷的") is True

    def test_normal_answer_not_interruption(self):
        assert is_scale_interruption_text("最近睡眠不太好") is False


class TestKeywordTables:
    """Sanity checks on the clinical keyword data (completeness)."""

    def test_phq9_has_all_nine_items(self):
        assert set(PHQ_POSITIVE_KEYWORDS_BY_ITEM.keys()) == set(range(1, 10))

    def test_gad7_has_all_seven_items(self):
        assert set(GAD7_POSITIVE_KEYWORDS_BY_ITEM.keys()) == set(range(1, 8))

    def test_frequency_words_nonempty(self):
        assert len(FREQUENCY_WORDS) > 5
