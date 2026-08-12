"""Append-only, de-identified JSONL event journal."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class EventJournal:
    """Persists operational events without raw participant messages by default."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event_type: str, payload: dict[str, Any] | BaseModel,
               *, session_id: str | None = None) -> None:
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="json")
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "session_id": session_id,
            "payload": payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
