"""Standalone live acceptance probe for the Windows/WSL2 Blackwell stack.

This command assumes that the launcher has already started both vLLM
services.  It performs observation and inference only; it never starts,
stops, or reconfigures a service.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import requests
from openai import OpenAI

# Make ``python scripts/acceptance/blackwell_live_probe.py`` work from the
# repository root without requiring the application to be installed.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.profiles import DeploymentProfile, RuntimeModels
from inference.factory import build_dialogue_client
try:
    from . import probe_support as support
except ImportError:  # Direct ``python scripts/acceptance/blackwell_live_probe.py`` execution.
    import probe_support as support


SYNTHETIC_AGENT_SYSTEM = "You are a connectivity probe. Return one JSON object only."
SYNTHETIC_AGENT_USER = "Return exactly a JSON object with the status field set to ok."
SYNTHETIC_DIALOGUE_MESSAGES = [
    {"role": "system", "content": "This is a synthetic infrastructure probe; reply briefly in Chinese."},
    {"role": "user", "content": "这是连接性测试，请用一句简短中文回复。"},
]


def exit_code_for_status(status: str) -> int:
    return 0 if status == "PASS" else 1


def write_summary_artifact(directory: str | Path, summary: Mapping[str, Any]) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "acceptance_summary.json"
    path.write_text(json.dumps(dict(summary), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _write_artifact(directory: Path, name: str, payload: Any) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _new_artifact_directory(output_root: str | Path | None, profile_name: str) -> Path:
    root = Path(output_root) if output_root is not None else PROJECT_ROOT / "test_output" / "blackwell_acceptance"
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


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_content(response: Any) -> str:
    choices = _field(response, "choices", []) or []
    if not choices:
        return ""
    choice = choices[0]
    message = _field(choice, "message")
    if message is not None:
        return str(_field(message, "content", "") or "")
    delta = _field(choice, "delta")
    return str(_field(delta, "content", "") or "") if delta is not None else ""


def _usage_dict(value: Any) -> dict[str, Any] | None:
    usage = _field(value, "usage")
    if usage is None:
        return None
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    result = {field: _field(usage, field) for field in fields if _field(usage, field) is not None}
    return result or None


def _parse_agent_response(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping) and "choices" not in response:
        payload = dict(response)
    else:
        content = _response_content(response).strip()
        try:
            payload = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise support.ProbeError("AGENT_INVALID_JSON", "Agent response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise support.ProbeError("AGENT_INVALID_JSON", "Agent response was not a JSON object")
    return payload


def run_agent_json_probe(call_json: Callable[..., Any], *, model: str | None = None,
                         timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Run one direct JSON request; no keyword-routing fallback is involved."""
    request: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": SYNTHETIC_AGENT_SYSTEM},
            {"role": "user", "content": SYNTHETIC_AGENT_USER},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 32,
        "temperature": 0.0,
        "timeout": timeout_seconds,
    }
    if model is not None:
        request["model"] = model
    try:
        response = call_json(**request)
    except Exception as exc:
        raise support.ProbeError("AGENT_INFERENCE_FAILED", str(exc)) from exc
    payload = _parse_agent_response(response)
    if payload.get("status") != "ok":
        raise support.ProbeError(
            "AGENT_SEMANTIC_MISMATCH",
            f"Agent probe expected status='ok', received {payload.get('status')!r}",
        )
    return {
        "status": "PASS",
        "json_valid": True,
        "keys": sorted(str(key) for key in payload),
        "response_format": request["response_format"],
    }


def build_profile_dialogue_client(profile: DeploymentProfile, models: RuntimeModels,
                                  *, timeout_seconds: float):
    client = build_dialogue_client(profile, models, timeout=float(timeout_seconds))
    if client is None:
        raise support.ProbeError("DIALOGUE_CLIENT_BUILD_FAILED", "selected profile did not build a vLLM dialogue client")
    return client


def _default_messages() -> list[dict[str, str]]:
    return [dict(message) for message in SYNTHETIC_DIALOGUE_MESSAGES]


def run_dialogue_nonstream_probe(client: Any, *, timeout_seconds: float,
                                 messages: Sequence[dict[str, str]] | None = None,
                                 clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    request_start = clock()
    try:
        content = str(client.complete_messages(messages=list(messages or _default_messages()), max_tokens=64) or "")
    except Exception as exc:
        raise support.ProbeError("DIALOGUE_NONSTREAM_FAILED", str(exc)) from exc
    response_end = clock()
    if not content.strip():
        raise support.ProbeError("DIALOGUE_NONSTREAM_EMPTY", "dialogue non-stream response was empty")
    return {
        "status": "PASS",
        "content": content,
        "request_start": request_start,
        "response_end": response_end,
        "total_latency_ms": (response_end - request_start) * 1000.0,
    }


def run_dialogue_stream_probe(client: Any, *, timeout_seconds: float,
                              messages: Sequence[dict[str, str]] | None = None,
                              clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    request_start = clock()
    chunks: list[str] = []
    first_content = ""
    first_content_at: float | None = None
    try:
        stream = client.stream_messages(messages=list(messages or _default_messages()))
        for chunk in stream:
            text = str(chunk or "")
            if text and not first_content:
                first_content = text
                first_content_at = clock()
            if text:
                chunks.append(text)
    except Exception as exc:
        raise support.ProbeError("DIALOGUE_STREAM_FAILED", str(exc)) from exc
    stream_end = clock()
    content = "".join(chunks)
    if not content.strip():
        raise support.ProbeError("DIALOGUE_STREAM_EMPTY", "dialogue stream produced no participant content")
    first_content_at = stream_end if first_content_at is None else first_content_at
    total_seconds = max(stream_end - request_start, 0.0)
    return {
        "status": "PASS",
        "content": content,
        "first_content": first_content,
        "request_start": request_start,
        "first_content_at": first_content_at,
        "stream_end": stream_end,
        "client_ttft_ms": (first_content_at - request_start) * 1000.0,
        "client_total_latency_ms": total_seconds * 1000.0,
    }


def validate_thinking_contract(profile: DeploymentProfile, client: Any) -> None:
    options = client._generation_options()
    extra_body = options.get("extra_body", {}) if isinstance(options, Mapping) else {}
    template_kwargs = extra_body.get("chat_template_kwargs", {}) if isinstance(extra_body, Mapping) else {}
    configured = getattr(client, "dialogue_enable_thinking", None)
    expected = profile.dialogue_enable_thinking
    if expected is False:
        if configured is not False or template_kwargs.get("enable_thinking") is not False:
            raise support.ProbeError("THINKING_CONFIG_MISSING", "profile requires enable_thinking=False")
    elif expected is None:
        if "enable_thinking" in template_kwargs:
            raise support.ProbeError("THINKING_CONFIG_UNEXPECTED", "profile does not permit thinking kwargs")
    elif configured != expected or template_kwargs.get("enable_thinking") != expected:
        raise support.ProbeError("THINKING_CONFIG_MISMATCH", "client thinking options differ from profile contract")


def _prepared_messages(client: Any, messages: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    prepare = getattr(client, "_prepare_chat_messages", None)
    return prepare(messages) if callable(prepare) else [dict(message) for message in messages]


def _raw_nonstream_response(client: Any, messages: Sequence[dict[str, str]]) -> Any:
    if getattr(client, "request_mode", "chat") != "chat":
        raise support.ProbeError("DIALOGUE_RAW_UNSUPPORTED", "Blackwell probe requires the chat request mode")
    request = {
        "model": client.model,
        "messages": _prepared_messages(client, messages),
        "stream": False,
        **client._generation_options(64),
    }
    return client._client.chat.completions.create(**request)


def _raw_stream_request(client: Any, messages: Sequence[dict[str, str]]) -> Iterable[Any]:
    if getattr(client, "request_mode", "chat") != "chat":
        raise support.ProbeError("DIALOGUE_RAW_UNSUPPORTED", "Blackwell probe requires the chat request mode")
    request = {
        "model": client.model,
        "messages": _prepared_messages(client, messages),
        "stream": True,
        "stream_options": {"include_usage": True},
        **client._generation_options(),
    }
    try:
        stream = client._client.chat.completions.create(**request)
    except Exception:
        # Older compatible servers may reject the optional usage request. A
        # retry without it keeps raw inspection available while metrics stay
        # explicitly marked unavailable.
        request.pop("stream_options", None)
        stream = client._client.chat.completions.create(**request)
    return stream


def _raw_stream_response(client: Any, messages: Sequence[dict[str, str]]) -> tuple[list[Any], dict[str, Any] | None]:
    chunks = list(_raw_stream_request(client, messages))
    usage = next((item for item in (_usage_dict(chunk) for chunk in chunks) if item), None)
    return chunks, usage


def run_raw_stream_acceptance(
    client: Any,
    *,
    messages: Sequence[dict[str, str]] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Collect all acceptance evidence from one raw streaming request.

    The raw stream owns its timing, visible content, usage, and leakage
    evidence.  ``raw_chunks`` is intentionally an internal return value and
    must not be persisted because it may contain sensitive reasoning text.
    """
    request_start = clock()
    chunks: list[Any] = []
    content_parts: list[str] = []
    first_content_at: float | None = None
    try:
        stream = _raw_stream_request(client, list(messages or _default_messages()))
        for chunk in stream:
            chunks.append(chunk)
            text = _response_content(chunk)
            if text:
                if first_content_at is None:
                    first_content_at = clock()
                content_parts.append(text)
    except support.ProbeError:
        raise
    except Exception as exc:
        raise support.ProbeError("DIALOGUE_RAW_STREAM_FAILED", str(exc)) from exc
    stream_end = clock()
    content = "".join(content_parts)
    if not content.strip():
        raise support.ProbeError("DIALOGUE_RAW_STREAM_EMPTY", "raw dialogue stream produced no participant content")
    usage = next((item for item in (_usage_dict(chunk) for chunk in chunks) if item), None)
    total_seconds = max(stream_end - request_start, 0.0)
    first_content_at = stream_end if first_content_at is None else first_content_at
    completion_tokens = usage.get("completion_tokens") if usage else None
    throughput = None
    if completion_tokens and total_seconds > 0:
        throughput = completion_tokens / total_seconds
    leakage = support.inspect_leakage(content, raw_values=chunks)
    return {
        "status": "PENDING_LEAKAGE_CHECK",
        "content": content,
        "request_start": request_start,
        "first_content_at": first_content_at,
        "stream_end": stream_end,
        "raw_stream_ttft_ms": (first_content_at - request_start) * 1000.0,
        "raw_stream_total_latency_ms": total_seconds * 1000.0,
        "usage": usage,
        "prompt_tokens": usage.get("prompt_tokens") if usage else None,
        "completion_tokens": completion_tokens,
        "raw_stream_output_tokens_per_second": throughput,
        "leakage": leakage,
        "raw_chunks": chunks,
    }


def _check_leakage_or_raise(content: str, raw_values: Iterable[Any]) -> dict[str, Any]:
    report = support.inspect_leakage(content, raw_values=raw_values)
    if report["thinking_markup"]:
        raise support.ProbeError("THINKING_LEAK", "participant-visible <think> markup was returned")
    if report["reasoning_fields"]:
        raise support.ProbeError("REASONING_FIELD_LEAK", "raw response contained non-empty reasoning fields")
    if report["control_tags"]:
        raise support.ProbeError("CONTROL_TAG_LEAK", "participant content contained a legacy control marker")
    return report


def _record_raw_stream_evidence(summary: dict[str, Any], raw_stream: Mapping[str, Any]) -> None:
    """Copy raw-stream evidence before enforcing its hard-fail leakage gate."""
    summary["raw_stream_acceptance_status"] = "RAN"
    summary["raw_stream_ttft_ms"] = raw_stream.get("raw_stream_ttft_ms")
    summary["raw_stream_total_latency_ms"] = raw_stream.get("raw_stream_total_latency_ms")
    summary["raw_stream_completion_tokens"] = raw_stream.get("completion_tokens")
    summary["raw_stream_output_tokens_per_second"] = raw_stream.get("raw_stream_output_tokens_per_second")
    summary["client_ttft_ms"] = raw_stream.get("raw_stream_ttft_ms")
    summary["client_total_latency_ms"] = raw_stream.get("raw_stream_total_latency_ms")
    summary["prompt_tokens"] = raw_stream.get("prompt_tokens")
    summary["completion_tokens"] = raw_stream.get("completion_tokens")
    summary["client_output_tokens_per_second"] = raw_stream.get("raw_stream_output_tokens_per_second")
    leakage = raw_stream.get("leakage", {})
    summary["thinking_leak"] = bool(leakage.get("thinking_markup"))
    summary["reasoning_field_leak"] = bool(leakage.get("reasoning_fields"))
    summary["control_tag_leak"] = bool(leakage.get("control_tags"))
    try:
        _check_leakage_or_raise(raw_stream.get("content", ""), raw_stream.get("raw_chunks", ()))
    except support.ProbeError:
        summary["raw_stream_acceptance_status"] = "FAIL"
        raise
    summary["raw_stream_acceptance_status"] = "PASS"


def _record_raw_nonstream_evidence(summary: dict[str, Any], content: str, raw_response: Any) -> dict[str, Any]:
    """Track raw non-stream execution and preserve a failed state in summary."""
    summary["raw_nonstream_acceptance_status"] = "RAN"
    leakage = support.inspect_leakage(content, raw_values=[raw_response])
    summary["raw_nonstream_thinking_leak"] = bool(leakage["thinking_markup"])
    summary["raw_nonstream_reasoning_field_leak"] = bool(leakage["reasoning_fields"])
    summary["raw_nonstream_control_tag_leak"] = bool(leakage["control_tags"])
    try:
        _check_leakage_or_raise(content, [raw_response])
    except support.ProbeError:
        summary["raw_nonstream_acceptance_status"] = "FAIL"
        raise
    summary["raw_nonstream_acceptance_status"] = "PASS"
    return leakage


def _query_metrics(base_url: str, timeout_seconds: float) -> dict[str, Any]:
    url = base_url.rstrip("/").removesuffix("/v1") + "/metrics"
    try:
        response = requests.get(url, timeout=timeout_seconds)
        return {"reachable": response.ok, "status_code": response.status_code}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def _probe_agent_direct(profile: DeploymentProfile, *, timeout_seconds: float) -> dict[str, Any]:
    client = OpenAI(base_url=profile.agent_base_url, api_key="EMPTY", timeout=timeout_seconds, max_retries=0)

    def call_json(**request: Any) -> Any:
        return client.chat.completions.create(**request)

    return run_agent_json_probe(call_json, model=profile.agent_model, timeout_seconds=timeout_seconds)


def _probe_vllm_version(distro: str | None, executable: str, timeout_seconds: float) -> tuple[str | None, str | None]:
    command = ["wsl.exe"]
    if distro:
        command.extend(["-d", distro])
    if executable.startswith("~/"):
        shell_executable = "$HOME/" + shlex.quote(executable[2:])
    else:
        shell_executable = shlex.quote(executable)
    command.extend(["--", "bash", "-lc", f"exec {shell_executable} --version"])
    try:
        result = support.run_command(command, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "version command failed").strip()
    text = (result.stdout or result.stderr or "").strip()
    return (text.splitlines()[0] if text else None), None


def _summary_template(profile_name: str) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "profile": profile_name,
        "hardware_status": "NOT RUN",
        "windows_gpu": None,
        "wsl_gpu": None,
        "dialogue_model": None,
        "agent_model": None,
        "dialogue_identity_status": "NOT RUN",
        "agent_identity_status": "NOT RUN",
        "agent_inference_status": "NOT RUN",
        "dialogue_nonstream_status": "NOT RUN",
        "dialogue_stream_status": "NOT RUN",
        "production_client_nonstream_status": "NOT RUN",
        "production_client_stream_status": "NOT RUN",
        "raw_nonstream_acceptance_status": "NOT RUN",
        "raw_stream_acceptance_status": "NOT RUN",
        "client_ttft_ms": None,
        "client_total_latency_ms": None,
        "raw_stream_ttft_ms": None,
        "raw_stream_total_latency_ms": None,
        "raw_stream_completion_tokens": None,
        "raw_stream_output_tokens_per_second": None,
        "raw_nonstream_thinking_leak": False,
        "raw_nonstream_reasoning_field_leak": False,
        "raw_nonstream_control_tag_leak": False,
        "prompt_tokens": None,
        "completion_tokens": None,
        "client_output_tokens_per_second": None,
        "server_per_request_metrics": "UNAVAILABLE / NOT ENABLED",
        "vllm_version": None,
        "thinking_leak": False,
        "reasoning_field_leak": False,
        "control_tag_leak": False,
        "gpu_memory_before_mib": None,
        "gpu_memory_after_mib": None,
        "gpu_memory_delta_mib": None,
        "overall_status": "FAIL",
        "errors": [],
        "warnings": [
            "Audio and microphone paths are not loaded by this probe.",
            "TTFT, throughput, and VRAM values are descriptive until later comparison work.",
        ],
    }


def run_probe(profile_name: str, *, distro: str | None = None,
              output_root: str | Path | None = None,
              timeout_seconds: float = 120.0,
              vllm_executable: str = "~/.venvs/voicechat-vllm/bin/vllm") -> int:
    """Execute one real acceptance run and always preserve a summary artifact."""
    directory = _new_artifact_directory(output_root, profile_name)
    summary = _summary_template(profile_name)
    try:
        profile, models = support.validate_profile(profile_name)
        summary["dialogue_model"] = models.dialogue
        summary["agent_model"] = profile.agent_model

        windows = support.validate_gpu_observation(
            support.query_windows_gpu(timeout_seconds=timeout_seconds), source="windows"
        )
        summary["windows_gpu"] = windows.to_dict()
        _write_artifact(directory, "hardware.json", {"windows": windows.to_dict()})
        wsl = support.validate_gpu_observation(
            support.query_wsl_gpu(distro, timeout_seconds=timeout_seconds), source="wsl"
        )
        support.compare_gpu_observations(windows, wsl)
        summary["hardware_status"] = "PASS"
        summary["wsl_gpu"] = wsl.to_dict()
        _write_artifact(directory, "hardware.json", {"windows": windows.to_dict(), "wsl": wsl.to_dict()})

        dialogue_ids = _server_model_ids(profile.dialogue_base_url, timeout_seconds)
        agent_ids = _server_model_ids(profile.agent_base_url, timeout_seconds)
        support.require_exact_model(dialogue_ids, models.dialogue, "dialogue")
        support.require_exact_model(agent_ids, profile.agent_model, "agent")
        summary["dialogue_identity_status"] = "PASS"
        summary["agent_identity_status"] = "PASS"
        _write_artifact(directory, "server_identity.json", {"dialogue": dialogue_ids, "agent": agent_ids})

        before = support.validate_gpu_observation(
            support.query_windows_gpu(timeout_seconds=timeout_seconds, include_utilization=True), source="windows"
        )
        summary["gpu_memory_before_mib"] = before.memory_used_mib
        _write_artifact(directory, "gpu_before.json", before.to_dict())

        agent_result = _probe_agent_direct(profile, timeout_seconds=timeout_seconds)
        summary["agent_inference_status"] = agent_result["status"]
        _write_artifact(directory, "agent_probe.json", agent_result)

        client = build_profile_dialogue_client(profile, models, timeout_seconds=timeout_seconds)
        validate_thinking_contract(profile, client)
        nonstream = run_dialogue_nonstream_probe(client, timeout_seconds=timeout_seconds)
        summary["production_client_nonstream_status"] = "RAN"
        raw_nonstream = _raw_nonstream_response(client, SYNTHETIC_DIALOGUE_MESSAGES)
        try:
            production_nonstream_leak = _check_leakage_or_raise(nonstream["content"], ())
        except support.ProbeError:
            summary["production_client_nonstream_status"] = "FAIL"
            raise
        summary["production_client_nonstream_status"] = "PASS"
        raw_nonstream_content = _response_content(raw_nonstream)
        summary["raw_nonstream_acceptance_status"] = "RAN"
        raw_nonstream_leak = support.inspect_leakage(raw_nonstream_content, raw_values=[raw_nonstream])
        nonstream_usage = _usage_dict(raw_nonstream)
        nonstream_artifact = {
            "production_client_smoke": {**nonstream, "leakage": production_nonstream_leak},
            "raw_acceptance": {
                "status": "RAN",
                "content": raw_nonstream_content,
                "leakage": raw_nonstream_leak,
                "usage": nonstream_usage,
            },
        }
        if not raw_nonstream_content.strip():
            summary["raw_nonstream_acceptance_status"] = "FAIL"
            nonstream_artifact["raw_acceptance"]["status"] = "FAIL"
            _write_artifact(directory, "dialogue_nonstream.json", nonstream_artifact)
            raise support.ProbeError("DIALOGUE_RAW_NONSTREAM_EMPTY", "raw dialogue non-stream response was empty")
        try:
            _record_raw_nonstream_evidence(summary, raw_nonstream_content, raw_nonstream)
        except support.ProbeError:
            nonstream_artifact["raw_acceptance"]["status"] = "FAIL"
            _write_artifact(directory, "dialogue_nonstream.json", nonstream_artifact)
            raise
        nonstream_artifact["raw_acceptance"]["status"] = "PASS"
        _write_artifact(directory, "dialogue_nonstream.json", nonstream_artifact)
        summary["dialogue_nonstream_status"] = "PASS"

        streaming = run_dialogue_stream_probe(client, timeout_seconds=timeout_seconds)
        production_stream_leak = _check_leakage_or_raise(streaming["content"], ())
        summary["dialogue_stream_status"] = "PASS"
        summary["production_client_stream_status"] = "PASS"

        raw_stream = run_raw_stream_acceptance(client, messages=SYNTHETIC_DIALOGUE_MESSAGES)
        raw_stream_artifact = {
            "production_client_smoke": {**streaming, "leakage": production_stream_leak},
            "raw_acceptance": {key: value for key, value in raw_stream.items() if key != "raw_chunks"},
            "server_per_request_metrics": support.extract_server_metrics(raw_stream["raw_chunks"]),
        }
        try:
            _record_raw_stream_evidence(summary, raw_stream)
        except support.ProbeError:
            raw_stream_artifact["raw_acceptance"]["status"] = summary["raw_stream_acceptance_status"]
            _write_artifact(directory, "dialogue_stream.json", raw_stream_artifact)
            raise
        raw_stream_artifact["raw_acceptance"]["status"] = summary["raw_stream_acceptance_status"]
        _write_artifact(directory, "dialogue_stream.json", raw_stream_artifact)
        summary["server_per_request_metrics"] = support.extract_server_metrics(raw_stream["raw_chunks"])
        _write_artifact(directory, "server_metrics.json", _query_metrics(profile.dialogue_base_url, timeout_seconds))

        after = support.validate_gpu_observation(
            support.query_windows_gpu(timeout_seconds=timeout_seconds, include_utilization=True), source="windows"
        )
        summary["gpu_memory_after_mib"] = after.memory_used_mib
        summary["gpu_memory_delta_mib"] = after.memory_used_mib - before.memory_used_mib
        _write_artifact(directory, "gpu_after.json", after.to_dict())
        version, version_warning = _probe_vllm_version(distro, vllm_executable, timeout_seconds)
        if version_warning:
            summary["warnings"].append(f"vLLM version unavailable: {version_warning}")
        summary["vllm_version"] = version
        summary["overall_status"] = "PASS"
    except support.ProbeError as exc:
        summary["errors"].append(str(exc))
    except Exception as exc:  # Preserve a failed artifact for unexpected probe errors.
        summary["errors"].append(f"UNEXPECTED_PROBE_ERROR: {exc}")
    finally:
        write_summary_artifact(directory, summary)
    return exit_code_for_status(summary["overall_status"])


def _server_model_ids(base_url: str, timeout_seconds: float) -> list[str]:
    if not base_url.startswith("http://127.0.0.1:"):
        raise support.ProbeError("ENDPOINT_CONTRACT", f"probe endpoint is not local loopback: {base_url}")
    try:
        response = requests.get(base_url.rstrip("/") + "/models", timeout=timeout_seconds)
        response.raise_for_status()
        return support.model_ids_from_payload(response.json())
    except Exception as exc:
        raise support.ProbeError("MODEL_IDENTITY_QUERY_FAILED", str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live Windows/WSL2 Blackwell vLLM acceptance probe")
    parser.add_argument("--profile", required=True, choices=sorted(support.SUPPORTED_PROFILES))
    parser.add_argument("--distro", default=None)
    parser.add_argument("--output-root", default=str(Path("test_output") / "blackwell_acceptance"))
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--vllm-executable", default="~/.venvs/voicechat-vllm/bin/vllm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_probe(
        args.profile,
        distro=args.distro,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
        vllm_executable=args.vllm_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
