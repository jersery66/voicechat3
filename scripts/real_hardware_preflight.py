"""Read-only target-workstation preflight for the RTX PRO 6000 candidate.

This script observes Windows, WSL2, PyTorch CUDA, and the configured vLLM
executable. It does not start or stop services, install packages, modify WSL
configuration, select a GPU, or load a model. The existing live probe remains
the authority for real inference acceptance.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.acceptance import probe_support

SUPPORTED_PROFILES = tuple(sorted(probe_support.SUPPORTED_PROFILES))
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


def _check_profile(profile_name: str) -> dict[str, Any]:
    try:
        profile, models = probe_support.validate_profile(profile_name)
    except Exception as exc:
        return {"status": "FAIL", "error": str(exc)}
    return {
        "status": "PASS",
        "profile": profile.name,
        "dialogue_model": models.dialogue,
        "agent_model": profile.agent_model,
        "dialogue_endpoint": profile.dialogue_base_url,
        "agent_endpoint": profile.agent_base_url,
        "expected_gpu_memory_gb": profile.expected_gpu_memory_gb,
        "strict_preflight": profile.strict_preflight,
        "immutable_runtime_contract": profile.immutable_runtime_contract,
    }


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
    "vllm_version",
)


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


def _new_artifact_directory(output_root: str | Path | None, profile_name: str) -> Path:
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = root / f"{stamp}_{profile_name}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{stamp}_{profile_name}_{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


def run_preflight(
    profile_name: str,
    *,
    distro: str | None = None,
    output_root: str | Path | None = None,
    timeout_seconds: float = 30.0,
    vllm_executable: str = "~/.venvs/voicechat-vllm/bin/vllm",
    python_executable: str = "~/.venvs/voicechat-vllm/bin/python",
) -> int:
    directory = _new_artifact_directory(output_root, profile_name)
    checks: dict[str, Any] = {"profile": _check_profile(profile_name)}
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

    wsl_python = shlex.quote(python_executable)
    checks["torch_cuda"] = _parse_json_stdout(
        _run(_wsl_bash(distro, f"exec {wsl_python} -c {shlex.quote(TORCH_PROBE)}"), timeout_seconds=timeout_seconds)
    )
    wsl_vllm = shlex.quote(vllm_executable)
    checks["vllm_version"] = _run(
        _wsl_bash(distro, f"exec {wsl_vllm} --version"), timeout_seconds=timeout_seconds
    )

    required = (
        "profile",
        "windows_os",
        "windows_gpu",
        "wsl_status",
        "wsl_version",
        "wsl_distributions",
        "wsl_uname",
        "wsl_gpu",
        "windows_wsl_gpu_consistency",
        "torch_cuda",
        "vllm_version",
    )
    overall = "PASS" if all(checks[name].get("status") == "PASS" for name in required) else "FAIL"
    evidence = _evidence_metadata(checks, overall_status=overall)
    artifact = {
        "schema_version": 1,
        "artifact_type": "REAL_HARDWARE_PREFLIGHT",
        **evidence,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "profile": profile_name,
        "distro": distro,
        "overall_status": overall,
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
    parser = argparse.ArgumentParser(description="Read-only Windows/WSL2 RTX PRO 6000 preflight")
    parser.add_argument("--profile", required=True, choices=SUPPORTED_PROFILES)
    parser.add_argument("--distro", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--vllm-executable", default="~/.venvs/voicechat-vllm/bin/vllm")
    parser.add_argument("--python-executable", default="~/.venvs/voicechat-vllm/bin/python")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_preflight(
        args.profile,
        distro=args.distro,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
        vllm_executable=args.vllm_executable,
        python_executable=args.python_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
