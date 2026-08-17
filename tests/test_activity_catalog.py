"""Phase A catalog validation and legacy activity preservation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from activity.catalog import ActivityCatalog, build_default_catalog
from activity.contracts import ActivityDefinition


def test_default_catalog_contains_six_new_and_existing_support_activities():
    catalog = build_default_catalog()

    for activity_id in (
        "trigger_detective",
        "refusal_rehearsal",
        "coping_toolbox",
        "crossroads",
        "ten_minute_buffer",
        "change_balance",
        "breathing",
        "muscle_relaxation",
        "meditation",
        "video",
        "game",
    ):
        assert catalog.get(activity_id).id == activity_id


def test_catalog_rejects_duplicate_ids_and_is_read_only():
    definition = build_default_catalog().get("trigger_detective")
    with pytest.raises(ValueError, match="duplicate"):
        ActivityCatalog([definition, definition])

    catalog = build_default_catalog()
    definitions = catalog.definitions
    with pytest.raises((TypeError, AttributeError)):
        definitions.append(definition)


def test_catalog_get_unknown_activity_fails_closed():
    catalog = build_default_catalog()
    assert catalog.get("not_registered") is None
    with pytest.raises(KeyError):
        catalog.require("not_registered")


def test_catalog_validation_rejects_unknown_stage_or_load():
    base = build_default_catalog().get("trigger_detective").model_dump()
    base["allowed_user_load"] = ("UNKNOWN",)
    with pytest.raises(ValidationError):
        ActivityDefinition(**base)

    base = build_default_catalog().get("trigger_detective").model_dump()
    base["allowed_conversation_stages"] = ("UNKNOWN",)
    with pytest.raises(ValidationError):
        ActivityDefinition(**base)
