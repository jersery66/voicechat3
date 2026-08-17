"""Privacy-preserving structured observability and artifact initialization."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.deployment.error_taxonomy import ERROR_CODES

EVIDENCE_MEASURED = "MEASURED"
EVIDENCE_SIMULATED = "SIMULATED"
EVIDENCE_NOT_AVAILABLE = "NOT AVAILABLE"

# Keys whose values could contain participant content or hidden reasoning.  The
# writer rejects them rather than trying to redact arbitrary nested payloads.
SENSITIVE_KEYS = {
    "prompt",
    "response",
    "transcript",
    "audio",
    "clinical_text",
    "clinical_score",
    "reasoning",
    "content",
    "raw_text",
    "spoken_text",
    "tts_text",
    "question",
    "answer",
    "diagnostic",
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class StructuredEventWriter:
    """Best-effort JSONL writer for identity/status/timing metadata only."""

    def __init__(self, path: str | Path, *, profile: str | None = None, git_commit: str | None = None) -> None:
        self.path = Path(path)
        self.profile = profile
        self.git_commit = git_commit

    def _event(
        self,
        event_name: str,
        *,
        component: str,
        status: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        generation_id: int | None = None,
        request_id: str | None = None,
        duration_ms: float | None = None,
        error_code: str | None = None,
        evidence_type: str = EVIDENCE_NOT_AVAILABLE,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "timestamp_utc": _timestamp(),
            "event_name": event_name,
            "component": component,
            "status": status,
            "session_id": session_id,
            "turn_id": turn_id,
            "generation_id": generation_id,
            "request_id": request_id,
            "profile": self.profile,
            "git_commit": self.git_commit,
            "duration_ms": duration_ms,
            "error_code": error_code,
            "evidence_type": evidence_type,
        }

    def emit(
        self,
        event_name: str,
        *,
        component: str,
        status: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        generation_id: int | None = None,
        request_id: str | None = None,
        duration_ms: float | None = None,
        error_code: str | None = None,
        evidence_type: str = EVIDENCE_NOT_AVAILABLE,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> bool:
        # ``diagnostic`` is accepted only as a compatibility sink; it is never
        # serialized.  This makes it impossible for a caller to accidentally
        # put participant text into the default telemetry stream.
        del diagnostic
        if error_code is not None and error_code not in ERROR_CODES:
            print(f"observability rejected unknown error code: {error_code}", file=sys.stderr)
            return False
        event = self._event(
            event_name,
            component=component,
            status=status,
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            request_id=request_id,
            duration_ms=duration_ms,
            error_code=error_code,
            evidence_type=evidence_type,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            return True
        except Exception as exc:  # best effort: never alter the business path
            print(f"observability write failed: {exc}", file=sys.stderr)
            return False


def initialise_observability_artifacts(
    output_root: str | Path,
    *,
    profile: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Path]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    measurement_events = root / "measurement_events.jsonl"
    memory_snapshots = root / "memory_snapshots.jsonl"
    performance_summary = root / "performance_summary.json"
    observability_summary = root / "observability_summary.json"
    measurement_events.touch(exist_ok=True)
    memory_snapshots.touch(exist_ok=True)
    performance_summary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "timestamp_utc": timestamp,
                "profile": profile,
                "git_commit": git_commit,
                "status": "NOT RUN",
                "evidence_type": EVIDENCE_NOT_AVAILABLE,
                "samples": [],
                "real_performance_summary": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    observability_summary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "timestamp_utc": timestamp,
                "profile": profile,
                "git_commit": git_commit,
                "status": "NOT RUN",
                "evidence_type": EVIDENCE_NOT_AVAILABLE,
                "content_logging": "OFF",
                "measurement_events": str(measurement_events.name),
                "memory_snapshots": str(memory_snapshots.name),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "measurement_events": measurement_events,
        "memory_snapshots": memory_snapshots,
        "performance_summary": performance_summary,
        "observability_summary": observability_summary,
    }
