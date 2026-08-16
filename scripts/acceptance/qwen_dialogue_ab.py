"""Profile-owned Qwen dialogue comparison harness.

This acceptance-only tool runs a fixed synthetic scenario matrix against one
already-running dialogue profile at a time, then structurally compares two
saved runs.  It never starts or stops services, loads participant data, or
decides whether a model should be promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.profiles import DeploymentProfile, RuntimeModels, get_deployment_profile, resolve_runtime_models
from inference.factory import build_dialogue_client

try:
    from . import probe_support as support
except ImportError:  # Direct ``python scripts/acceptance/qwen_dialogue_ab.py`` execution.
    import probe_support as support


SUPPORTED_PROFILES = {
    "rtxpro6000_96g",
    "rtxpro6000_96g_qwen38_candidate",
}
BASELINE_PROFILE = "rtxpro6000_96g"
CANDIDATE_PROFILE = "rtxpro6000_96g_qwen38_candidate"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "test_output" / "qwen_dialogue_ab"


class ABError(RuntimeError):
    """A deterministic, operator-facing A/B harness failure."""


@dataclass(frozen=True)
class ABScenario:
    scenario_id: str
    title: str
    user_text: str
    history: tuple[tuple[str, str], ...] = ()


REVIEW_DIMENSIONS = (
    "chinese_naturalness",
    "empathy_and_reflection",
    "one_primary_question",
    "no_premature_advice",
    "scale_question_semantics",
    "asr_ambiguity_confirmation",
    "resistance_handling",
    "interruption_response",
    "no_control_leakage",
)


# These are synthetic prompts only.  They exercise ordinary language
# realization and ambiguity handling without asking the dialogue model to
# choose an action, scale, intervention, or session outcome.
SCENARIOS: tuple[ABScenario, ...] = (
    ABScenario("greeting", "普通开场", "你好，我想聊聊最近的生活。"),
    ABScenario("sleep", "睡眠表达", "这几天晚上入睡有点困难，白天也有些疲惫。"),
    ABScenario("worry", "担忧表达", "明天有一件重要的事，我有点紧张，想先把感受说清楚。"),
    ABScenario("family", "想家表达", "我最近有些想家，想到家人时心里会有点复杂。"),
    ABScenario("ambiguity", "含糊表达", "有时候我也说不清自己到底是累还是烦。"),
    ABScenario("asr_ambiguity", "ASR 语义边界", "不是每天都睡不好，大概一周有两三天。"),
    ABScenario("scale_wording", "已批准量表问法", "请用自然口语问我最近睡眠情况，只问一个主要问题。"),
    ABScenario("resistance", "拒答与阻抗", "我不想回答这个问题，也不想被劝说。"),
    ABScenario("interruption", "中途暂停表达", "我想先停一下，等会儿再继续说。"),
    ABScenario(
        "multi_turn",
        "多轮上下文",
        "把这些说出来以后，我想知道接下来可以先从哪里整理。",
        history=(
            ("user", "我最近事情比较多，脑子里总是堆在一起。"),
            ("assistant", "听起来你最近承受了不少事情，可以慢慢把最困扰你的部分说出来。"),
        ),
    ),
    ABScenario("progress", "积极反馈", "刚才把事情说出来以后，我感觉稍微清楚了一些。"),
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


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


def _system_prompt() -> str:
    """Load the frozen participant-facing prompt without duplicating it here."""
    from config import SYSTEM_PROMPT

    return str(SYSTEM_PROMPT).strip()


def _build_messages(scenario: ABScenario) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": _system_prompt()}]
    messages.extend({"role": role, "content": content} for role, content in scenario.history)
    messages.append({"role": "user", "content": scenario.user_text})
    return messages


def prompt_hash(messages: Sequence[Mapping[str, str]]) -> str:
    canonical = json.dumps(
        [dict(message) for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def scenario_matrix_hash() -> str:
    matrix = [
        {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "user_text": scenario.user_text,
            "history": list(scenario.history),
        }
        for scenario in SCENARIOS
    ]
    return prompt_hash(({"content": json.dumps(matrix, ensure_ascii=False, sort_keys=True)},))


def validate_ab_profile(name: str) -> tuple[DeploymentProfile, RuntimeModels]:
    if name not in SUPPORTED_PROFILES:
        raise ABError(f"unsupported A/B profile: {name!r}")
    profile = get_deployment_profile(name)
    models = resolve_runtime_models(profile, environment={})
    if profile.runtime_backend != "vllm":
        raise ABError("A/B harness requires a vLLM profile")
    if profile.expected_gpu_memory_gb != 96:
        raise ABError("A/B harness requires the RTX PRO 6000 96GB profiles")
    if not profile.immutable_runtime_contract or not profile.strict_preflight:
        raise ABError("A/B profile must be immutable and strict")
    return profile, models


def _new_run_directory(output_root: str | Path | None, profile_name: str) -> Path:
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = root / f"{stamp}_{profile_name}"
    suffix = 1
    while directory.exists():
        directory = root / f"{stamp}_{profile_name}_{suffix}"
        suffix += 1
    directory.mkdir()
    return directory


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _generation_options(client: Any) -> dict[str, Any]:
    options = getattr(client, "_generation_options", None)
    if not callable(options):
        raise ABError("profile-built dialogue client does not expose generation options")
    return _json_safe(options())


def _validate_thinking_contract(profile: DeploymentProfile, client: Any, options: Mapping[str, Any]) -> None:
    extra_body = options.get("extra_body", {})
    template_kwargs = extra_body.get("chat_template_kwargs", {}) if isinstance(extra_body, Mapping) else {}
    configured = getattr(client, "dialogue_enable_thinking", None)
    if profile.dialogue_enable_thinking is False:
        if configured is not False or template_kwargs.get("enable_thinking") is not False:
            raise ABError("candidate profile is missing profile-owned enable_thinking=False")
    elif "enable_thinking" in template_kwargs:
        raise ABError("baseline profile unexpectedly sends enable_thinking")


def _measure_nonstream(client: Any, messages: Sequence[Mapping[str, str]], *, max_tokens: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        content = str(client.complete_messages(messages=list(messages), max_tokens=max_tokens) or "")
    except Exception as exc:
        raise ABError(f"non-stream request failed: {exc}") from exc
    finished = time.perf_counter()
    if not content.strip():
        raise ABError("non-stream response was empty")
    return {
        "content": content,
        "total_latency_ms": (finished - started) * 1000.0,
    }


def _measure_stream(client: Any, messages: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    started = time.perf_counter()
    first_content_at: float | None = None
    chunks: list[str] = []
    try:
        stream = client.stream_messages(messages=list(messages))
        for chunk in stream:
            text = str(chunk or "")
            if text and first_content_at is None:
                first_content_at = time.perf_counter()
            if text:
                chunks.append(text)
    except Exception as exc:
        raise ABError(f"stream request failed: {exc}") from exc
    finished = time.perf_counter()
    content = "".join(chunks)
    if not content.strip():
        raise ABError("stream response was empty")
    first_content_at = finished if first_content_at is None else first_content_at
    return {
        "content": content,
        "client_ttft_ms": (first_content_at - started) * 1000.0,
        "client_total_latency_ms": (finished - started) * 1000.0,
    }


def _leakage_failures(content: str, scenario_id: str, phase: str) -> list[str]:
    leakage = support.inspect_leakage(content)
    failures: list[str] = []
    if leakage["thinking_markup"]:
        failures.append(
            f"{scenario_id}/{phase}: thinking markup leaked ({', '.join(leakage['thinking_markup'])})"
        )
    if leakage["reasoning_fields"]:
        failures.append(
            f"{scenario_id}/{phase}: reasoning field leaked ({', '.join(leakage['reasoning_fields'])})"
        )
    if leakage["control_tags"]:
        failures.append(
            f"{scenario_id}/{phase}: control marker leaked ({', '.join(leakage['control_tags'])})"
        )
    return failures


def _summary_template(profile_name: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "profile": profile_name,
        "dialogue_model": None,
        "dialogue_base_url": None,
        "dialogue_enable_thinking": None,
        "generation_options": None,
        "scenario_matrix_hash": scenario_matrix_hash(),
        "prompt_hash": None,
        "server_model_ids": None,
        "scenario_results": [],
        "leakage_failures": [],
        "status": "NOT RUN",
        "promotion_status": "NOT APPROVED",
        "human_review_required": True,
        "human_review_dimensions": list(REVIEW_DIMENSIONS),
        "real_hardware_status": "NOT RUN",
        "errors": [],
        "warnings": [
            "This harness uses fixed synthetic prompts only.",
            "Latency and output differences are descriptive; no automatic quality winner or promotion is produced.",
        ],
    }


def run_profile(
    profile_name: str,
    *,
    output_root: str | Path | None = None,
    timeout_seconds: float = 120.0,
    max_tokens: int = 128,
) -> int:
    """Run the fixed matrix against one already-running profile."""
    directory = _new_run_directory(output_root, profile_name)
    artifact = _summary_template(profile_name)
    try:
        profile, models = validate_ab_profile(profile_name)
        artifact.update(
            {
                "dialogue_model": models.dialogue,
                "dialogue_base_url": profile.dialogue_base_url,
                "dialogue_enable_thinking": profile.dialogue_enable_thinking,
            }
        )
        client = build_dialogue_client(profile, models, timeout=float(timeout_seconds))
        if client is None:
            raise ABError("profile factory returned no dialogue client")
        options = _generation_options(client)
        _validate_thinking_contract(profile, client, options)
        artifact["generation_options"] = options

        try:
            model_ids = list(client.list_model_ids())
        except Exception as exc:
            raise ABError(f"model identity query failed: {exc}") from exc
        artifact["server_model_ids"] = model_ids
        if models.dialogue not in model_ids:
            raise ABError(
                f"dialogue model identity mismatch: expected {models.dialogue!r}, got {model_ids!r}"
            )

        for scenario in SCENARIOS:
            messages = _build_messages(scenario)
            current_prompt_hash = prompt_hash(messages)
            if artifact["prompt_hash"] is None:
                artifact["prompt_hash"] = current_prompt_hash
            nonstream = _measure_nonstream(client, messages, max_tokens=max_tokens)
            stream = _measure_stream(client, messages)
            failures = _leakage_failures(nonstream["content"], scenario.scenario_id, "nonstream")
            failures.extend(_leakage_failures(stream["content"], scenario.scenario_id, "stream"))
            artifact["leakage_failures"].extend(failures)
            artifact["scenario_results"].append(
                {
                    "scenario_id": scenario.scenario_id,
                    "title": scenario.title,
                    "prompt_hash": current_prompt_hash,
                    "nonstream": {
                        "content": nonstream["content"],
                        "output_chars": len(nonstream["content"]),
                        "total_latency_ms": nonstream["total_latency_ms"],
                    },
                    "stream": {
                        "content": stream["content"],
                        "output_chars": len(stream["content"]),
                        "client_ttft_ms": stream["client_ttft_ms"],
                        "client_total_latency_ms": stream["client_total_latency_ms"],
                    },
                }
            )

        if artifact["leakage_failures"]:
            raise ABError("participant-visible thinking/control leakage detected")
        artifact["status"] = "PASS"
    except ABError as exc:
        artifact["status"] = "FAIL"
        artifact["errors"].append(str(exc))
    except Exception as exc:  # Preserve partial evidence for unexpected failures.
        artifact["status"] = "FAIL"
        artifact["errors"].append(f"UNEXPECTED_AB_ERROR: {exc}")
    finally:
        _write_json(directory / "run.json", artifact)
    return 0 if artifact["status"] == "PASS" else 1


def _load_run(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ABError(f"could not read run artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ABError(f"run artifact {path} is not a JSON object")
    return payload


def compare_runs(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a paired, review-only comparison without a promotion verdict."""
    baseline = _load_run(baseline_path)
    candidate = _load_run(candidate_path)
    if baseline.get("profile") != BASELINE_PROFILE:
        raise ABError(f"baseline artifact must use {BASELINE_PROFILE!r}")
    if candidate.get("profile") != CANDIDATE_PROFILE:
        raise ABError(f"candidate artifact must use {CANDIDATE_PROFILE!r}")
    if baseline.get("scenario_matrix_hash") != candidate.get("scenario_matrix_hash"):
        raise ABError("baseline and candidate scenario matrices differ")
    baseline_rows = {row.get("scenario_id"): row for row in baseline.get("scenario_results", [])}
    candidate_rows = {row.get("scenario_id"): row for row in candidate.get("scenario_results", [])}
    if set(baseline_rows) != set(candidate_rows) or set(baseline_rows) != {s.scenario_id for s in SCENARIOS}:
        raise ABError("baseline and candidate scenario results are not aligned")

    paired: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        left = baseline_rows[scenario.scenario_id]
        right = candidate_rows[scenario.scenario_id]
        paired.append(
            {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "prompt_hash_equal": left.get("prompt_hash") == right.get("prompt_hash"),
                "baseline": {
                    "nonstream_latency_ms": left.get("nonstream", {}).get("total_latency_ms"),
                    "stream_ttft_ms": left.get("stream", {}).get("client_ttft_ms"),
                    "stream_total_latency_ms": left.get("stream", {}).get("client_total_latency_ms"),
                    "output_chars": left.get("stream", {}).get("output_chars"),
                    "content": left.get("stream", {}).get("content", ""),
                },
                "candidate": {
                    "nonstream_latency_ms": right.get("nonstream", {}).get("total_latency_ms"),
                    "stream_ttft_ms": right.get("stream", {}).get("client_ttft_ms"),
                    "stream_total_latency_ms": right.get("stream", {}).get("client_total_latency_ms"),
                    "output_chars": right.get("stream", {}).get("output_chars"),
                    "content": right.get("stream", {}).get("content", ""),
                },
            }
        )
    comparison = {
        "schema_version": 1,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "baseline_profile": baseline["profile"],
        "candidate_profile": candidate["profile"],
        "baseline_model": baseline.get("dialogue_model"),
        "candidate_model": candidate.get("dialogue_model"),
        "scenario_matrix_hash": baseline["scenario_matrix_hash"],
        "prompt_hashes_equal": all(row["prompt_hash_equal"] for row in paired),
        "paired_scenarios": paired,
        "baseline_status": baseline.get("status"),
        "candidate_status": candidate.get("status"),
        "status": (
            "READY_FOR_HUMAN_REVIEW"
            if baseline.get("status") == "PASS" and candidate.get("status") == "PASS"
            else "INCOMPLETE"
        ),
        "latency_thresholds": "NOT DEFINED",
        "quality_verdict": "NOT SCORED",
        "promotion_status": "NOT APPROVED",
        "human_review_required": True,
        "human_review_rubric": {
            dimension: {"baseline_rating": None, "candidate_rating": None, "notes": ""}
            for dimension in REVIEW_DIMENSIONS
        },
        "real_hardware_status": "NOT RUN",
        "warnings": [
            "This comparison is descriptive and does not select or promote a model.",
            "Run each profile against the same hardware and service configuration before human review.",
        ],
    }
    if output_path is not None:
        _write_json(Path(output_path), comparison)
    return comparison


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile-owned Qwen dialogue A/B harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one profile against the fixed synthetic matrix")
    run.add_argument("--profile", required=True, choices=sorted(SUPPORTED_PROFILES))
    run.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    run.add_argument("--timeout-seconds", type=float, default=120.0)
    run.add_argument("--max-tokens", type=int, default=128)
    compare = subparsers.add_parser("compare", help="compare two previously saved profile runs")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return run_profile(
                args.profile,
                output_root=args.output_root,
                timeout_seconds=args.timeout_seconds,
                max_tokens=args.max_tokens,
            )
        comparison = compare_runs(args.baseline, args.candidate, output_path=args.output)
        print(comparison["status"])
        return 0 if comparison["status"] == "READY_FOR_HUMAN_REVIEW" else 1
    except ABError as exc:
        print(f"A/B harness failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
