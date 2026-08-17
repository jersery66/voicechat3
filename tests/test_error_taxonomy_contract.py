"""Stable machine-readable error taxonomy contracts."""

from __future__ import annotations

import pytest

from scripts.deployment.error_taxonomy import (
    ERROR_CATEGORIES,
    ERROR_CODES,
    classify_error,
    error_entry,
)


def test_required_error_codes_are_stable_and_categorised():
    for code in (
        "MODEL_IDENTITY_MISMATCH",
        "ENDPOINT_UNAVAILABLE",
        "UNKNOWN_PORT_OWNER",
        "PROCESS_OWNERSHIP_MISMATCH",
        "LLM_TIMEOUT",
        "LLM_EMPTY_RESPONSE",
        "LLM_STREAM_INTERRUPTED",
        "STT_PROVIDER_ERROR",
        "VAD_PROVIDER_ERROR",
        "TTS_PROVIDER_ERROR",
        "AUDIO_DEVICE_UNAVAILABLE",
        "DELIVERY_CANCELLED",
        "STALE_GENERATION_DROPPED",
    ):
        assert code in ERROR_CODES
        assert ERROR_CODES[code] in ERROR_CATEGORIES


def test_error_entry_contains_category_code_without_sensitive_text():
    entry = error_entry("LLM_TIMEOUT", component="dialogue", status="FAILED")
    assert entry["category"] == "DIALOGUE"
    assert entry["code"] == "LLM_TIMEOUT"
    assert entry["component"] == "dialogue"
    assert "prompt" not in entry
    assert "response" not in entry


def test_unknown_error_code_fails_closed():
    with pytest.raises(ValueError):
        error_entry("NOT_A_REAL_ERROR", component="test")


def test_classify_error_does_not_choose_recovery_policy():
    result = classify_error("MODEL_IDENTITY_MISMATCH", detail="wrong model")
    assert result["code"] == "MODEL_IDENTITY_MISMATCH"
    assert "recovery" not in result
    assert "action" not in result
