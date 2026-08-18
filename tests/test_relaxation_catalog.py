"""Phase 1 RelaxationCatalog validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relaxation.catalog import RelaxationCatalog, build_default_catalog
from relaxation.contracts import RelaxationContentDefinition, RelaxationContentType


def _content(**overrides):
    values = {
        "id": "bubble_pop",
        "display_name": "泡泡",
        "category": RelaxationContentType.GAME,
        "enabled": True,
        "recommended_duration_seconds": 120,
        "max_duration_seconds": 300,
        "requires_mouse": True,
        "requires_audio": False,
        "requires_video": False,
        "resource_path": None,
        "implementation_type": "local_deterministic",
        "sort_order": 10,
        "implementation_status": "PLANNED",
    }
    values.update(overrides)
    return RelaxationContentDefinition(**values)


def test_default_catalog_contains_existing_and_v1_content_metadata():
    catalog = build_default_catalog()
    for content_id in (
        "breathing",
        "muscle_relaxation",
        "meditation",
        "bubble_pop",
        "gentle_search",
        "calm_puzzle",
        "falling_leaves",
    ):
        assert catalog.require(content_id).id == content_id


def test_catalog_lists_only_enabled_content_when_requested():
    catalog = RelaxationCatalog([_content(), _content(id="disabled", enabled=False)])
    assert [item.id for item in catalog.list_enabled()] == ["bubble_pop"]
    assert catalog.get("disabled").enabled is False


def test_catalog_rejects_duplicate_unknown_category_and_missing_name():
    definition = _content()
    with pytest.raises(ValueError, match="duplicate"):
        RelaxationCatalog([definition, definition])
    with pytest.raises(ValidationError):
        _content(category="UNKNOWN")
    with pytest.raises(ValidationError):
        _content(display_name="")


def test_catalog_rejects_invalid_duration_relationship():
    with pytest.raises(ValidationError):
        _content(recommended_duration_seconds=-1)
    with pytest.raises(ValidationError):
        _content(max_duration_seconds=60, recommended_duration_seconds=120)


def test_planned_content_is_not_reported_as_implemented():
    catalog = RelaxationCatalog([_content(implementation_status="PLANNED")])
    assert catalog.require("bubble_pop").implementation_status == "PLANNED"
    assert catalog.require("bubble_pop").is_available is False
