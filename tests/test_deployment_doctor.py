"""Offline contract tests for the read-only deployment doctor."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from deployment.runtime_config import get_runtime_config
from scripts.deployment import doctor


def test_runtime_contract_validates_the_shipped_configuration():
    config = get_runtime_config()
    result = doctor.validate_runtime_contract(config)
    assert result["status"] == "PASS"
    assert result["contract"]["dialogue_model"] == config.dialogue.model
    assert result["contract"]["agent_model"] == config.agent.model
    assert result["contract"]["dialogue_provider"] == config.dialogue.provider
    assert result["contract"]["strict_preflight"] is config.runtime.strict_preflight
    assert result["contract"]["immutable_contract"] is config.runtime.immutable_contract
    assert result["contract"]["dialogue_max_tokens"] > 0
    assert result["contract"]["request_mode"] == config.dialogue.request_mode


def test_runtime_contract_rejects_unsupported_provider():
    config = get_runtime_config()
    broken = replace(config, dialogue=replace(config.dialogue, provider="typo-engine"))
    result = doctor.validate_runtime_contract(broken)
    assert result["status"] == "FAIL"
    assert "unsupported dialogue provider" in result["detail"]


def test_runtime_contract_rejects_malformed_endpoint():
    config = get_runtime_config()
    malformed = replace(config, dialogue=replace(config.dialogue, base_url="http://127.0.0.1:not-a-port"))
    result = doctor.validate_runtime_contract(malformed)
    assert result["status"] == "FAIL"
    assert "dialogue_base_url" in result["detail"]


def test_endpoint_status_distinguishes_free_listening_healthy_and_wrong_model(monkeypatch):
    monkeypatch.setattr(doctor, "_tcp_connect", lambda host, port, timeout: False)
    assert doctor.inspect_endpoint("http://127.0.0.1:8000/v1", "model")["status"] == "PORT FREE"

    monkeypatch.setattr(doctor, "_tcp_connect", lambda host, port, timeout: True)
    monkeypatch.setattr(doctor, "_fetch_model_ids", lambda url, timeout: (_ for _ in ()).throw(RuntimeError("not HTTP")))
    assert doctor.inspect_endpoint("http://127.0.0.1:8000/v1", "model")["status"] == "PORT LISTENING"

    monkeypatch.setattr(doctor, "_fetch_model_ids", lambda url, timeout: ["model"])
    healthy = doctor.inspect_endpoint("http://127.0.0.1:8000/v1", "model")
    assert healthy["status"] == "ENDPOINT HEALTHY"
    assert healthy["model_ids"] == ["model"]

    monkeypatch.setattr(doctor, "_fetch_model_ids", lambda url, timeout: ["other"])
    assert doctor.inspect_endpoint("http://127.0.0.1:8000/v1", "model")["status"] == "ENDPOINT WRONG MODEL"


def test_wsl_unavailable_is_not_available(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("wsl.exe")

    monkeypatch.setattr(doctor, "_run_command", missing)
    result = doctor.check_wsl()
    assert result["status"] == "NOT AVAILABLE"
    assert "wsl" in result["detail"].lower()


def test_doctor_writes_summary_and_details_without_starting_services(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_run_command", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""))
    monkeypatch.setattr(doctor, "_tcp_connect", lambda host, port, timeout: False)
    monkeypatch.setattr(doctor, "_find_spec", lambda name: object())
    result = doctor.run_doctor(output_root=tmp_path)

    assert result["runtime_config"] == str(get_runtime_config().source_path)
    assert result["runtime_config_source"] == "repository default"
    assert result["overall_status"] in {"NOT AVAILABLE", "PASS"}
    assert result["hardware_blocked"] is True
    summary_path = tmp_path / "readiness_summary.json"
    details_path = tmp_path / "readiness_details.json"
    assert summary_path.exists()
    assert details_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    details = json.loads(details_path.read_text(encoding="utf-8"))
    assert summary["git_commit"] == doctor._git_commit()
    assert summary["runtime_config"] == str(get_runtime_config().source_path)
    assert summary["hardware_blocked"] is True
    assert isinstance(details["checks"], list)
    assert all(check["status"] in doctor.ALLOWED_STATUSES for check in details["checks"])


def test_doctor_is_read_only_and_does_not_delegate_to_check_config():
    source = Path(doctor.__file__).read_text(encoding="utf-8")
    assert "run_check" not in source
    assert "pip install" not in source
    assert "subprocess.kill" not in source
    assert "pkill" not in source
    assert "killall" not in source


def test_doctor_supports_direct_script_invocation():
    result = subprocess.run(
        [sys.executable, str(doctor.PROJECT_ROOT / "scripts" / "deployment" / "doctor.py"), "--help"],
        cwd=doctor.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Read-only voicechat deployment doctor" in result.stdout
