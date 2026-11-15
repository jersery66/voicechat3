"""Static/contract checks for the engine service lifecycle scripts.

The Windows launchers were archived with the hardware profiles; what
remains under test is the engine-agnostic ownership contract shared by
``scripts/engines/llama_cpp`` and ``scripts/engines/vllm``.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINES = {
    "llama_cpp": ROOT / "scripts" / "engines" / "llama_cpp",
    "vllm": ROOT / "scripts" / "engines" / "vllm",
}
SCRIPTS = {name: {part: (directory / f"{part}_service.sh").read_text(encoding="utf-8")
                  for part in ("start", "stop", "status")}
           for name, directory in ENGINES.items()}
IDENTITY = {name: (directory / "service_identity.sh").read_text(encoding="utf-8")
            for name, directory in ENGINES.items()}


def test_engine_scripts_exist_for_both_inference_engines():
    for name, directory in ENGINES.items():
        assert (directory / "start_service.sh").exists()
        assert (directory / "stop_service.sh").exists()
        assert (directory / "status_service.sh").exists()
        assert (directory / "service_identity.sh").exists()


def test_start_and_stop_share_metadata_backed_process_identity():
    for name, scripts in SCRIPTS.items():
        for source in scripts.values():
            assert "service_identity.sh" in source
            assert "inspect_service_slot" in source
        for field in ("service_name", "pid", "model", "port"):
            assert f"{field}=" in scripts["start"]
            assert field in IDENTITY[name]
        assert "/proc/$pid/cmdline" in IDENTITY[name]
        assert "process_matches_metadata" in scripts["stop"]


def test_stale_and_ownership_mismatch_paths_fail_closed():
    for name, scripts in SCRIPTS.items():
        assert "STALE_PID" in IDENTITY[name]
        assert "OWNERSHIP_MISMATCH" in IDENTITY[name]
        assert "remove_service_metadata" in scripts["start"]
        assert "refusing to kill" in scripts["start"]
        assert "refusing to kill" in scripts["stop"]
        assert scripts["stop"].index("process_matches_metadata \"$pid\"") < scripts["stop"].index('kill -TERM "$pid"')
        assert "Process identity changed" in scripts["stop"]


def test_unknown_port_owner_is_not_replaced():
    for name, scripts in SCRIPTS.items():
        assert "service_port_is_listening" in IDENTITY[name]
        assert "unknown owner" in scripts["start"]
        assert "ss is unavailable" in scripts["start"]


def test_stop_has_no_broad_process_kill_and_status_is_observer_only():
    for name, scripts in SCRIPTS.items():
        for source in (*scripts.values(), IDENTITY[name]):
            lowered = source.lower()
            assert "pkill" not in lowered
            assert "killall" not in lowered
            assert "taskkill" not in lowered
        assert "Status is an observer" in scripts["status"]


def test_agent_dialogue_startup_order_is_owned_by_the_operator_not_the_app():
    # The application never starts model servers; ordering belongs to the
    # operator's start procedure, not to repository code.
    for scripts in SCRIPTS.values():
        assert "Ensure-Service" not in scripts["start"]


def test_identity_helper_does_not_create_unbounded_tombstones_or_state():
    for name, identity in IDENTITY.items():
        assert "_pruned" not in identity
        assert "set_service_context" in identity
        assert "rm -f -- \"$pid_file\" \"$metadata_file\"" in identity


def test_manifests_are_not_runtime_configuration_sources():
    for name, scripts in SCRIPTS.items():
        for source in scripts.values():
            assert "deployment_manifest.json" not in source
            assert "acceptance_manifest.json" not in source
