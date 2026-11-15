"""Read-only target-workstation preflight for the configured deployment.

This script observes Windows, WSL2, PyTorch CUDA, and the inference engine the
runtime configuration declares. It does not start or stop services, install
packages, modify WSL configuration, select a GPU, or load a model. The existing
live probe remains the authority for real inference acceptance. The deployment
under test is described by one runtime configuration file, not by a GPU-specific
profile.

The engine check follows configuration, not tradition: ``dialogue.engine`` and
``agent.engine`` decide whether ``llama-server``, ``vllm``, or ``ollama`` is
asked for a version. An ``openai_compatible`` deployment that declares no
engine fails here instead of defaulting to vLLM, because the preflight cannot
verify a binary it cannot name.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.runtime_config import (
    ENGINE_LLAMA_CPP,
    ENGINE_OLLAMA,
    ENGINE_VLLM,
    RuntimeConfig,
)
from scripts.acceptance import probe_support

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "test_output" / "real_hardware_validation"
TORCH_PROBE = (
    "import json, torch; "
    "payload={'torch':torch.__version__,'cuda_available':bool(torch.cuda.is_available(),),"
    "'cuda_version':torch.version.cuda,'device_count':int(torch.cuda.device_count())}; "
    "payload.update({'device_name':torch.cuda.get_device_name(0),"
    "'total_memory_gb':torch.cuda.get_device_properties(0).total_memory/1024**3} "
    "if torch.cuda.is_available() and torch.cuda.device_count() else {}); "
    "print(json.dumps(payload))"
)


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _run(command: Sequence[str], *, timeout_seconds: float) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": list(command),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "status": "PASS" if result.returncode == 0 else "FAIL",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": list(command),
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "status": "NOT AVAILABLE",
        }


def _wsl_command(distro: str | None, *command: str) -> list[str]:
    result = ["wsl.exe"]
    if distro:
        result.extend(["-d", distro])
    result.extend(["--", *command])
    return result


def _wsl_bash(distro: str | None, script: str) -> list[str]:
    return _wsl_command(distro, "bash", "-lc", script)


def _shell_executable(executable: str) -> str:
    """Quote an executable path for ``bash -lc`` while keeping ``~`` expansion.

    ``shlex.quote("~/bin/tool")`` produces ``'~/bin/tool'``, and a quoted tilde
    is never expanded, so the default vLLM path would be reported as missing on
    every machine. Home-relative paths are therefore split before quoting.
    """
    if executable.startswith("~/"):
        return "$HOME/" + shlex.quote(executable[2:])
    return shlex.quote(executable)


def _deployment_check(config_path: str | None) -> tuple[dict[str, Any], str, RuntimeConfig | None]:
    """Validate the runtime configuration the operator intends to deploy.

    Returns the check payload, a short label used to name the artifact
    directory, and the resolved configuration (``None`` when it could not be
    loaded).  GPU memory is not asserted here: the configured deployment
    carries no fixed hardware budget, and inventing one would misrepresent
    what was verified.
    """
    try:
        config = probe_support.validate_runtime_deployment(config_path)
    except Exception as exc:
        return {"status": "FAIL", "error": str(exc)}, "unresolved-config", None
    label = Path(config.source_path).stem or "runtime"
    return {
        "status": "PASS",
        "config_file": Path(config.source_path).name,
        "dialogue_provider": config.dialogue.provider,
        "dialogue_engine": config.dialogue.engine,
        "dialogue_model": config.dialogue.model,
        "dialogue_endpoint": config.dialogue.base_url,
        "agent_enabled": config.agent.enabled,
        "agent_provider": config.agent.provider,
        "agent_engine": config.agent.engine,
        "agent_model": config.agent.model,
        "agent_endpoint": config.agent.base_url,
        "asr_provider": config.asr.provider,
        "tts_provider": config.tts.provider,
        "strict_preflight": config.runtime.strict_preflight,
        "immutable_runtime_contract": config.runtime.immutable_contract,
    }, label, config


def _gpu_check(source: str, query) -> tuple[dict[str, Any], Any | None]:
    try:
        observations = query()
        observation = probe_support.validate_gpu_observation(observations, source=source)
        return {"status": "PASS", "observation": observation.to_dict()}, observation
    except probe_support.ProbeError as exc:
        status = "NOT AVAILABLE" if exc.code == "GPU_QUERY_FAILED" else "FAIL"
        return {"status": status, "error_code": exc.code, "error": exc.detail}, None
    except Exception as exc:
        return {"status": "NOT AVAILABLE", "error": str(exc)}, None


def _parse_json_stdout(result: dict[str, Any]) -> dict[str, Any]:
    if result["status"] != "PASS":
        return result
    try:
        return {**result, "parsed": json.loads(result["stdout"].strip())}
    except (TypeError, ValueError):
        return {**result, "status": "FAIL", "error": "command did not return JSON"}


_HARDWARE_EVIDENCE_CHECKS = (
    "windows_gpu",
    "wsl_gpu",
    "windows_wsl_gpu_consistency",
    "torch_cuda",
)

ENGINE_LABELS = {
    ENGINE_LLAMA_CPP: "llama.cpp",
    ENGINE_VLLM: "vLLM",
    ENGINE_OLLAMA: "Ollama",
}


def _engine_requirements(config: RuntimeConfig) -> dict[str, str | None]:
    """Map every enabled service to the engine its configuration declares."""
    roles: dict[str, str | None] = {"dialogue": config.dialogue.engine}
    if config.agent.enabled:
        roles["agent"] = config.agent.engine
    return roles


def _engine_check(
    config: RuntimeConfig | None,
    *,
    distro: str | None,
    executables: dict[str, str],
    runner: Callable[[Sequence[str], float], dict[str, Any]],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Verify the ``--version`` of every engine the configuration declares.

    The check is driven by the configuration, so it never asks for vLLM on a
    llama.cpp deployment. It is not skipped when the engine is unknown: an
    undeclared engine on an ``openai_compatible`` endpoint is an operator
    error that must be fixed in the YAML, not a check to relax.
    """
    if config is None:
        return {
            "status": "FAIL",
            "error": "runtime configuration could not be loaded, so the serving engine is unknown",
        }
    declared = _engine_requirements(config)
    undeclared = sorted(role for role, engine in declared.items() if not engine)
    if undeclared:
        return {
            "status": "FAIL",
            "declared": declared,
            "undeclared": undeclared,
            "error": (
                f"inference engine is not declared for {', '.join(undeclared)}; add "
                "'engine: llamacpp' or 'engine: vllm' to that section of the runtime "
                "configuration so the preflight can verify the serving binary"
            ),
        }

    engines: dict[str, Any] = {}
    errors: list[str] = []
    for engine in sorted({value for value in declared.values() if value}):
        executable = executables.get(engine) or engine
        script = f"exec {_shell_executable(executable)} --version"
        outcome = runner(_wsl_bash(distro, script), timeout_seconds)
        stdout = str(outcome.get("stdout") or "").strip()
        stderr = str(outcome.get("stderr") or "").strip()
        record: dict[str, Any] = {
            "label": ENGINE_LABELS.get(engine, engine),
            "executable": executable,
            "status": "PASS" if outcome.get("status") == "PASS" else "FAIL",
            "returncode": outcome.get("returncode"),
            "version": (stdout or stderr).splitlines()[0] if (stdout or stderr) else None,
        }
        if record["status"] != "PASS":
            record["error"] = (stderr or stdout or "version command failed")[:400]
            errors.append(f"{record['label']}: {record['error']}")
        engines[engine] = record

    payload: dict[str, Any] = {
        "status": "FAIL" if errors else "PASS",
        "declared": declared,
        "engines": engines,
    }
    if errors:
        payload["errors"] = errors
    return payload


def _loopback_opener() -> Callable[..., Any]:
    """Open loopback URLs directly, ignoring any system HTTP proxy.

    A configured proxy turns a refused connection into a 502 from the proxy,
    which hides the real cause and makes the check depend on the operator's
    shell environment. Loopback traffic must never leave the machine.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def _fetch_json(url: str, *, timeout_seconds: float, opener: Callable[..., Any]) -> Any:
    with opener(url, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _service_health(
    config: RuntimeConfig | None,
    *,
    expect_services: bool,
    timeout_seconds: float,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Check that each enabled endpoint answers and publishes its model id.

    Services are started by the engine scripts, not by this read-only
    preflight, so the call is opt-in. When it is requested it is a hard
    requirement: an unreachable endpoint or a different model id fails.
    """
    if config is None:
        return {"status": "FAIL", "error": "runtime configuration could not be loaded"}
    if not expect_services:
        return {
            "status": "NOT RUN",
            "reason": (
                "model servers are started separately from this read-only preflight; "
                "rerun with --expect-services once they answer"
            ),
        }
    opener = opener or _loopback_opener()

    targets: dict[str, tuple[str, str]] = {
        "dialogue": (config.dialogue.base_url, config.dialogue.model)
    }
    if config.agent.enabled:
        targets["agent"] = (config.agent.base_url, config.agent.model)

    services: dict[str, Any] = {}
    failed = False
    for role, (base_url, expected_model) in targets.items():
        url = f"{base_url.rstrip('/')}/models"
        try:
            payload = _fetch_json(url, timeout_seconds=timeout_seconds, opener=opener)
        except Exception as exc:  # URLError, HTTPError, socket timeout, bad JSON
            services[role] = {
                "status": "FAIL",
                "url": url,
                "error_code": "ENDPOINT_UNAVAILABLE",
                "error": str(exc)[:400],
            }
            failed = True
            continue
        model_ids = probe_support.model_ids_from_payload(payload)
        try:
            probe_support.require_exact_model(model_ids, expected_model, role)
        except probe_support.ProbeError as exc:
            services[role] = {
                "status": "FAIL",
                "url": url,
                "error_code": exc.code,
                "error": exc.detail,
                "model_ids": model_ids,
            }
            failed = True
            continue
        services[role] = {"status": "PASS", "url": url, "model": expected_model}
    return {"status": "FAIL" if failed else "PASS", "services": services}


def _overall_status(checks: dict[str, Any], required: Sequence[str]) -> str:
    return "PASS" if all(checks.get(name, {}).get("status") == "PASS" for name in required) else "FAIL"


def _required_checks(*, torch_required: bool, expect_services: bool) -> list[str]:
    """Assemble the acceptance gate for this particular deployment.

    ``torch_cuda`` is a vLLM prerequisite: a llama.cpp or Ollama deployment is
    a native binary plus nvidia-smi, so PyTorch is observed but not required.
    ``service_health`` is required only when the operator asked for it, because
    servers are started by the engine scripts and not by this preflight.
    """
    required = [
        "deployment",
        "windows_os",
        "windows_gpu",
        "wsl_status",
        "wsl_version",
        "wsl_distributions",
        "wsl_uname",
        "wsl_gpu",
        "windows_wsl_gpu_consistency",
    ]
    if torch_required:
        required.append("torch_cuda")
    required.append("inference_engine")
    if expect_services:
        required.append("service_health")
    return required


def _evidence_metadata(checks: dict[str, Any], *, overall_status: str) -> dict[str, str]:
    """Separate execution, evidence availability, and acceptance status."""
    statuses = [checks.get(name, {}).get("status") for name in _HARDWARE_EVIDENCE_CHECKS]
    measured = any(status in {"PASS", "FAIL"} for status in statuses)
    hardware_failed = any(status == "FAIL" for status in statuses)
    if overall_status == "PASS":
        evidence_type = "MEASURED"
    elif measured:
        evidence_type = "MEASURED"
    else:
        evidence_type = "NOT AVAILABLE"
    if overall_status == "PASS":
        hardware_validation = "PASS"
    elif hardware_failed:
        hardware_validation = "FAIL"
    else:
        hardware_validation = "NOT RUN"
    return {
        "execution": "RAN",
        "evidence_type": evidence_type,
        "status": overall_status,
        "hardware_validation": hardware_validation,
    }


def _validate_wsl2_listing(result: dict[str, Any], distro: str | None) -> dict[str, Any]:
    """Require at least one WSL2 distribution before CUDA probing."""
    if result["status"] != "PASS":
        return result
    output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".replace("\x00", "")
    version_two_rows = [line for line in output.splitlines() if re.search(r"(?:^|\s)2\s*$", line)]
    if not version_two_rows:
        return {
            **result,
            "status": "FAIL",
            "error": "wsl -l -v did not report a WSL2 distribution",
        }
    if distro and not any(distro.lower() in line.lower() for line in version_two_rows):
        return {
            **result,
            "status": "FAIL",
            "error": f"selected distribution {distro!r} was not reported as WSL2",
        }
    return {**result, "parsed_wsl2_rows": version_two_rows}


def _new_artifact_directory(output_root: str | Path | None, label: str) -> Path:
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = root / f"{stamp}_{label}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{stamp}_{label}_{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


def run_preflight(
    config_path: str | None = None,
    *,
    distro: str | None = None,
    output_root: str | Path | None = None,
    timeout_seconds: float = 30.0,
    vllm_executable: str = "~/.venvs/voicechat-vllm/bin/vllm",
    llama_cpp_executable: str = "llama-server",
    ollama_executable: str = "ollama",
    python_executable: str = "~/.venvs/voicechat-vllm/bin/python",
    expect_services: bool = False,
) -> int:
    deployment, label, config = _deployment_check(config_path)
    directory = _new_artifact_directory(output_root, label)
    checks: dict[str, Any] = {"deployment": deployment}
    checks["windows_os"] = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber | ConvertTo-Json -Compress",
        ],
        timeout_seconds=timeout_seconds,
    )
    checks["windows_gpu"], windows_gpu = _gpu_check(
        "windows", lambda: probe_support.query_windows_gpu(timeout_seconds=timeout_seconds)
    )
    checks["wsl_status"] = _run(["wsl.exe", "--status"], timeout_seconds=timeout_seconds)
    checks["wsl_version"] = _run(["wsl.exe", "--version"], timeout_seconds=timeout_seconds)
    checks["wsl_distributions"] = _validate_wsl2_listing(
        _run(["wsl.exe", "-l", "-v"], timeout_seconds=timeout_seconds), distro
    )
    checks["wsl_uname"] = _run(_wsl_command(distro, "uname", "-s"), timeout_seconds=timeout_seconds)
    if checks["wsl_uname"]["status"] == "PASS" and checks["wsl_uname"]["stdout"].strip() != "Linux":
        checks["wsl_uname"]["status"] = "FAIL"
        checks["wsl_uname"]["error"] = "selected WSL environment did not report Linux"
    checks["wsl_gpu"], wsl_gpu = _gpu_check(
        "wsl", lambda: probe_support.query_wsl_gpu(distro, timeout_seconds=timeout_seconds)
    )
    if windows_gpu is not None and wsl_gpu is not None:
        try:
            probe_support.compare_gpu_observations(windows_gpu, wsl_gpu)
            checks["windows_wsl_gpu_consistency"] = {"status": "PASS"}
        except probe_support.ProbeError as exc:
            checks["windows_wsl_gpu_consistency"] = {
                "status": "FAIL",
                "error_code": exc.code,
                "error": exc.detail,
            }
    else:
        checks["windows_wsl_gpu_consistency"] = {"status": "NOT RUN"}

    wsl_python = _shell_executable(python_executable)
    checks["torch_cuda"] = _parse_json_stdout(
        _run(_wsl_bash(distro, f"exec {wsl_python} -c {shlex.quote(TORCH_PROBE)}"), timeout_seconds=timeout_seconds)
    )
    checks["inference_engine"] = _engine_check(
        config,
        distro=distro,
        executables={
            ENGINE_LLAMA_CPP: llama_cpp_executable,
            ENGINE_VLLM: vllm_executable,
            ENGINE_OLLAMA: ollama_executable,
        },
        runner=lambda command, timeout: _run(command, timeout_seconds=timeout),
        timeout_seconds=timeout_seconds,
    )
    engine_names = _engine_requirements(config) if config is not None else {}
    torch_required = ENGINE_VLLM in set(engine_names.values())
    if not torch_required and checks["torch_cuda"].get("status") != "PASS":
        checks["torch_cuda"] = {
            **checks["torch_cuda"],
            "status": "NOT REQUIRED",
            "reason": (
                "PyTorch CUDA is a vLLM prerequisite; the configured engine(s) "
                f"are {sorted({name for name in engine_names.values() if name}) or 'unknown'}, "
                "so GPU identity is taken from nvidia-smi instead"
            ),
        }
    checks["service_health"] = _service_health(
        config,
        expect_services=expect_services,
        timeout_seconds=min(timeout_seconds, 15.0),
    )

    required = _required_checks(torch_required=torch_required, expect_services=expect_services)
    overall = _overall_status(checks, required)
    evidence = _evidence_metadata(checks, overall_status=overall)
    artifact = {
        "schema_version": 1,
        "artifact_type": "REAL_HARDWARE_PREFLIGHT",
        **evidence,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "runtime_config": label,
        "distro": distro,
        "expected_services": expect_services,
        "overall_status": overall,
        "required_checks": list(required),
        "checks": checks,
        "windows_gpu": windows_gpu.to_dict() if windows_gpu else None,
        "wsl_gpu": wsl_gpu.to_dict() if wsl_gpu else None,
        "service_lifecycle": "NOT USED",
        "models_started": False,
    }
    (directory / "preflight.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if overall == "PASS" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Windows/WSL2 hardware preflight")
    parser.add_argument(
        "--config",
        default=None,
        help="runtime configuration to preflight (default: config/runtime.yaml)",
    )
    parser.add_argument("--distro", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--vllm-executable",
        default="~/.venvs/voicechat-vllm/bin/vllm",
        help="vLLM executable, used only when the configuration declares engine: vllm",
    )
    parser.add_argument(
        "--llama-cpp-executable",
        default="llama-server",
        help="llama-server executable, used only when the configuration declares engine: llamacpp",
    )
    parser.add_argument(
        "--ollama-executable",
        default="ollama",
        help="Ollama executable, used only when the configuration declares engine: ollama",
    )
    parser.add_argument("--python-executable", default="~/.venvs/voicechat-vllm/bin/python")
    parser.add_argument(
        "--expect-services",
        action="store_true",
        help=(
            "also require every enabled endpoint to answer GET /v1/models with the "
            "configured model id (run again after the engine scripts started the servers)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_preflight(
        args.config,
        distro=args.distro,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
        vllm_executable=args.vllm_executable,
        llama_cpp_executable=args.llama_cpp_executable,
        ollama_executable=args.ollama_executable,
        python_executable=args.python_executable,
        expect_services=args.expect_services,
    )


if __name__ == "__main__":
    raise SystemExit(main())
