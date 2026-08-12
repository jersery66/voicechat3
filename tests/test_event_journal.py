"""Research events are append-only and do not require a running UI."""

import json

from research.event_journal import EventJournal


def test_append_persists_a_jsonl_record_with_generated_timestamp(tmp_path):
    path = tmp_path / "research" / "events.jsonl"
    journal = EventJournal(path)

    journal.append("policy_decision", {"action": "chat"}, session_id="session-1")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["type"] == "policy_decision"
    assert record["session_id"] == "session-1"
    assert record["ts"]
