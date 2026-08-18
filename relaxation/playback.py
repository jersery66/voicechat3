"""Compatibility identifiers for the existing core video provider."""

from __future__ import annotations

_CONTENT_TO_LEGACY = {
    "breathing": "breathing",
    "muscle_relaxation": "muscle",
    "meditation": "meditation",
}


def legacy_relaxation_key(content_id: str) -> str | None:
    return _CONTENT_TO_LEGACY.get(content_id)


def content_id_from_legacy(value: str) -> str | None:
    for content_id, legacy_key in _CONTENT_TO_LEGACY.items():
        if value == legacy_key:
            return content_id
    return None
