"""De-identified per-turn decision and latency trace."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any


_LATENCY_FIELDS = (
    "vad_end_ms",
    "asr_ms",
    "agent_ms",
    "turn_policy_ms",
    "rag_ms",
    "dialogue_ttft_ms",
    "first_sentence_ms",
    "tts_first_audio_ms",
    "e2e_first_audio_ms",
)
_DECISION_FIELDS = (
    "turn_action",
    "session_state",
    "scale_state",
    "rag_used",
    "cancelled",
    "fallback_type",
    "error_category",
)


class TurnTraceRecorder:
    """Write only typed stage/decision fields; raw text is discarded."""

    def __init__(self, path: str | Path, *, session_id: str) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self._lock = threading.Lock()

    def record(self, *, turn_id: int, input_mode: str | None = None, **values: Any) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "turn_id": turn_id,
            "input_mode": input_mode,
        }
        for name in _LATENCY_FIELDS + _DECISION_FIELDS:
            record[name] = values.get(name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
        return record
