"""Deterministic contract checks for the Windows/WSL2 Blackwell launcher."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_START = ROOT / "scripts" / "windows" / "start_blackwell_stack.ps1"
WINDOWS_STOP = ROOT / "scripts" / "windows" / "stop_blackwell_stack.ps1"
WSL_START = ROOT / "scripts" / "wsl" / "start_vllm_service.sh"
WSL_STOP = ROOT / "scripts" / "wsl" / "stop_vllm_service.sh"
DOC = ROOT / "docs" / "deployment" / "windows_wsl2_blackwell_launcher.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_launcher_scripts_and_documentation_exist():
    assert WINDOWS_START.exists()
    assert WINDOWS_STOP.exists()
    assert WSL_START.exists()
    assert WSL_STOP.exists()
    assert DOC.exists()


def test_windows_launcher_defaults_to_blackwell_baseline_and_accepts_candidate():
    source = _read(WINDOWS_START)

    assert '"rtxpro6000_96g"' in source
    assert "rtxpro6000_96g_qwen38_candidate" in source
    assert "[string]$Profile = \"rtxpro6000_96g\"" in source
    assert "[ValidateSet" in source


def test_windows_launcher_uses_wsl_not_windows_vllm_or_legacy_a100_launcher():
    source = _read(WINDOWS_START)

    assert "wsl.exe" in source
    assert "vllm serve" not in source
    assert "start_vllm_a100.ps1" not in source
    assert "start_a100_vllm_stack.ps1" not in source


def test_windows_launcher_derives_models_from_profile_export():
    source = _read(WINDOWS_START)

    assert "get_deployment_profile" in source
    assert "dialogue_model" in source
    assert "agent_model" in source
    assert "Qwen/Qwen2.5-72B-Instruct-AWQ" not in source
    assert "Qwen/Qwen3.8-27B-FP8" not in source
    assert "Qwen/Qwen2.5-3B-Instruct-AWQ" not in source


def test_windows_launcher_validates_profile_policy_and_clears_overrides():
    source = _read(WINDOWS_START)

    assert "immutable_runtime_contract" in source
    assert "strict_preflight" in source
    assert "expected_gpu_memory_gb" in source
    for variable in (
        "OLLAMA_MODEL",
        "AGENT_MODEL",
        "VOICECHAT_DIALOGUE_MODEL",
        "VOICECHAT_VLLM_MODEL",
        "VOICECHAT_DIALOGUE_BASE_URL",
        "VOICECHAT_AGENT_BASE_URL",
    ):
        assert variable in source


def test_windows_launcher_checks_wsl_linux_and_nvidia_visibility():
    source = _read(WINDOWS_START)

    assert "uname" in source
    assert "Linux" in source
    assert "nvidia-smi" in source
    assert "wslpath" in source


def test_repository_scripts_use_bash_and_do_not_require_executable_bits():
    source = _read(WINDOWS_START)

    assert '"bash", $WslStartScript' in source
    assert '"bash", $WslStopScript' in source
    assert '"test", "-f", $WslStartScript' in source
    assert '"test", "-f", $WslStopScript' in source
    assert '"test", "-x", $WslStartScript' not in source
    assert '"test", "-x", $WslStopScript' not in source


def test_only_the_vllm_executable_keeps_strict_executable_validation():
    source = _read(WINDOWS_START)
    wsl_source = _read(WSL_START)

    assert "--check-executable" in source
    assert '"--check-executable"' in source
    assert '[[ -x "$vllm_executable" ]]' in wsl_source
    assert "chmod" not in source.lower()
    assert "chmod" not in wsl_source.lower()
    assert "wsl.conf" not in source.lower()
    assert "wsl.conf" not in wsl_source.lower()
    assert "DrvFS" not in source
    assert "DrvFS" not in wsl_source


def test_windows_launcher_starts_agent_before_dialogue_and_waits_for_exact_models():
    source = _read(WINDOWS_START)

    assert source.index('Ensure-Service -ServiceName "agent"') < source.index(
        'Ensure-Service -ServiceName "dialogue"'
    )
    assert "/v1/models" in source
    assert "ExpectedModel" in source
    assert "StartupTimeoutMinutes" in source
    assert "StartupTimeoutSeconds" in source
    assert "8000" in source
    assert "8001" in source


def test_windows_launcher_requires_explicit_unfrozen_gpu_budget():
    source = _read(WINDOWS_START)

    assert "Mandatory" in source
    assert "$true" in source
    assert "DialogueGpuMemoryUtilization" in source
    assert "AgentGpuMemoryUtilization" in source
    assert "+ $AgentGpuMemoryUtilization)" in source
    assert "-ge 1" in source
    assert "0.82" not in source
    assert "0.08" not in source
    assert "DialogueMaxModelLen" in source
    assert "AgentMaxModelLen" in source


def test_windows_launcher_runs_strict_check_before_main_and_keeps_main_on_windows():
    source = _read(WINDOWS_START)

    assert "check_config.py" in source
    assert "main.py" in source
    assert source.index("check_config.py") < source.index("main.py")
    assert "& $Python" in source
    assert "bash" in source


def test_windows_launcher_reuses_only_exact_models_and_tracks_owned_services():
    source = _read(WINDOWS_START)

    assert "wrong model" in source.lower() or "model mismatch" in source.lower()
    assert "startedServices" in source
    assert "pre-existing" in source.lower() or "preexisting" in source.lower()
    assert "Stop-ServiceByName" in source
    assert "log" in source.lower()


def test_windows_launcher_does_not_change_network_mode_or_expose_lan():
    source = _read(WINDOWS_START)

    assert "127.0.0.1" in source
    assert "0.0.0.0" not in source
    assert "netsh" not in source.lower()
    assert ".wslconfig" not in source.lower()
    assert "portproxy" not in source.lower()


def test_wsl_service_launcher_uses_one_gpu_and_durable_pid_logs():
    source = _read(WSL_START)

    assert "exec" in source
    assert "nohup" in source
    assert "vllm" in source
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


def test_wsl_stop_uses_pid_files_and_never_broad_kill_patterns():
    source = _read(WSL_STOP)
    windows_source = _read(WINDOWS_STOP)

    assert ".pid" in source
    assert "SIGTERM" in source or "kill -TERM" in source
    assert "SIGKILL" in source or "kill -KILL" in source
    for forbidden in ("pkill", "killall", "taskkill"):
        assert forbidden not in source.lower()
        assert forbidden not in windows_source.lower()
    assert "stop_vllm_service.sh" in windows_source


def test_launcher_documentation_records_unvalidated_runtime_boundary():
    source = _read(DOC)

    assert "localhost" in source.lower()
    assert "Windows" in source
    assert "WSL2" in source
    assert "nvidia-smi" in source
    assert "NOT RUN" in source
    assert "NOT VERIFIED" in source
    assert "manual" in source.lower()
