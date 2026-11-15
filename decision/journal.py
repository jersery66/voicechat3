"""Append-only decision journal (metadata only).

Every field written here is either an enum value, a number, or a boolean.  The
journal has an explicit deny-list and raises away any accidental text field, so
a future caller cannot turn it into a transcript sink by adding a key.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

_FORBIDDEN_KEYS = frozenset(
    {
        "user_text",
        "text",
        "utterance",
        "transcript",
        "history",
        "recent_history",
        "content",
        "reply",
        "response",
        "prompt",
        "messages",
        "evidence_text",
    }
)


class DecisionJournalError(RuntimeError):
    """Raised when a journal record would carry free text."""


class DecisionJournal:
    """Write one JSONL line per decision turn."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, payload: Mapping[str, Any]) -> None:
        self.write(payload)

    def write(self, payload: Mapping[str, Any]) -> None:
        record = dict(payload)
        leaked = sorted(set(record) & _FORBIDDEN_KEYS)
        if leaked:
            raise DecisionJournalError(f"journal record contains text field(s): {leaked}")
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


def build_journal(path: Optional[str | Path]) -> Optional[DecisionJournal]:
    """Return a journal for ``path``, or ``None`` when journalling is off."""
    if not path:
        return None
    text = str(path).strip()
    if not text:
        return None
    return DecisionJournal(text)
