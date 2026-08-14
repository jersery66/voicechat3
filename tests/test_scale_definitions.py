"""Canonical scale-definition and score-domain contracts for Phase 3."""

import pytest

from services.scales import SCALES, get_scale_manager


@pytest.fixture()
def manager():
    return get_scale_manager()


@pytest.mark.parametrize(
    ("scale_name", "item_count", "legal_scores", "max_score"),
    [
        ("PHQ-9", 9, (0, 1, 2, 3), 27),
        ("GAD-7", 7, (0, 1, 2, 3), 21),
        ("PCL-5", 8, (0, 1, 2, 3, 4), 32),
    ],
)
def test_definition_accessors_expose_canonical_scale_contract(
    manager, scale_name, item_count, legal_scores, max_score
):
    definition = manager.get_scale_definition(scale_name)

    assert definition.item_count == item_count
    assert definition.legal_scores == legal_scores
    assert definition.max_score == max_score
    assert definition.questions == tuple(SCALES[scale_name]["questions"])


@pytest.mark.parametrize(
    ("scale_name", "score", "expected"),
    [
        ("PHQ-9", 3, True),
        ("PHQ-9", 4, False),
        ("GAD-7", 3, True),
        ("GAD-7", 4, False),
        ("PCL-5", 4, True),
    ],
)
def test_validate_answer_uses_definition_score_domain(manager, scale_name, score, expected):
    assert manager.validate_answer(scale_name, item=1, score=score) is expected


def test_validate_answer_rejects_unknown_scale_and_item(manager):
    assert manager.validate_answer("unknown", item=1, score=0) is False
    assert manager.validate_answer("PHQ-9", item=0, score=0) is False
    assert manager.validate_answer("PHQ-9", item=10, score=0) is False


def test_score_scale_reports_invalid_scores_instead_of_summing_them(manager):
    result = manager.score_scale("PHQ-9", [0, 4])

    assert result["error"] == "invalid_score"
    assert result["total"] is None
    assert result["items"] == 0
    assert result["max_score"] == 27


def test_score_scale_preserves_valid_partial_report_behavior(manager):
    result = manager.score_scale("PHQ-9", [0, 1, 2])

    assert result["total"] == 3
    assert result["items"] == 3
    assert result["max_score"] == 27
    assert "error" not in result
