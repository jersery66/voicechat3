"""Privacy and failure-isolation contracts for structured observability."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.deployment.observability import (
    SENSITIVE_KEYS,
    StructuredEventWriter,
    initialise_observability_artifacts,
)


def test_structured_event_has_ids_timing_and_error_fields(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = StructuredEventWriter(path, profile="rtxpro6000_96g", git_commit="test-commit")
    assert writer.emit(
        "llm_first_sentence",
        component="dialogue",
        status="SUCCESS",
        session_id="s1",
        turn_id="t1",
        generation_id=3,
        request_id="r1",
        duration_ms=12.5,
        error_code=None,
    ) is True
    event = json.loads(path.read_text(encoding="utf-8").strip())
    for key in ("schema_version", "timestamp_utc", "event_name", "component", "status", "session_id", "turn_id", "generation_id", "request_id", "profile", "git_commit", "duration_ms", "error_code"):
        assert key in event
    assert event["generation_id"] == 3
    assert event["duration_ms"] == 12.5


def test_sensitive_content_is_never_written_by_default(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = StructuredEventWriter(path)
    writer.emit(
        "dialogue_complete",
        component="dialogue",
        status="SUCCESS",
        diagnostic={
            "prompt": "secret prompt",
            "response": "secret response",
            "transcript": "secret transcript",
            "reasoning": "hidden reasoning",
            "safe_count": 2,
        },
    )
    event = json.loads(path.read_text(encoding="utf-8").strip())
    encoded = json.dumps(event, ensure_ascii=False).lower()
    for key in SENSITIVE_KEYS:
        assert f'"{key}"' not in encoded
    assert "safe_count" not in encoded


def test_observability_write_failure_is_best_effort(tmp_path):
    path = tmp_path / "directory"
    path.mkdir()
    writer = StructuredEventWriter(path)
    assert writer.emit("test", component="test", status="SUCCESS") is False


def test_observability_rejects_unknown_error_codes(tmp_path):
    writer = StructuredEventWriter(tmp_path / "events.jsonl")
    assert writer.emit("failure", component="test", status="FAILED", error_code="NOT_REAL") is False


def test_initialise_artifacts_are_not_run_and_do_not_contain_participant_text(tmp_path):
    paths = initialise_observability_artifacts(tmp_path, profile="rtxpro6000_96g", git_commit="test-commit")
    assert set(paths) == {"measurement_events", "memory_snapshots", "performance_summary", "observability_summary"}
    summary = json.loads(paths["performance_summary"].read_text(encoding="utf-8"))
    assert summary["status"] == "NOT RUN"
    assert summary["evidence_type"] == "NOT AVAILABLE"
    assert summary["samples"] == []


def test_measurement_modules_have_no_business_authority_imports():
    root = Path(__file__).resolve().parents[1] / "scripts" / "deployment"
    for name in ("measurement.py", "memory_snapshot.py", "observability.py", "error_taxonomy.py"):
        source = (root / name).read_text(encoding="utf-8")
        for forbidden in ("TurnDecision", "ScaleRuntime", "SessionEngine", "RAGService"):
            assert forbidden not in source
