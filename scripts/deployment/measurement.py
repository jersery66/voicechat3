"""Evidence-aware passive timing and performance measurement contracts.

This module deliberately has no dependency on the dialogue, policy, delivery,
STT, or TTS runtime.  Callers may observe their existing events and pass the
same generation identity into these helpers; the helpers never make a
business decision or change runtime state.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EVIDENCE_MEASURED = "MEASURED"
EVIDENCE_SIMULATED = "SIMULATED"
EVIDENCE_NOT_AVAILABLE = "NOT AVAILABLE"
EVIDENCE_TYPES = {EVIDENCE_MEASURED, EVIDENCE_SIMULATED, EVIDENCE_NOT_AVAILABLE}

STATUS_SUCCESS = "SUCCESS"
STATUS_CANCELLED = "CANCELLED"
STATUS_FAILED = "FAILED"
STATUS_NOT_RUN = "NOT RUN"

TIMING_EVENTS = {
    "turn_start",
    "speech_start",
    "speech_end",
    "speech_detected",
    "vad_endpoint",
    "asr_request_start",
    "asr_end",
    "asr_final_text",
    "policy_start",
    "policy_end",
    "rag_start",
    "rag_end",
    "llm_request_start",
    "llm_first_token",
    "llm_first_sentence",
    "first_sentence_ready",
    "llm_generation_end",
    "sentence_enqueued",
    "sentence_delivery_start",
    "sentence_delivery_complete",
    "generation_cancelled",
    "generation_completed",
    "tts_request_start",
    "tts_first_audio_ready",
    "tts_generation_complete",
    "playback_start",
    "playback_end",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_git_commit() -> str | None:
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


def _delta_ms(start_ns: int | None, end_ns: int | None) -> float | None:
    if start_ns is None or end_ns is None or end_ns < start_ns:
        return None
    return (end_ns - start_ns) / 1_000_000.0


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Return the fixed linear-interpolation percentile used by this project."""
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must satisfy 0 <= quantile <= 1")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 10)


def token_metrics(usage: Mapping[str, Any] | None, *, generation_ms: float | None) -> dict[str, Any]:
    """Normalize real API usage; never infer tokens from characters."""
    if not isinstance(usage, Mapping):
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "tokens_per_second": None,
            "status": EVIDENCE_NOT_AVAILABLE,
        }
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if not isinstance(completion, (int, float)) or isinstance(completion, bool) or not isinstance(generation_ms, (int, float)) or generation_ms <= 0:
        throughput = None
    else:
        throughput = float(completion) / (float(generation_ms) / 1000.0)
    status = STATUS_SUCCESS if isinstance(completion, (int, float)) and not isinstance(completion, bool) else EVIDENCE_NOT_AVAILABLE
    return {
        "prompt_tokens": prompt if isinstance(prompt, (int, float)) and not isinstance(prompt, bool) else None,
        "completion_tokens": completion if isinstance(completion, (int, float)) and not isinstance(completion, bool) else None,
        "total_tokens": total if isinstance(total, (int, float)) and not isinstance(total, bool) else None,
        "tokens_per_second": throughput,
        "status": status,
    }


def build_measurement_event(
    *,
    component: str,
    metric_name: str,
    value: float | int | None,
    unit: str,
    evidence_type: str,
    status: str = STATUS_SUCCESS,
    source: str = "passive_measurement",
    timestamp_utc: str | None = None,
    git_commit: str | None = None,
    profile: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    generation_id: int | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"unsupported evidence_type: {evidence_type}")
    return {
        "schema_version": 1,
        "measurement_id": os.urandom(8).hex(),
        "evidence_type": evidence_type,
        "timestamp_utc": timestamp_utc or utc_timestamp(),
        "git_commit": git_commit if git_commit is not None else current_git_commit(),
        "profile": profile,
        "component": component,
        "session_id": session_id,
        "turn_id": turn_id,
        "generation_id": generation_id,
        "request_id": request_id,
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "source": source,
        "status": status,
    }


class TimingRecorder:
    """Record monotonic event boundaries and derive passive latency metrics."""

    def __init__(self, *, clock_ns: Callable[[], int] = time.perf_counter_ns) -> None:
        self._clock_ns = clock_ns
        self._events: dict[str, int] = {}
        self.status = STATUS_NOT_RUN

    @property
    def event_times_ns(self) -> dict[str, int]:
        return dict(self._events)

    def mark(self, event_name: str, *, at_ns: int | None = None) -> int:
        if event_name not in TIMING_EVENTS:
            raise ValueError(f"unsupported timing event: {event_name}")
        value = self._clock_ns() if at_ns is None else int(at_ns)
        previous = self._events.get(event_name)
        # Boundary events are first-observation points.  This prevents a late
        # duplicate callback from changing TTFT or first-sentence latency.
        if previous is None:
            self._events[event_name] = value
        if event_name in {"llm_generation_end", "playback_end"} and self.status == STATUS_NOT_RUN:
            self.status = STATUS_SUCCESS
        return self._events[event_name]

    def finish(self, status: str = STATUS_SUCCESS) -> None:
        if "generation_completed" not in self._events:
            self.mark("generation_completed")
        self.status = status

    def cancel(self) -> None:
        self.mark("generation_cancelled")
        self.status = STATUS_CANCELLED

    def fail(self) -> None:
        self.status = STATUS_FAILED

    def metrics(self) -> dict[str, float | None]:
        event = self._events
        first_sentence = event.get("first_sentence_ready")
        if first_sentence is None:
            first_sentence = event.get("llm_first_sentence")
        first_audio = event.get("playback_start")
        if first_audio is None:
            first_audio = event.get("tts_first_audio_ready")
        result: dict[str, float | None] = {
            "ttft_ms": _delta_ms(event.get("llm_request_start"), event.get("llm_first_token")),
            "first_sentence_latency_ms": _delta_ms(event.get("llm_request_start"), first_sentence),
            "generation_latency_ms": _delta_ms(event.get("llm_request_start"), event.get("llm_generation_end")),
            "speech_duration_ms": _delta_ms(event.get("speech_start"), event.get("speech_end")),
            "endpoint_detection_latency_ms": _delta_ms(event.get("speech_end"), event.get("vad_endpoint")),
            "asr_processing_latency_ms": _delta_ms(event.get("asr_request_start"), event.get("asr_end")),
            "speech_end_to_text_latency_ms": _delta_ms(event.get("speech_end"), event.get("asr_final_text")),
            "policy_latency_ms": _delta_ms(event.get("policy_start"), event.get("policy_end")),
            "rag_latency_ms": _delta_ms(event.get("rag_start"), event.get("rag_end")),
            "tts_first_audio_latency_ms": _delta_ms(event.get("tts_request_start"), event.get("tts_first_audio_ready")),
            "tts_generation_latency_ms": _delta_ms(event.get("tts_request_start"), event.get("tts_generation_complete")),
            "playback_delay_ms": _delta_ms(event.get("tts_first_audio_ready"), event.get("playback_start")),
            "speech_to_first_text_ms": _delta_ms(event.get("speech_end"), event.get("asr_final_text")),
            "speech_to_first_token_ms": _delta_ms(event.get("speech_end"), event.get("llm_first_token")),
            "speech_to_first_sentence_ms": _delta_ms(event.get("speech_end"), first_sentence),
            "speech_to_first_audio_ms": _delta_ms(event.get("speech_end"), first_audio),
            "full_turn_duration_ms": _delta_ms(event.get("turn_start"), event.get("playback_end")),
            "sentence_delivery_latency_ms": _delta_ms(
                event.get("sentence_delivery_start"), event.get("sentence_delivery_complete")
            ),
        }
        return result

    def measurement_events(self, **context: Any) -> list[dict[str, Any]]:
        """Project derived durations into the common measurement-event schema."""
        evidence_type = context.pop("evidence_type", EVIDENCE_NOT_AVAILABLE)
        component = context.pop("component", "conversation")
        context.pop("status", None)
        result = []
        for metric_name, value in self.metrics().items():
            result.append(
                build_measurement_event(
                    component=component,
                    metric_name=metric_name,
                    value=value,
                    unit="ms",
                    evidence_type=evidence_type,
                    status=self.status,
                    **context,
                )
            )
        return result


def aggregate_metric(
    samples: Iterable[Mapping[str, Any]],
    metric_name: str,
    *,
    evidence_type: str = EVIDENCE_MEASURED,
) -> dict[str, Any]:
    values = [
        float(sample["value"])
        for sample in samples
        if sample.get("metric_name") == metric_name
        and sample.get("status") == STATUS_SUCCESS
        and sample.get("evidence_type") == evidence_type
        and isinstance(sample.get("value"), (int, float))
        and not isinstance(sample.get("value"), bool)
    ]
    if not values:
        return {"count": 0, "median": None, "p95": None, "min": None, "max": None}
    return {
        "count": len(values),
        "median": float(statistics.median(values)),
        "p95": percentile(values, 0.95),
        "min": min(values),
        "max": max(values),
    }


def build_performance_summary(
    samples: Iterable[Mapping[str, Any]],
    *,
    profile: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Aggregate successful measured samples without comparing model quality."""
    materialized = list(samples)
    names = sorted({str(sample.get("metric_name")) for sample in materialized if sample.get("metric_name")})
    metrics = {
        name: aggregate_metric(materialized, name, evidence_type=EVIDENCE_MEASURED)
        for name in names
    }
    has_measured = any(item["count"] > 0 for item in metrics.values())
    return {
        "schema_version": 1,
        "timestamp_utc": utc_timestamp(),
        "git_commit": git_commit if git_commit is not None else current_git_commit(),
        "profile": profile,
        "status": "PASS" if has_measured else STATUS_NOT_RUN,
        "evidence_type": EVIDENCE_MEASURED if has_measured else EVIDENCE_NOT_AVAILABLE,
        "real_performance_summary": has_measured,
        "metrics": metrics,
        "model_comparison": "NOT PERFORMED",
        "promotion": "NOT APPROVED",
    }


def write_performance_summary(
    path: str | Path,
    samples: Iterable[Mapping[str, Any]],
    *,
    profile: str | None = None,
    git_commit: str | None = None,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            build_performance_summary(samples, profile=profile, git_commit=git_commit),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination


def initialise_measurement_artifacts(
    output_root: str | Path,
    *,
    profile: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Path]:
    """Create empty, explicitly-not-run observability artifacts."""
    from scripts.deployment.observability import initialise_observability_artifacts

    return initialise_observability_artifacts(
        output_root,
        profile=profile,
        git_commit=git_commit if git_commit is not None else current_git_commit(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Initialize evidence-aware measurement artifacts")
    parser.add_argument("--profile", default=os.environ.get("VOICECHAT_DEPLOYMENT_PROFILE"))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "test_output" / "observability"))
    args = parser.parse_args(argv)
    paths = initialise_measurement_artifacts(args.output_root, profile=args.profile)
    print(json.dumps({name: str(path) for name, path in paths.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
