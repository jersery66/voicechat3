"""Read-only deployment readiness diagnostics.

The doctor observes the current checkout, host, optional WSL distribution,
local endpoints, and importable dependencies.  It never installs packages,
starts or stops services, edits configuration, or kills processes.  Missing
hardware or unavailable developer dependencies are reported explicitly as
``NOT AVAILABLE`` rather than being converted into a false PASS.

The deployment under observation is described by one runtime configuration
(``config/runtime.yaml`` by default, overridable with
``VOICECHAT_RUNTIME_CONFIG``); the doctor never selects a model from the
hardware it detects.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

# Support both ``python -m scripts.deployment.doctor`` and the operator-facing
# ``python scripts/deployment/doctor.py`` form.  The latter puts ``scripts/``
# ahead of the project root on ``sys.path``; add only this repository root so
# the runtime configuration module remains importable without changing the
# caller's environment.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.runtime_config import (
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OLLAMA,
    RUNTIME_CONFIG_ENV_VAR,
    RuntimeConfig,
    get_runtime_config,
    load_runtime_config,
)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "test_output" / "deployment_readiness"
ALLOWED_STATUSES = {"PASS", "FAIL", "NOT AVAILABLE", "NOT RUN", "SKIPPED"}
ENDPOINT_STATUSES = {
    "PORT FREE",
    "PORT LISTENING",
    "ENDPOINT HEALTHY",
    "ENDPOINT WRONG MODEL",
    "ENDPOINT UNAVAILABLE",
}
REQUIRED_FILES = (
    "main.py",
    "config.py",
    "config/runtime.yaml",
    "deployment/runtime_config.py",
    "scripts/deployment/doctor.py",
    "scripts/check_config.py",
    "scripts/windows/start_blackwell_stack.ps1",
    "scripts/windows/stop_blackwell_stack.ps1",
    "scripts/wsl/start_vllm_service.sh",
    "scripts/wsl/stop_vllm_service.sh",
    "scripts/wsl/status_vllm_service.sh",
    "scripts/wsl/vllm_service_identity.sh",
    "scripts/acceptance/blackwell_live_probe.py",
    "scripts/acceptance/qwen_dialogue_ab.py",
    "scripts/deployment/manifest.py",
    "scripts/deployment/final_readiness.py",
    "scripts/deployment/measurement.py",
    "scripts/deployment/memory_snapshot.py",
    "scripts/deployment/observability.py",
    "scripts/deployment/error_taxonomy.py",
    "knowledge_base/knowledge.json",
)
DEPENDENCY_MODULES = {
    "stt_dependency_status": "funasr",
    "vad_dependency_status": "services.fsmn_vad_adapter",
    "tts_dependency_status": "voxcpm",
    "ui_dependency_status": "PySide6",
    "rag_dependency_status": "requests",
    "report_dependency_status": "reportlab",
}


def _git_commit() -> str | None:
    try:
        result = _run_command(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 8.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _find_spec(name: str) -> Any:
    return importlib.util.find_spec(name)


def _check(name: str, status: str, detail: str, **data: Any) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported doctor status: {status}")
    result = {"name": name, "status": status, "detail": detail}
    if data:
        result["data"] = data
    return result


def _normalise_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        part.strip()
        for part in (result.stdout or "", result.stderr or "")
        if part and part.strip()
    )


def resolve_runtime_config_source(config_path: str | None = None) -> tuple[str, str]:
    """Resolve the runtime configuration to inspect and where it came from."""
    if config_path:
        return config_path, "argument"
    configured = os.environ.get(RUNTIME_CONFIG_ENV_VAR, "").strip()
    if configured:
        return configured, RUNTIME_CONFIG_ENV_VAR
    return str(get_runtime_config().source_path), "repository default"


def _runtime_contract(config: RuntimeConfig) -> dict[str, Any]:
    dialogue = config.dialogue
    agent = config.agent
    return {
        "source_path": str(config.source_path),
        "dialogue_provider": dialogue.provider,
        "dialogue_model": dialogue.model,
        "dialogue_base_url": dialogue.base_url,
        "agent_enabled": agent.enabled,
        "agent_provider": agent.provider,
        "agent_model": agent.model,
        "agent_base_url": agent.base_url,
        "asr_provider": config.asr.provider,
        "tts_provider": config.tts.provider,
        "enable_streaming_tts": config.runtime.enable_streaming_tts,
        "request_mode": dialogue.request_mode,
        "system_role_mode": dialogue.system_role_mode,
        "strict_preflight": config.runtime.strict_preflight,
        "immutable_contract": config.runtime.immutable_contract,
        "dialogue_max_tokens": dialogue.max_tokens,
        "dialogue_temperature": dialogue.temperature,
        "dialogue_top_p": dialogue.top_p,
        "dialogue_top_k": dialogue.top_k,
        "dialogue_presence_penalty": dialogue.presence_penalty,
        "dialogue_enable_thinking": dialogue.enable_thinking,
    }


def validate_runtime_contract(config: RuntimeConfig) -> dict[str, Any]:
    """Validate one runtime configuration statically without probing services."""
    errors: list[str] = []
    dialogue = config.dialogue
    agent = config.agent
    for label, provider in (("dialogue", dialogue.provider), ("agent", agent.provider)):
        if provider not in {PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA}:
            errors.append(f"unsupported {label} provider: {provider!r}")
    for label, value in (
        ("dialogue_model", dialogue.model),
        ("dialogue_base_url", dialogue.base_url),
        ("agent_model", agent.model),
        ("agent_base_url", agent.base_url),
    ):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} is empty")
    if not isinstance(config.runtime.enable_streaming_tts, bool):
        errors.append("enable_streaming_tts must be boolean")
    if not isinstance(dialogue.request_mode, str) or not dialogue.request_mode.strip():
        errors.append("request_mode must be non-empty")
    if not isinstance(dialogue.system_role_mode, str) or not dialogue.system_role_mode.strip():
        errors.append("system_role_mode must be non-empty")
    if not isinstance(config.runtime.strict_preflight, bool):
        errors.append("strict_preflight must be boolean")
    if not isinstance(config.runtime.immutable_contract, bool):
        errors.append("immutable_contract must be boolean")
    if isinstance(dialogue.max_tokens, bool) or not isinstance(dialogue.max_tokens, int) or dialogue.max_tokens <= 0:
        errors.append("dialogue_max_tokens must be a positive integer")
    for label, value in (
        ("dialogue_temperature", dialogue.temperature),
        ("dialogue_top_p", dialogue.top_p),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{label} must be positive")
    if dialogue.top_p > 1:
        errors.append("dialogue_top_p must be <= 1")
    if dialogue.top_k is not None and (
        isinstance(dialogue.top_k, bool)
        or not isinstance(dialogue.top_k, int)
        or dialogue.top_k <= 0
    ):
        errors.append("dialogue_top_k must be a positive integer when configured")
    if dialogue.presence_penalty is not None and not isinstance(
        dialogue.presence_penalty, (int, float)
    ):
        errors.append("dialogue_presence_penalty must be numeric when configured")
    if isinstance(dialogue.presence_penalty, bool):
        errors.append("dialogue_presence_penalty must be numeric when configured")
    if dialogue.enable_thinking is not None and not isinstance(dialogue.enable_thinking, bool):
        errors.append("dialogue_enable_thinking must be boolean when configured")
    for label, value in (("dialogue_base_url", dialogue.base_url), ("agent_base_url", agent.base_url)):
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError:
            parsed = None
            port = None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname or not port:
            errors.append(f"{label} must be an HTTP URL with an explicit port")

    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "detail": "runtime configuration is internally consistent" if not errors else "; ".join(errors),
        "errors": errors,
        "contract": _runtime_contract(config),
    }


def check_host() -> dict[str, Any]:
    info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": sys.version,
    }
    if info["system"] == "Windows":
        return _check("host", "PASS", "Windows host detected", **info)
    return _check("host", "NOT AVAILABLE", "target deployment host is Windows", **info)


def check_python() -> dict[str, Any]:
    return _check("python", "PASS", "doctor is running under the current Python interpreter", version=sys.version)


def check_required_files() -> dict[str, Any]:
    missing = [path for path in REQUIRED_FILES if not (PROJECT_ROOT / path).is_file()]
    if missing:
        return _check("required_files", "FAIL", "required repository files are missing", missing=missing)
    return _check("required_files", "PASS", "deployment entrypoints and frozen acceptance tools exist")


def check_environment(config_source: str, source: str) -> dict[str, Any]:
    observed = {
        name: bool(os.environ.get(name, "").strip())
        for name in (
            RUNTIME_CONFIG_ENV_VAR,
            "NO_PROXY",
            "OLLAMA_MODEL",
            "AGENT_MODEL",
            "VOICECHAT_DIALOGUE_MODEL",
            "VOICECHAT_DIALOGUE_BASE_URL",
            "VOICECHAT_AGENT_BASE_URL",
        )
    }
    return _check(
        "environment",
        "PASS",
        f"runtime configuration selected from {source}",
        runtime_config=config_source,
        project_root=str(PROJECT_ROOT),
        observed_variables=observed,
        no_secret_values_recorded=True,
    )


def _tcp_connect(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fetch_model_ids(url: str, timeout: float) -> list[str]:
    endpoint = urljoin(url.rstrip("/") + "/", "models")
    request = Request(endpoint, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback endpoint from the runtime configuration only
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data", []) if isinstance(payload, Mapping) else []
    return [str(item["id"]) for item in data if isinstance(item, Mapping) and item.get("id")]


def inspect_endpoint(base_url: str, expected_model: str, *, timeout: float = 1.0) -> dict[str, Any]:
    """Classify a local endpoint without starting or stopping anything."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        host = None
        port = None
    if not host or not port:
        return {"status": "ENDPOINT UNAVAILABLE", "url": base_url, "model_ids": [], "detail": "invalid URL"}
    if not _tcp_connect(host, port, timeout):
        return {"status": "PORT FREE", "url": base_url, "port": port, "model_ids": [], "detail": "TCP connect refused or timed out"}
    try:
        model_ids = _fetch_model_ids(base_url, timeout)
    except Exception as exc:
        return {"status": "PORT LISTENING", "url": base_url, "port": port, "model_ids": [], "detail": str(exc)}
    if expected_model not in model_ids:
        status = "ENDPOINT WRONG MODEL"
        detail = f"expected {expected_model!r}; server exposed {model_ids!r}"
    else:
        status = "ENDPOINT HEALTHY"
        detail = f"exact model {expected_model!r} is exposed"
    return {"status": status, "url": base_url, "port": port, "model_ids": model_ids, "detail": detail}


def check_endpoints(config: RuntimeConfig) -> dict[str, Any]:
    dialogue = inspect_endpoint(config.dialogue.base_url, config.dialogue.model)
    if config.agent.enabled:
        agent = inspect_endpoint(config.agent.base_url, config.agent.model)
    else:
        agent = {"status": "ENDPOINT HEALTHY", "url": config.agent.base_url, "model_ids": [], "detail": "agent disabled by runtime configuration"}
    endpoint_results = {"dialogue": dialogue, "agent": agent}
    statuses = {dialogue["status"], agent["status"]}
    if "ENDPOINT WRONG MODEL" in statuses:
        status = "FAIL"
        detail = "one or more configured endpoints expose the wrong model"
    elif statuses <= {"ENDPOINT HEALTHY"}:
        status = "PASS"
        detail = "dialogue and Agent endpoints expose exact configured models"
    else:
        status = "NOT AVAILABLE"
        detail = "one or more configured endpoints are not reachable"
    return _check("endpoints", status, detail, **endpoint_results)


def _check_module(module_name: str, label: str) -> dict[str, Any]:
    try:
        found = _find_spec(module_name)
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        found = None
        detail = f"lookup failed: {exc}"
    else:
        detail = f"{module_name} is importable" if found is not None else f"{module_name} is not installed"
    return _check(label, "PASS" if found is not None else "NOT AVAILABLE", detail, module=module_name)


def check_dependencies() -> dict[str, Any]:
    checks = {
        label: _check_module(module, label)
        for label, module in DEPENDENCY_MODULES.items()
    }
    statuses = {check["status"] for check in checks.values()}
    status = "PASS" if statuses <= {"PASS"} else "NOT AVAILABLE"
    return _check("application_dependencies", status, "dependency importability was observed", checks=checks)


def _wsl_command(arguments: Sequence[str], *, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return _run_command(["wsl.exe", *arguments], timeout=timeout)


def check_wsl() -> dict[str, Any]:
    try:
        status = _wsl_command(["--status"])
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return _check("wsl", "NOT AVAILABLE", f"wsl.exe is unavailable: {exc}")
    if status.returncode != 0:
        return _check("wsl", "NOT AVAILABLE", _normalise_output(status) or "wsl --status failed")
    try:
        version = _wsl_command(["--version"])
        distros = _wsl_command(["-l", "-v"])
        uname = _wsl_command(["--", "uname", "-s"])
        gpu = _wsl_command(["--", "nvidia-smi"])
        python_result = _wsl_command(["--", "python", "--version"])
        torch_result = _wsl_command(["--", "python", "-c", "import torch; print(torch.__version__); print(torch.cuda.is_available())"])
        vllm_result = _wsl_command(["--", "vllm", "--version"])
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return _check("wsl", "NOT AVAILABLE", f"WSL observation failed: {exc}")
    linux_ok = uname.returncode == 0 and uname.stdout.strip().lower() == "linux"
    gpu_ok = gpu.returncode == 0 and bool(gpu.stdout.strip())
    command_checks = {
        "version": _normalise_output(version),
        "distributions": _normalise_output(distros),
        "uname": _normalise_output(uname),
        "nvidia_smi": _normalise_output(gpu),
        "python": _normalise_output(python_result),
        "torch": _normalise_output(torch_result),
        "vllm": _normalise_output(vllm_result),
    }
    if not linux_ok or not gpu_ok:
        return _check("wsl", "NOT AVAILABLE", "WSL is present but Linux/GPU visibility is unavailable", **command_checks)
    return _check("wsl", "PASS", "WSL reports Linux and nvidia-smi output", **command_checks)


def check_host_gpu() -> dict[str, Any]:
    try:
        result = _run_command(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,memory.free", "--format=csv"],
            timeout=8.0,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return _check("host_gpu", "NOT AVAILABLE", f"nvidia-smi unavailable: {exc}")
    output = _normalise_output(result)
    if result.returncode != 0 or not output:
        return _check("host_gpu", "NOT AVAILABLE", output or "nvidia-smi returned no GPU data")
    return _check("host_gpu", "PASS", "host nvidia-smi returned GPU data", raw=output)


def _overall_status(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(check.get("status")) for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "NOT AVAILABLE" in statuses:
        return "NOT AVAILABLE"
    if "NOT RUN" in statuses or "SKIPPED" in statuses:
        return "NOT RUN"
    return "PASS"


def run_doctor(
    *,
    config_path: str | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run all read-only observations and write the two readiness artifacts."""
    config_source, source = resolve_runtime_config_source(config_path)
    config: RuntimeConfig | None = None
    load_error: str | None = None
    try:
        config = load_runtime_config(config_source) if config_path else get_runtime_config()
    except (OSError, ValueError, TypeError) as exc:
        load_error = str(exc)

    contract = (
        validate_runtime_contract(config)
        if config is not None
        else {"status": "FAIL", "detail": f"runtime configuration could not be loaded: {load_error}", "errors": [load_error or ""]}
    )
    host = check_host()
    python_check = check_python()
    required = check_required_files()
    environment = check_environment(config_source, source)
    host_gpu = check_host_gpu()
    dependencies = check_dependencies()
    wsl = check_wsl()
    endpoints = check_endpoints(config) if config is not None and contract["status"] == "PASS" else _check(
        "endpoints", "FAIL", "endpoint checks skipped because runtime configuration validation failed"
    )
    checks = [
        host,
        python_check,
        required,
        environment,
        {"name": "runtime_config", **{key: value for key, value in contract.items() if key != "contract"}},
        host_gpu,
        dependencies,
        wsl,
        endpoints,
    ]
    contract_status = contract["status"]
    host_gpu_status = host_gpu["status"]
    wsl_gpu_status = "PASS" if wsl["status"] == "PASS" else wsl["status"]
    if host_gpu_status == "FAIL" or wsl_gpu_status == "FAIL":
        gpu_status = "FAIL"
    elif host_gpu_status == "PASS" and wsl_gpu_status == "PASS":
        gpu_status = "PASS"
    else:
        gpu_status = "NOT AVAILABLE"
    wsl_data = wsl.get("data", {})
    torch_output = str(wsl_data.get("torch", ""))
    vllm_output = str(wsl_data.get("vllm", ""))
    cuda_status = "PASS" if wsl["status"] == "PASS" and "true" in torch_output.lower() else "NOT AVAILABLE"
    torch_status = "PASS" if wsl["status"] == "PASS" and torch_output.strip() else "NOT AVAILABLE"
    vllm_status = "PASS" if wsl["status"] == "PASS" and vllm_output.strip() else "NOT AVAILABLE"
    dependency_checks = dependencies.get("data", {}).get("checks", {})
    summary = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "runtime_config": config_source,
        "runtime_config_source": source,
        "host_status": host["status"],
        "wsl_status": wsl["status"],
        "gpu_status": gpu_status,
        "python_status": python_check["status"],
        "torch_status": torch_status,
        "cuda_status": cuda_status,
        "vllm_status": vllm_status,
        "dialogue_endpoint_status": endpoints.get("data", {}).get("dialogue", {}).get("status", "NOT RUN"),
        "agent_endpoint_status": endpoints.get("data", {}).get("agent", {}).get("status", "NOT RUN"),
        "stt_dependency_status": dependencies.get("data", {}).get("checks", {}).get("stt_dependency_status", {}).get("status", "NOT RUN"),
        "vad_dependency_status": dependency_checks.get("vad_dependency_status", {}).get("status", "NOT RUN"),
        "tts_dependency_status": dependencies.get("data", {}).get("checks", {}).get("tts_dependency_status", {}).get("status", "NOT RUN"),
        "ui_dependency_status": dependencies.get("data", {}).get("checks", {}).get("ui_dependency_status", {}).get("status", "NOT RUN"),
        "rag_dependency_status": dependency_checks.get("rag_dependency_status", {}).get("status", "NOT RUN"),
        "report_dependency_status": dependency_checks.get("report_dependency_status", {}).get("status", "NOT RUN"),
        "runtime_config_validation_status": contract_status,
        "overall_status": _overall_status(checks),
        "hardware_evidence": "NOT RUN",
        "evidence_type": "OBSERVED",
    }
    # Generic GPU visibility is useful diagnostics, but it is not target
    # Blackwell acceptance.  The exact identity/memory gate belongs to the
    # live probe, so readiness remains hardware-blocked until measured there.
    summary["hardware_blocked"] = summary["hardware_evidence"] != "MEASURED" or gpu_status != "PASS"
    details = {
        "schema_version": 1,
        "timestamp": summary["timestamp"],
        "git_commit": summary["git_commit"],
        "runtime_config": config_source,
        "checks": checks,
        "runtime_config_contract": contract.get("contract"),
        "wsl_and_host_observations": {"host_gpu": host_gpu, "wsl": wsl},
        "endpoint_observations": endpoints.get("data", {}),
    }
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    (root / "readiness_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "readiness_details.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read-only voicechat deployment doctor")
    parser.add_argument("--config", default=None, help="runtime configuration to inspect; otherwise use VOICECHAT_RUNTIME_CONFIG")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args(argv)
    summary = run_doctor(config_path=args.config, output_root=args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["overall_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
