"""Small, dependency-light helpers for the live acceptance probe.

The helpers in this module deliberately do not start or stop model services,
load STT/TTS providers, or select a GPU.  They only validate observations and
normalize evidence collected by the standalone probe.

The deployment under test is described by one runtime configuration
(``config/runtime.yaml`` by default).  Hardware identity is observed, never
assumed: the probe reports what GPU is present instead of requiring a
hardcoded model or a fixed VRAM threshold.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from deployment.runtime_config import (
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OLLAMA,
    RuntimeConfig,
    load_runtime_config,
)
from urllib.parse import urlparse


GPU_QUERY_FIELDS = (
    "index",
    "name",
    "memory.total",
    "memory.used",
    "memory.free",
    "driver_version",
    "uuid",
)
GPU_SNAPSHOT_FIELDS = GPU_QUERY_FIELDS + ("utilization.gpu",)
_REASONING_FIELDS = {"reasoning_content", "reasoning", "thinking"}
_CONTROL_MARKERS = ("[REC_", "[END_", "[SCALE:", "|||")


class ProbeError(RuntimeError):
    """A deterministic, operator-facing acceptance failure."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class GPUObservation:
    index: int
    name: str
    memory_total_mib: int
    memory_used_mib: int
    memory_free_mib: int
    driver_version: str | None
    uuid: str | None
    utilization_gpu: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_runtime_deployment(config_path: str | None = None) -> RuntimeConfig:
    """Load and validate the runtime configuration the probe will observe.

    The configuration is the single source of truth for models, endpoints,
    and providers; the probe refuses to run when it is not an explicit,
    loopback-only, OpenAI-compatible or Ollama deployment.  A disabled agent
    is a valid deployment shape and needs no endpoint.
    """
    try:
        config = load_runtime_config(config_path) if config_path else _load_default()
    except (OSError, ValueError, TypeError) as exc:
        raise ProbeError("RUNTIME_CONFIG_INVALID", f"runtime configuration could not be loaded: {exc}") from exc

    errors: list[str] = []
    for label, provider in (("dialogue", config.dialogue.provider), ("agent", config.agent.provider)):
        if provider not in {PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA}:
            errors.append(f"unsupported {label} provider: {provider!r}")
    for label, value in (
        ("dialogue_model", config.dialogue.model),
        ("dialogue_base_url", config.dialogue.base_url),
    ):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} is empty")
    if config.agent.enabled:
        for label, value in (
            ("agent_model", config.agent.model),
            ("agent_base_url", config.agent.base_url),
        ):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label} is empty")
    endpoints = [("dialogue_base_url", config.dialogue.base_url)]
    if config.agent.enabled:
        endpoints.append(("agent_base_url", config.agent.base_url))
    for label, value in endpoints:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            errors.append(f"{label} must be a loopback HTTP URL with an explicit port")
    if errors:
        raise ProbeError("RUNTIME_CONFIG_CONTRACT", "; ".join(errors))
    return config


def _load_default() -> RuntimeConfig:
    from deployment.runtime_config import get_runtime_config

    return get_runtime_config()


def run_command(command: Sequence[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    """Run an observation-only command with captured output."""
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _parse_int(value: str, *, field: str) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError) as exc:
        raise ProbeError("GPU_QUERY_INVALID", f"invalid {field}: {value!r}") from exc


def parse_nvidia_smi_csv(output: str, *, include_utilization: bool = False) -> list[GPUObservation]:
    """Parse ``nvidia-smi --format=csv,noheader,nounits`` output."""
    rows = [row for row in csv.reader(io.StringIO(output)) if any(cell.strip() for cell in row)]
    observations: list[GPUObservation] = []
    minimum = len(GPU_SNAPSHOT_FIELDS if include_utilization else GPU_QUERY_FIELDS)
    if any(len(row) < minimum for row in rows):
        raise ProbeError("GPU_QUERY_INVALID", "nvidia-smi returned incomplete CSV columns")
    for row in rows:
        utilization = None
        if include_utilization:
            raw_utilization = row[7].strip()
            if raw_utilization:
                try:
                    utilization = float(raw_utilization)
                except ValueError as exc:
                    raise ProbeError("GPU_QUERY_INVALID", f"invalid utilization.gpu: {raw_utilization!r}") from exc
        observations.append(
            GPUObservation(
                index=_parse_int(row[0], field="index"),
                name=row[1].strip(),
                memory_total_mib=_parse_int(row[2], field="memory.total"),
                memory_used_mib=_parse_int(row[3], field="memory.used"),
                memory_free_mib=_parse_int(row[4], field="memory.free"),
                driver_version=row[5].strip() or None,
                uuid=row[6].strip() or None,
                utilization_gpu=utilization,
            )
        )
    return observations


def _nvidia_smi_command(*, include_utilization: bool = False) -> list[str]:
    fields = GPU_SNAPSHOT_FIELDS if include_utilization else GPU_QUERY_FIELDS
    return [
        "nvidia-smi",
        f"--query-gpu={','.join(fields)}",
        "--format=csv,noheader,nounits",
    ]


def _query_gpu(command: Sequence[str], *, timeout_seconds: float, include_utilization: bool = False) -> list[GPUObservation]:
    try:
        result = run_command(command, timeout_seconds=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("GPU_QUERY_FAILED", str(exc)) from exc
    return _query_gpu_result(result, include_utilization=include_utilization)


def _result_is_missing_executable(result: subprocess.CompletedProcess[str]) -> bool:
    """Return whether a WSL command failed because the executable was absent."""
    text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return result.returncode == 127 or "command not found" in text or "no such file or directory" in text


def _query_gpu_result(
    result: subprocess.CompletedProcess[str], *, include_utilization: bool = False
) -> list[GPUObservation]:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "nvidia-smi failed").strip()
        raise ProbeError("GPU_QUERY_FAILED", detail)
    observations = parse_nvidia_smi_csv(result.stdout, include_utilization=include_utilization)
    if not observations:
        raise ProbeError("GPU_QUERY_FAILED", "nvidia-smi returned no NVIDIA GPU")
    return observations


def query_windows_gpu(*, timeout_seconds: float = 10.0, include_utilization: bool = False) -> list[GPUObservation]:
    return _query_gpu(
        _nvidia_smi_command(include_utilization=include_utilization),
        timeout_seconds=timeout_seconds,
        include_utilization=include_utilization,
    )


def query_wsl_gpu(distro: str | None = None, *, timeout_seconds: float = 10.0,
                  include_utilization: bool = False) -> list[GPUObservation]:
    command = ["wsl.exe"]
    if distro:
        command.extend(["-d", distro])
    command.extend(["--", *_nvidia_smi_command(include_utilization=include_utilization)])
    try:
        result = run_command(command, timeout_seconds=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("GPU_QUERY_FAILED", str(exc)) from exc
    if _result_is_missing_executable(result):
        fallback = ["wsl.exe"]
        if distro:
            fallback.extend(["-d", distro])
        fallback.extend(
            [
                "--",
                "/usr/lib/wsl/lib/nvidia-smi",
                *_nvidia_smi_command(include_utilization=include_utilization)[1:],
            ]
        )
        try:
            result = run_command(fallback, timeout_seconds=timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProbeError("GPU_QUERY_FAILED", str(exc)) from exc
    return _query_gpu_result(result, include_utilization=include_utilization)


def _normalized_gpu_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def validate_gpu_observation(
    observations: Sequence[GPUObservation],
    *,
    source: str,
) -> GPUObservation:
    """Return the single observed GPU without imposing a hardware target.

    GPU identity is reported, not enforced: a different GPU is a fact for
    the operator to interpret, not a code-level rejection, and no fixed
    VRAM threshold decides whether the deployment may run.
    """
    if len(observations) != 1:
        raise ProbeError(
            "UNSUPPORTED_MULTI_GPU_CURRENT_CONTRACT",
            f"{source} reports {len(observations)} NVIDIA GPUs; explicit device selection is not implemented",
        )
    return observations[0]


def compare_gpu_observations(
    windows: GPUObservation,
    wsl: GPUObservation,
) -> None:
    """Check that WSL and Windows observe the same physical GPU."""
    normalized_windows = _normalized_gpu_name(windows.name)
    normalized_wsl = _normalized_gpu_name(wsl.name)
    if normalized_windows != normalized_wsl:
        raise ProbeError("GPU_CONSISTENCY_MISMATCH", "WSL GPU differs from the Windows GPU")
    tolerance = max(4096, int(max(windows.memory_total_mib, wsl.memory_total_mib) * 0.10))
    if abs(windows.memory_total_mib - wsl.memory_total_mib) > tolerance:
        raise ProbeError(
            "GPU_CONSISTENCY_MISMATCH",
            f"Windows/WSL memory totals differ: {windows.memory_total_mib} vs {wsl.memory_total_mib} MiB",
        )


def model_ids_from_payload(payload: Mapping[str, Any]) -> list[str]:
    data = payload.get("data", []) if isinstance(payload, Mapping) else []
    return [str(item.get("id")) for item in data if isinstance(item, Mapping) and item.get("id")]


def require_exact_model(model_ids: Sequence[str], expected: str, role: str) -> str:
    if expected not in model_ids:
        raise ProbeError(
            "MODEL_IDENTITY_MISMATCH",
            f"{role} expected {expected!r}, server exposed {list(model_ids)!r}",
        )
    return expected


def _walk_raw(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            yield key_path, child
            yield from _walk_raw(child, key_path)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_raw(child, f"{path}[{index}]")
        return
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, Mapping):
        yield from _walk_raw(attrs, path)
        return
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            yield from _walk_raw(dumped, path)


def inspect_leakage(content: str, *, raw_values: Iterable[Any] = ()) -> dict[str, Any]:
    text = str(content or "")
    lowered = text.lower()
    reasoning_fields: dict[str, int] = {}
    for value in raw_values:
        for path, child in _walk_raw(value):
            field_name = path.rsplit(".", 1)[-1].split("[", 1)[0]
            if field_name in _REASONING_FIELDS and child not in (None, "", [], {}):
                reasoning_fields[field_name] = max(reasoning_fields.get(field_name, 0), len(str(child)))
    thinking_markup = [marker for marker in ("<think>", "</think>") if marker in lowered]
    control_tags = [marker for marker in _CONTROL_MARKERS if marker.lower() in lowered]
    return {
        "thinking_markup": thinking_markup,
        "reasoning_fields": reasoning_fields,
        "control_tags": control_tags,
    }


def extract_server_metrics(value: Any) -> dict[str, Any] | str:
    aliases = (
        "time_to_first_token_ms",
        "generation_time_ms",
        "queue_time_ms",
        "mean_itl_ms",
        "tokens_per_second",
    )
    found: dict[str, Any] = {}
    for path, child in _walk_raw(value):
        field_name = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if field_name in aliases and isinstance(child, (int, float)):
            found[field_name] = child
    return found if found else "UNAVAILABLE / NOT ENABLED"
