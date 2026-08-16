"""Static/contract checks for the existing Windows/WSL lifecycle owner."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_START = (ROOT / "scripts" / "windows" / "start_blackwell_stack.ps1").read_text(encoding="utf-8")
WINDOWS_STOP = (ROOT / "scripts" / "windows" / "stop_blackwell_stack.ps1").read_text(encoding="utf-8")
WSL_START = (ROOT / "scripts" / "wsl" / "start_vllm_service.sh").read_text(encoding="utf-8")
WSL_STOP = (ROOT / "scripts" / "wsl" / "stop_vllm_service.sh").read_text(encoding="utf-8")
WSL_STATUS = (ROOT / "scripts" / "wsl" / "status_vllm_service.sh").read_text(encoding="utf-8")
IDENTITY = (ROOT / "scripts" / "wsl" / "vllm_service_identity.sh").read_text(encoding="utf-8")


def test_verify_is_read_only_and_reports_profile_ports_pid_and_identity():
    assert "VerifyOnly" in WINDOWS_START
    assert "Alias(\"Status\")" in WINDOWS_START
    assert "Invoke-Verify" in WINDOWS_START
    assert "Get-WslServiceStatus" in WINDOWS_START
    assert "Get-EndpointProbe" in WINDOWS_START
    assert "WslStatusScript" in WINDOWS_START
    assert '"test", "-f", $WslStatusScript' in WINDOWS_START
    assert "agent_pid_state" in WINDOWS_START
    assert "dialogue_pid_state" in WINDOWS_START
    assert "gui = \"NOT STARTED\"" in WINDOWS_START
    # Environment cleanup is intentionally inside the launch branch, not the
    # verify-only observer path.
    assert WINDOWS_START.index("if ($VerifyOnly)") < WINDOWS_START.rfind("Clear-MisleadingOverrides")


def test_start_and_stop_share_metadata_backed_process_identity():
    for source in (WSL_START, WSL_STOP, WSL_STATUS):
        assert "vllm_service_identity.sh" in source
        assert "inspect_service_slot" in source
    for field in ("service_name", "pid", "model", "port", "vllm_executable"):
        assert f"{field}=" in WSL_START
        assert field in IDENTITY
    assert "/proc/$pid/cmdline" in IDENTITY
    assert "process_matches_metadata" in WSL_STOP


def test_stale_and_ownership_mismatch_paths_fail_closed():
    assert "STALE_PID" in IDENTITY
    assert "OWNERSHIP_MISMATCH" in IDENTITY
    assert "remove_service_metadata" in WSL_START
    assert "refusing to kill" in WSL_START
    assert "refusing to kill" in WSL_STOP
    assert WSL_STOP.index("process_matches_metadata \"$pid\"") < WSL_STOP.index('kill -TERM "$pid"')
    assert "Process identity changed" in WSL_STOP


def test_unknown_port_owner_is_not_replaced():
    assert "service_port_is_listening" in IDENTITY
    assert "unknown owner" in WSL_START
    assert "ss is unavailable" in WSL_START
    assert "PORT LISTENING" in WINDOWS_START
    assert "unknown listener" in WINDOWS_START
    assert "different model" in WINDOWS_START


def test_stop_has_no_broad_process_kill_and_status_is_observer_only():
    for source in (WINDOWS_STOP, WSL_STOP, WSL_STATUS):
        lowered = source.lower()
        assert "pkill" not in lowered
        assert "killall" not in lowered
        assert "taskkill" not in lowered
    assert "Status is an observer" in WSL_STATUS


def test_agent_dialogue_gui_order_and_partial_cleanup_are_preserved():
    assert WINDOWS_START.index('Ensure-Service -ServiceName "agent"') < WINDOWS_START.index(
        'Ensure-Service -ServiceName "dialogue"'
    )
    assert WINDOWS_START.index("check_config.py") < WINDOWS_START.index("main.py")
    assert "startedServices" in WINDOWS_START
    assert "Stop-ServiceByName" in WINDOWS_START


def test_identity_helper_does_not_create_unbounded_tombstones_or_state():
    assert "_pruned" not in IDENTITY
    assert "set_service_context" in IDENTITY
    assert "rm -f -- \"$pid_file\" \"$metadata_file\"" in IDENTITY


def test_manifests_are_not_runtime_configuration_sources():
    assert "deployment_manifest.json" not in WINDOWS_START
    assert "acceptance_manifest.json" not in WINDOWS_START
    assert "deployment_manifest.json" not in WINDOWS_STOP
