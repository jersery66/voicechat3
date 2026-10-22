"""Deterministic contract checks for the engine service scripts.

Each inference engine exposes the same start/stop/status/identity contract;
engine-specific flags are asserted per engine. The hardware-specific
Windows launchers were archived with the deployment profiles.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VLLM = ROOT / "scripts" / "engines" / "vllm"
LLAMA_CPP = ROOT / "scripts" / "engines" / "llama_cpp"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_engine_scripts_use_bash_and_do_not_require_executable_bits():
    for start in (VLLM / "start_service.sh", LLAMA_CPP / "start_service.sh"):
        source = _read(start)
        assert source.startswith("#!/usr/bin/env bash")
        assert "chmod" not in source.lower()
        assert "wsl.conf" not in source.lower()
        assert "DrvFS" not in source


def test_only_the_server_executable_keeps_strict_executable_validation():
    vllm_start = _read(VLLM / "start_service.sh")
    llama_start = _read(LLAMA_CPP / "start_service.sh")

    assert "--check-executable" in vllm_start
    assert "--check-executable" in llama_start
    assert '[[ -x "$vllm_executable" ]]' in vllm_start
    assert '[[ -x "$server_executable" ]]' in llama_start
    for source in (vllm_start, llama_start):
        assert "chmod" not in source.lower()
        assert "wsl.conf" not in source.lower()


def test_vllm_service_launcher_uses_one_gpu_and_durable_pid_logs():
    source = _read(VLLM / "start_service.sh")

    assert "nohup" in source
    for flag in (
        "--model",
        "--port",
        "--gpu-memory-utilization",
        "--max-model-len",
        "--service-name",
        "--host",
        "127.0.0.1",
        "--dtype",
        "auto",
        "--max-num-seqs",
        "4",
        "--enable-prefix-caching",
    ):
        assert flag in source
    assert "--tensor-parallel-size" not in source
    assert ".voicechat/vllm" in source
    assert ".pid" in source
    assert ".log" in source


def test_llama_cpp_service_launcher_serves_the_configured_alias():
    source = _read(LLAMA_CPP / "start_service.sh")

    assert "nohup" in source
    for flag in (
        "--model",
        "--port",
        "--alias",
        "--context-size",
        "--gpu-layers",
        "--parallel",
        "--service-name",
        "--chat-template-kwargs",
        "--host",
        "127.0.0.1",
        "--jinja",
    ):
        assert flag in source
    assert "-ngl" in source
    assert ".voicechat/llama_cpp" in source
    assert ".pid" in source
    assert ".log" in source


def test_llama_cpp_service_launcher_validates_its_numeric_contract():
    source = _read(LLAMA_CPP / "start_service.sh")

    assert "Invalid port" in source
    assert "Invalid context size" in source
    assert "Invalid gpu layers" in source
    assert "Invalid parallel slot count" in source
    assert "Invalid alias" in source


def test_vllm_stop_uses_pid_files_and_never_broad_kill_patterns():
    source = _read(VLLM / "stop_service.sh")

    assert ".pid" in source
    assert "SIGTERM" in source or "kill -TERM" in source
    assert "SIGKILL" in source or "kill -KILL" in source
    for forbidden in ("pkill", "killall", "taskkill"):
        assert forbidden not in source.lower()


def test_llama_cpp_stop_uses_pid_files_and_never_broad_kill_patterns():
    source = _read(LLAMA_CPP / "stop_service.sh")

    assert ".pid" in source
    assert "kill -TERM" in source
    assert "kill -KILL" in source
    for forbidden in ("pkill", "killall", "taskkill"):
        assert forbidden not in source.lower()


def test_both_engines_expose_the_same_ownership_states():
    states = ("SERVICE_RUNNING", "OWNERSHIP_MISMATCH", "STALE_PID", "NO_PID_FILE")

    for engine in (VLLM, LLAMA_CPP):
        identity = _read(engine / "service_identity.sh")
        for state in states:
            assert f'"{state}"' in identity, f"{engine.name} is missing {state}"
        assert "/proc/" in identity
        assert "cmdline" in identity
        # Ownership is proved through the recorded model and port.
        assert "metadata_model" in identity
        assert "metadata_port" in identity


def test_status_scripts_are_observers_that_never_signal():
    for engine in (VLLM, LLAMA_CPP):
        source = _read(engine / "status_service.sh")
        for forbidden in ("kill ", "pkill", "killall", "taskkill", "rm -f"):
            assert forbidden not in source, f"{engine.name} status must not act"
        assert "state=" in source
        assert "exit 0" in source
