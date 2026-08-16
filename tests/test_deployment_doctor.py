"""Offline contract tests for the read-only deployment doctor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from deployment.profiles import get_deployment_profile
from scripts.deployment import doctor


def test_profile_contract_validates_both_blackwell_profiles_without_overrides():
    for name in ("rtxpro6000_96g", "rtxpro6000_96g_qwen38_candidate"):
        result = doctor.validate_profile_contract(name)
        assert result["status"] == "PASS"
        assert result["contract"]["dialogue_model"] == get_deployment_profile(name).dialogue_model
        assert result["contract"]["agent_model"] == get_deployment_profile(name).agent_model
        assert result["contract"]["strict_preflight"] is True
        assert result["contract"]["immutable_runtime_contract"] is True
        assert result["contract"]["dialogue_max_tokens"] > 0
        assert result["contract"]["vllm_request_mode"] == "chat"


def test_profile_contract_rejects_unknown_profile():
    result = doctor.validate_profile_contract("missing_profile")
    assert result["status"] == "FAIL"
    assert "unknown" in result["detail"].lower()


def test_profile_contract_rejects_malformed_endpoint(monkeypatch):
    original = get_deployment_profile("rtxpro6000_96g")
    malformed = original.__class__(
        **{**original.__dict__, "dialogue_base_url": "http://127.0.0.1:not-a-port"}
    )
    monkeypatch.setattr(doctor, "get_deployment_profile", lambda name: malformed)
    result = doctor.validate_profile_contract("synthetic_bad_profile")
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
    result = doctor.run_doctor(profile_name="rtxpro6000_96g", output_root=tmp_path)

    assert result["profile"] == "rtxpro6000_96g"
    assert result["overall_status"] in {"NOT AVAILABLE", "PASS"}
    assert result["hardware_blocked"] is True
    summary_path = tmp_path / "readiness_summary.json"
    details_path = tmp_path / "readiness_details.json"
    assert summary_path.exists()
    assert details_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    details = json.loads(details_path.read_text(encoding="utf-8"))
    assert summary["git_commit"] == doctor._git_commit()
    assert summary["profile"] == "rtxpro6000_96g"
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
