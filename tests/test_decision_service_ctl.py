"""Lifecycle controller: ownership states, refusal paths, recovery."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CTL_PATH = PROJECT_ROOT / "scripts" / "decision" / "service_ctl.py"


def _load_ctl():
    spec = importlib.util.spec_from_file_location("decision_service_ctl", CTL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICECHAT_TACIT_RUNTIME_DIR", str(tmp_path / "runtime"))
    return _load_ctl()


def _write_slot(ctl, name, pid, meta=None):
    paths = ctl.slot_paths(name)
    paths["pid"].write_text(str(pid), encoding="utf-8")
    if meta is not None:
        paths["meta"].write_text(json.dumps(meta), encoding="utf-8")
    return paths


def test_fresh_slot_has_no_pid_file(ctl):
    state = ctl.effective_state("tacit")
    assert state["state"] == "NO_PID_FILE"
    assert state["pid"] is None


def test_metadata_without_pid_file_is_stale(ctl):
    paths = ctl.slot_paths("tacit")
    paths["meta"].write_text(json.dumps({"service_name": "tacit", "pid": 1}), encoding="utf-8")
    assert ctl.effective_state("tacit")["state"] == "STALE_PID"


def test_dead_pid_is_stale(ctl):
    _write_slot(ctl, "tacit", 999_999, {"service_name": "tacit", "pid": 999_999, "port": 1})
    state = ctl.effective_state("tacit")
    assert state["state"] == "STALE_PID"


def test_pid_file_that_is_not_a_number_is_stale(ctl):
    ctl.slot_paths("tacit")["pid"].write_text("not-a-pid", encoding="utf-8")
    assert ctl.effective_state("tacit")["state"] == "STALE_PID"


def test_live_but_foreign_process_is_an_ownership_mismatch(ctl):
    """The pytest process is alive but is certainly not a decision service."""
    pid = os.getpid()
    _write_slot(ctl, "tacit", pid, {"service_name": "tacit", "pid": pid, "port": 8100})
    state = ctl.effective_state("tacit")
    if ctl.process_command_line(pid) is None:
        pytest.skip("command line is not inspectable on this host")
    assert state["state"] == "OWNERSHIP_MISMATCH"


def test_metadata_pid_must_match_the_pid_file(ctl):
    pid = os.getpid()
    _write_slot(ctl, "tacit", pid, {"service_name": "tacit", "pid": pid + 1, "port": 8100})
    assert ctl.effective_state("tacit")["state"] == "OWNERSHIP_MISMATCH"


def test_metadata_for_another_slot_is_an_ownership_mismatch(ctl):
    pid = os.getpid()
    _write_slot(ctl, "tacit", pid, {"service_name": "other", "pid": pid, "port": 8100})
    assert ctl.effective_state("tacit")["state"] == "OWNERSHIP_MISMATCH"


def test_stop_refuses_an_unverified_slot_without_force(ctl, monkeypatch):
    # Stub the command-line probe: PowerShell/CIM lookups cost ~1s each and the
    # real path is covered by test_live_but_foreign_process_is_an_ownership_mismatch.
    monkeypatch.setattr(ctl, "process_command_line", lambda pid: "python -c \"import time\"")
    pid = os.getpid()
    _write_slot(ctl, "tacit", pid, {"service_name": "tacit", "pid": pid, "port": 8100})
    code = ctl.main(["stop", "--name", "tacit"])
    assert code == 1
    # and it must not have killed the current process
    assert ctl.pid_alive(pid) is True


def test_stop_on_a_fresh_slot_is_a_noop(ctl):
    assert ctl.main(["stop", "--name", "tacit"]) == 0


def test_start_requires_a_model_path(ctl):
    assert ctl.main(["start", "--name", "tacit"]) == 2


def test_start_refuses_a_missing_checkpoint(ctl, tmp_path):
    assert ctl.main(["start", "--name", "tacit", "--model-path", str(tmp_path / "absent")]) == 3


def test_start_refuses_a_checkpoint_without_a_manifest(ctl, tmp_path):
    root = tmp_path / "Tacit-1.7B"
    root.mkdir()
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "tokenizer.json").write_text("{}", encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"\x00" * 64)
    assert ctl.main(["start", "--name", "tacit", "--model-path", str(root)]) == 3


def test_health_on_a_stopped_slot_returns_4(ctl):
    assert ctl.main(["health", "--name", "tacit"]) == 4


def test_recover_without_a_model_path_returns_2(ctl):
    assert ctl.main(["recover", "--name", "tacit"]) == 2


def test_name_validation_rejects_unsafe_characters(ctl):
    with pytest.raises(SystemExit) as excinfo:
        ctl.main(["status", "--name", "../evil;rm -rf /"])
    assert excinfo.value.code == 2


def test_status_output_is_key_value_and_log_safe(ctl, capsys):
    _write_slot(ctl, "tacit", 999_999, {"service_name": "tacit", "pid": 999_999, "port": 1})
    assert ctl.main(["status", "--name", "tacit"]) == 0
    output = capsys.readouterr().out
    assert "state=STALE_PID" in output
    assert "pid_file=" in output
    assert "detail=" in output


def test_runtime_dir_honours_the_environment(ctl, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICECHAT_TACIT_RUNTIME_DIR", str(tmp_path / "custom"))
    paths = ctl.slot_paths("x")
    assert paths["root"] == tmp_path / "custom"
    assert paths["pid"].name == "x.pid"


def test_force_stop_terminates_an_unverified_process(ctl, monkeypatch):
    monkeypatch.setattr(ctl, "process_command_line", lambda pid: "python -c \"import time\"")
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_slot(
            ctl, "tacit", sleeper.pid,
            {"service_name": "tacit", "pid": sleeper.pid, "port": 1, "model_path": "/models/x"},
        )
        assert ctl.effective_state("tacit")["state"] == "OWNERSHIP_MISMATCH"
        assert ctl.main(["stop", "--name", "tacit", "--force"]) == 0
        assert sleeper.wait(timeout=20) is not None
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=10)
    assert ctl.slot_paths("tacit")["pid"].is_file() is False
