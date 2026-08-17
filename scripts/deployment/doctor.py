"""Read-only deployment readiness diagnostics.

The doctor observes the current checkout, host, optional WSL distribution,
local endpoints, and importable dependencies.  It never installs packages,
starts or stops services, edits configuration, or kills processes.  Missing
hardware or unavailable developer dependencies are reported explicitly as
``NOT AVAILABLE`` rather than being converted into a false PASS.
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
# the profile package remains importable without changing the caller's
# environment.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.profiles import DeploymentProfile, RuntimeModels, get_deployment_profile, resolve_runtime_models

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
    "deployment/profiles.py",
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


def resolve_profile_name(profile_name: str | None = None) -> tuple[str, str]:
    """Resolve the requested profile and expose whether it came from env/default."""
    if profile_name:
        return profile_name.strip().lower(), "argument"
    configured = os.environ.get("VOICECHAT_DEPLOYMENT_PROFILE", "").strip().lower()
    if configured:
        return configured, "VOICECHAT_DEPLOYMENT_PROFILE"
    return "dev_6g", "repository default"


def _profile_contract(profile: DeploymentProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "runtime_backend": profile.runtime_backend,
        "dialogue_model": profile.dialogue_model,
        "dialogue_base_url": profile.dialogue_base_url,
        "router_model": profile.router_model,
        "agent_model": profile.agent_model,
        "agent_base_url": profile.agent_base_url,
        "enable_streaming_tts": profile.enable_streaming_tts,
        "vllm_request_mode": profile.vllm_request_mode,
        "vllm_system_role_mode": profile.vllm_system_role_mode,
        "expected_gpu_memory_gb": profile.expected_gpu_memory_gb,
        "strict_preflight": profile.strict_preflight,
        "immutable_runtime_contract": profile.immutable_runtime_contract,
        "dialogue_max_tokens": profile.dialogue_max_tokens,
        "dialogue_temperature": profile.dialogue_temperature,
        "dialogue_top_p": profile.dialogue_top_p,
        "dialogue_top_k": profile.dialogue_top_k,
        "dialogue_presence_penalty": profile.dialogue_presence_penalty,
        "dialogue_enable_thinking": profile.dialogue_enable_thinking,
    }


def validate_profile_contract(profile_name: str) -> dict[str, Any]:
    """Validate one profile statically without probing or mutating services."""
    try:
        profile = get_deployment_profile(profile_name)
    except (TypeError, ValueError) as exc:
        return {"status": "FAIL", "detail": f"unknown deployment profile: {exc}", "errors": [str(exc)]}

    errors: list[str] = []
    if profile.runtime_backend not in {"ollama", "vllm"}:
        errors.append(f"unsupported runtime_backend: {profile.runtime_backend!r}")
    for label, value in (
        ("dialogue_model", profile.dialogue_model),
        ("router_model", profile.router_model),
        ("agent_model", profile.agent_model),
        ("dialogue_base_url", profile.dialogue_base_url),
        ("agent_base_url", profile.agent_base_url),
    ):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} is empty")
    if isinstance(profile.expected_gpu_memory_gb, bool) or not isinstance(profile.expected_gpu_memory_gb, int) or profile.expected_gpu_memory_gb <= 0:
        errors.append("expected_gpu_memory_gb must be a positive integer")
    if not isinstance(profile.enable_streaming_tts, bool):
        errors.append("enable_streaming_tts must be boolean")
    if not isinstance(profile.vllm_request_mode, str) or not profile.vllm_request_mode.strip():
        errors.append("vllm_request_mode must be non-empty")
    if not isinstance(profile.vllm_system_role_mode, str) or not profile.vllm_system_role_mode.strip():
        errors.append("vllm_system_role_mode must be non-empty")
    if not isinstance(profile.strict_preflight, bool):
        errors.append("strict_preflight must be boolean")
    if not isinstance(profile.immutable_runtime_contract, bool):
        errors.append("immutable_runtime_contract must be boolean")
    if isinstance(profile.dialogue_max_tokens, bool) or not isinstance(profile.dialogue_max_tokens, int) or profile.dialogue_max_tokens <= 0:
        errors.append("dialogue_max_tokens must be a positive integer")
    for label, value in (
        ("dialogue_temperature", profile.dialogue_temperature),
        ("dialogue_top_p", profile.dialogue_top_p),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{label} must be positive")
    if profile.dialogue_top_p > 1:
        errors.append("dialogue_top_p must be <= 1")
    if profile.dialogue_top_k is not None and (
        isinstance(profile.dialogue_top_k, bool)
        or not isinstance(profile.dialogue_top_k, int)
        or profile.dialogue_top_k <= 0
    ):
        errors.append("dialogue_top_k must be a positive integer when configured")
    if profile.dialogue_presence_penalty is not None and not isinstance(
        profile.dialogue_presence_penalty, (int, float)
    ):
        errors.append("dialogue_presence_penalty must be numeric when configured")
    if isinstance(profile.dialogue_presence_penalty, bool):
        errors.append("dialogue_presence_penalty must be numeric when configured")
    if profile.dialogue_enable_thinking is not None and not isinstance(profile.dialogue_enable_thinking, bool):
        errors.append("dialogue_enable_thinking must be boolean when configured")
    for label, value in (("dialogue_base_url", profile.dialogue_base_url), ("agent_base_url", profile.agent_base_url)):
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
        "detail": "profile contract is internally consistent" if not errors else "; ".join(errors),
        "errors": errors,
        "contract": _profile_contract(profile),
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


def check_environment(profile_name: str, source: str) -> dict[str, Any]:
    observed = {
        name: bool(os.environ.get(name, "").strip())
        for name in (
            "VOICECHAT_DEPLOYMENT_PROFILE",
            "NO_PROXY",
            "OLLAMA_MODEL",
            "AGENT_MODEL",
            "VOICECHAT_DIALOGUE_MODEL",
            "VOICECHAT_VLLM_MODEL",
            "VOICECHAT_DIALOGUE_BASE_URL",
            "VOICECHAT_AGENT_BASE_URL",
        )
    }
    return _check(
        "environment",
        "PASS",
        f"profile selected from {source}",
        profile=profile_name,
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
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback/profile-owned endpoint only
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


def check_endpoints(profile: DeploymentProfile, models: RuntimeModels) -> dict[str, Any]:
    dialogue = inspect_endpoint(profile.dialogue_base_url, models.dialogue)
    agent = inspect_endpoint(profile.agent_base_url, models.router)
    endpoint_results = {"dialogue": dialogue, "agent": agent}
    statuses = {dialogue["status"], agent["status"]}
    if "ENDPOINT WRONG MODEL" in statuses:
        status = "FAIL"
        detail = "one or more profile-owned endpoints expose the wrong model"
    elif statuses <= {"ENDPOINT HEALTHY"}:
        status = "PASS"
        detail = "dialogue and Agent endpoints expose exact profile models"
    else:
        status = "NOT AVAILABLE"
        detail = "one or more profile-owned endpoints are not reachable"
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
    profile_name: str | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run all read-only observations and write the two readiness artifacts."""
    selected_profile, source = resolve_profile_name(profile_name)
    contract = validate_profile_contract(selected_profile)
    host = check_host()
    python_check = check_python()
    required = check_required_files()
    environment = check_environment(selected_profile, source)
    host_gpu = check_host_gpu()
    dependencies = check_dependencies()
    wsl = check_wsl()
    profile = get_deployment_profile(selected_profile) if contract["status"] == "PASS" else None
    models = resolve_runtime_models(profile, environment={}) if profile is not None else None
    endpoints = check_endpoints(profile, models) if profile is not None and models is not None else _check(
        "endpoints", "FAIL", "endpoint checks skipped because profile validation failed"
    )
    checks = [
        host,
        python_check,
        required,
        environment,
        {"name": "profile", **{key: value for key, value in contract.items() if key != "contract"}},
        host_gpu,
        dependencies,
        wsl,
        endpoints,
    ]
    profile_status = contract["status"]
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
        "profile": selected_profile,
        "profile_source": source,
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
        "profile_validation_status": profile_status,
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
        "profile": selected_profile,
        "checks": checks,
        "profile_contract": contract.get("contract"),
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
    parser.add_argument("--profile", default=None, help="profile to inspect; otherwise use VOICECHAT_DEPLOYMENT_PROFILE")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args(argv)
    summary = run_doctor(profile_name=args.profile, output_root=args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["overall_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
