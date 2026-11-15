"""The quit banner is a UI affordance, never transcript content.

Regression cover for the overwrite seen on 2026-11-06: the assistant message
writer reused the current turn index, so the "session is ending" banner
replaced ``002_assistant.txt`` and the real reply was lost on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from conversation.delivery import DeliveryLedger, GenerationController, SentenceReady
from data.data_manager import DataManager

BANNER = "本次会话已结束，系统正在保存报告。"


class _History:
    def __init__(self):
        self.conversation_history = []


class _Data:
    def __init__(self):
        self.writes = []

    def save_assistant_message(self, _audio, text, sample_rate=48000):
        self.writes.append(text)


def test_visible_only_generation_reaches_screen_but_not_disk_or_history():
    controller = GenerationController()
    ledger = DeliveryLedger(controller)
    history = _History()
    data = _Data()

    banner = controller.start_generation()
    ledger.record_generated(banner.generation_id, BANNER)
    assert ledger.commit_visible(SentenceReady(banner.generation_id, 0, BANNER))

    delivered = ledger.finalize_history(
        banner.generation_id, history, data, persist=False
    )

    # Still delivered to the UI...
    assert delivered == BANNER
    # ...but no transcript write and no history pollution.
    assert data.writes == []
    assert history.conversation_history == []


def test_persist_false_is_idempotent_with_the_committed_guard():
    controller = GenerationController()
    ledger = DeliveryLedger(controller)
    history = _History()
    data = _Data()

    record = controller.start_generation()
    ledger.record_generated(record.generation_id, BANNER)
    ledger.commit_visible(SentenceReady(record.generation_id, 0, BANNER))

    assert ledger.finalize_history(record.generation_id, history, data, persist=False) == BANNER
    assert ledger.finalize_history(record.generation_id, history, data, persist=True) == BANNER
    assert data.writes == []


def test_real_turn_still_persists_after_a_non_persisted_generation():
    controller = GenerationController()
    ledger = DeliveryLedger(controller)
    history = _History()
    data = _Data()

    banner = controller.start_generation()
    ledger.record_generated(banner.generation_id, BANNER)
    ledger.commit_visible(SentenceReady(banner.generation_id, 0, BANNER))
    ledger.finalize_history(banner.generation_id, history, data, persist=False)

    answer = controller.start_generation()
    ledger.record_generated(answer.generation_id, "嗯，一般就是还行吧。")
    ledger.commit_visible(SentenceReady(answer.generation_id, 0, "嗯，一般就是还行吧。"))
    assert ledger.finalize_history(answer.generation_id, history, data) == "嗯，一般就是还行吧。"

    assert data.writes == ["嗯，一般就是还行吧。"]
    assert history.conversation_history == [
        {"role": "assistant", "content": "嗯，一般就是还行吧。"}
    ]


def test_quit_banner_does_not_overwrite_the_turn_transcript(tmp_path):
    """The exact 2026-11-06 failure: the banner reused index 002 and replaced
    the real assistant reply inside 002_assistant.txt."""
    manager = DataManager(data_root=str(tmp_path))
    manager.current_subject_id = "003"
    manager.start_new_session()
    session = Path(manager.session_dir)

    manager.save_user_message(None, "你好")
    manager.save_assistant_message(None, "嗨，来了呀。最近怎么样？")
    manager.save_user_message(None, "一般")
    manager.save_assistant_message(None, "嗯，一般就是还行吧。")

    # The banner is UI-only, so the writer is never invoked for it.
    reply = (session / "002_assistant.txt").read_text(encoding="utf-8")
    assert reply.split("\n", 1)[1] == "嗯，一般就是还行吧。"
    assert BANNER not in reply
    assert len(list(session.glob("*_assistant.txt"))) == 2


def test_empty_visitor_feedback_writes_no_file(tmp_path):
    manager = DataManager(data_root=str(tmp_path))
    manager.current_subject_id = "001"
    manager.start_new_session()
    session = Path(manager.session_dir)

    result = manager.save_session_report({"summary": "x"}, "", "QUIT")

    assert result["feedback_written"] is False
    assert result["feedback_path"] == ""
    assert result["ok"] is True
    assert not (session / "visitor_feedback.txt").exists()


def test_non_empty_visitor_feedback_is_still_written(tmp_path):
    manager = DataManager(data_root=str(tmp_path))
    manager.current_subject_id = "002"
    manager.start_new_session()
    session = Path(manager.session_dir)

    result = manager.save_session_report({"summary": "x"}, "今天辛苦了。", "END_SESSION")

    assert result["feedback_written"] is True
    written = (session / "visitor_feedback.txt").read_text(encoding="utf-8")
    assert "今天辛苦了。" in written
    payload = json.loads(
        (session / "researcher_report.json").read_text(encoding="utf-8")
    )
    assert payload["end_type"] == "END_SESSION"
