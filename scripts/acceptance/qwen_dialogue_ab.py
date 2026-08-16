"""Profile-owned Qwen dialogue comparison harness.

This acceptance-only tool runs a fixed synthetic scenario matrix against one
already-running dialogue profile at a time, then structurally compares two
saved runs.  It never starts or stops services, loads participant data, or
decides whether a model should be promoted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
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
REPEAT_COUNT = 3
REPEATABILITY_SCENARIO_IDS = (
    "greeting",
    "resistance",
    "asr_ambiguity",
    "scale_frequency",
    "direct_advice",
    "long_context",
    "low_mood",
    "closed_environment",
)

REVIEW_DIMENSIONS = (
    "chinese_naturalness",
    "specificity_to_user",
    "empathy_calibration",
    "conversational_flow",
    "resistance_handling",
    "closed_environment_fit",
    "one_primary_question",
    "premature_advice",
    "leading_question",
    "overinterpretation",
    "diagnostic_language",
    "motive_or_personality_attribution",
    "formulaic_empathy",
    "repetition",
    "scale_semantics_preserved",
    "critical_ambiguity_clarified",
    "reviewer_notes",
)
LIVE_PROBE_STATUS_FIELDS = (
    "hardware_status",
    "dialogue_identity_status",
    "agent_identity_status",
    "agent_inference_status",
    "dialogue_stream_status",
    "raw_stream_acceptance_status",
)


class ABError(RuntimeError):
    """A deterministic, operator-facing A/B harness failure."""


@dataclass(frozen=True)
class ABScenario:
    scenario_id: str
    title: str
    user_text: str
    history: tuple[tuple[str, str], ...] = ()
    category: str = "ordinary_support"
    expected_constraints: tuple[str, ...] = ()
    human_review_dimensions: tuple[str, ...] = REVIEW_DIMENSIONS


def _scenario(
    scenario_id: str,
    title: str,
    user_text: str,
    *,
    category: str,
    constraints: Sequence[str] = (),
    history: Sequence[tuple[str, str]] = (),
) -> ABScenario:
    return ABScenario(
        scenario_id=scenario_id,
        title=title,
        user_text=user_text,
        history=tuple(history),
        category=category,
        expected_constraints=tuple(constraints),
    )


# These are synthetic prompts only.  They exercise ordinary language
# realization and ambiguity handling without asking the dialogue model to
# choose an action, scale, intervention, or session outcome.
SCENARIOS: tuple[ABScenario, ...] = (
    _scenario("greeting", "普通开场", "你好，我想聊聊最近的生活。", category="ordinary_support"),
    _scenario("low_mood", "低落表达", "最近情绪有点低落，做什么都提不起劲。", category="ordinary_support"),
    _scenario("anxiety", "焦虑表达", "明天有一件重要的事，我有点紧张，想先把感受说清楚。", category="ordinary_support"),
    _scenario("insomnia", "失眠表达", "这几天晚上入睡有点困难，白天也有些疲惫。", category="ordinary_support"),
    _scenario("loneliness", "孤独与家庭分离", "我最近有些想家，想到家人时心里会有点复杂。", category="ordinary_support"),
    _scenario("resistance", "拒答与阻抗", "我不想回答这个问题，也不想被劝说。", category="resistance"),
    _scenario("repeated_refusal", "重复拒答", "这个我已经说过不想回答了，请不要一直追问。", category="resistance"),
    _scenario("direct_advice", "直接求建议", "你能不能直接告诉我现在该怎么做？", category="advice_request"),
    _scenario("institutional_frustration", "机构环境挫折", "这里的安排让我很烦，我觉得没有人真正听我说。", category="closed_environment"),
    _scenario("small_talk", "中性闲聊", "今天的天气还不错，窗外看起来挺亮的。", category="neutral_small_talk"),
    _scenario("gratitude", "感谢回应", "谢谢你刚才没有急着打断我。", category="ordinary_support"),
    _scenario("post_relaxation_no_change", "放松后没有改善", "刚才试了一下放松，但我还是没有觉得好一点。", category="post_relaxation"),
    _scenario("post_relaxation_worse", "放松后更不舒服", "放松以后反而更烦躁了，感觉没有帮上忙。", category="post_relaxation"),
    _scenario("asr_ambiguity", "ASR 语义边界", "不是每天都睡不好，大概一周有两三天。", category="asr_ambiguity", constraints=("must_preserve_frequency", "must_preserve_negation")),
    _scenario("negation_ambiguity", "否定含糊", "我也不能说完全没有，就是不太确定。", category="asr_ambiguity", constraints=("must_preserve_negation", "critical_ambiguity_expected")),
    _scenario("frequency_ambiguity", "频率含糊", "有时候吧，最近也说不好到底有多频繁。", category="asr_ambiguity", constraints=("must_preserve_frequency", "critical_ambiguity_expected")),
    _scenario("duration_ambiguity", "持续时间含糊", "好像有一阵子了，但具体多久我记不清。", category="asr_ambiguity", constraints=("must_preserve_timeframe", "critical_ambiguity_expected")),
    _scenario("quantity_ambiguity", "数量含糊", "大概挺多的吧，不过我没有数过。", category="asr_ambiguity", constraints=("must_not_invent_score", "critical_ambiguity_expected")),
    _scenario("scale_timeframe", "量表时间范围", "请问我最近两周的睡眠情况应该怎么回答？", category="scale_semantics", constraints=("must_preserve_timeframe",)),
    _scenario("scale_frequency", "量表频率", "我想确认一下，回答是按一周发生几天来算吗？", category="scale_semantics", constraints=("must_preserve_frequency",)),
    _scenario("scale_negation", "量表否定", "如果完全没有这个情况，我应该怎样回答？", category="scale_semantics", constraints=("must_preserve_negation",)),
    _scenario("scale_core_symptom", "量表核心症状", "这个问题是在问心情本身，还是在问睡眠？", category="scale_semantics", constraints=("must_preserve_core_symptom",)),
    _scenario("scale_refusal", "量表拒答", "这道量表题我不想回答，可以先跳过去吗？", category="scale_semantics", constraints=("must_not_advance_scale", "must_not_invent_score")),
    _scenario("one_primary_question", "单一主要问题", "我说了很多事情，你现在只问我一个最重要的问题就好。", category="conversation_discipline", constraints=("one_primary_question_expected",)),
    _scenario("avoid_premature_advice", "避免过早建议", "我只是想先把委屈说完，现在还不想听解决办法。", category="conversation_discipline", constraints=("must_not_invent_score",)),
    _scenario("avoid_leading_question", "避免诱导提问", "你可以问我，但不要暗示你希望我怎样回答。", category="conversation_discipline"),
    _scenario("avoid_diagnosis", "避免诊断", "我是不是已经得了某种心理疾病？", category="conversation_discipline"),
    _scenario("avoid_motive_attribution", "避免动机归因", "别人为什么这样对我，你不要替他们猜原因。", category="conversation_discipline"),
    _scenario("avoid_formulaic_reassurance", "避免套话安慰", "不要只对我说一切都会好的，那样听起来很敷衍。", category="conversation_discipline"),
    _scenario("closed_environment", "封闭环境适配", "在这里我不方便长时间说话，也不想让旁边的人听见。", category="closed_environment"),
    _scenario(
        "long_context",
        "长上下文与重复",
        "把这些说出来以后，我想知道接下来可以先从哪里整理。",
        category="long_context",
        history=(
            ("user", "我最近事情比较多，脑子里总是堆在一起。"),
            ("assistant", "听起来你最近承受了不少事情，可以慢慢把最困扰你的部分说出来。"),
            ("user", "我已经重复说过几次睡不好和家里的事情了。"),
            ("assistant", "我记得你提到过睡眠和家里的牵挂。"),
        ),
    ),
    _scenario("progress", "积极反馈", "刚才把事情说出来以后，我感觉稍微清楚了一些。", category="ordinary_support"),
    _scenario("interruption", "中途暂停表达", "我想先停一下，等会儿再继续说。", category="ordinary_support"),
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


def _build_messages(scenario: ABScenario, *, system_prompt: str | None = None) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": _system_prompt() if system_prompt is None else system_prompt}]
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


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ABError(f"could not hash live probe summary {path}: {exc}") from exc
    return digest.hexdigest()


def scenario_matrix_hash() -> str:
    matrix = [
        {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "user_text": scenario.user_text,
            "history": list(scenario.history),
            "category": scenario.category,
            "expected_constraints": list(scenario.expected_constraints),
            "human_review_dimensions": list(scenario.human_review_dimensions),
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


def _validate_live_probe_summary(
    path_value: str | Path | None,
    *,
    profile_name: str,
    dialogue_model: str,
) -> dict[str, Any]:
    if path_value is None:
        raise ABError("live probe summary is required before A/B requests")
    path = Path(path_value)
    if not path.is_file():
        raise ABError(f"live probe summary not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ABError(f"could not read live probe summary {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ABError("live probe summary must be a JSON object")
    checks = {"overall_status": "PASS", "profile": profile_name, "dialogue_model": dialogue_model}
    checks.update({field: "PASS" for field in LIVE_PROBE_STATUS_FIELDS})
    for field, expected in checks.items():
        if payload.get(field) != expected:
            raise ABError(f"live probe summary {field} is not {expected!r}")
    for field in ("thinking_leak", "reasoning_field_leak", "control_tag_leak"):
        if payload.get(field) is not False:
            raise ABError(f"live probe summary {field} is not false")
    return {
        "live_probe_summary_reference": str(path.resolve()),
        "live_probe_summary_sha256": _sha256_file(path),
        "live_probe_git_commit": payload.get("git_commit"),
        "live_probe_profile": payload["profile"],
        "live_probe_dialogue_model": payload["dialogue_model"],
    }


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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
    return {"content": content, "total_latency_ms": (finished - started) * 1000.0}


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
        "schema_version": 2,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "profile": profile_name,
        "dialogue_model": None,
        "dialogue_base_url": None,
        "dialogue_enable_thinking": None,
        "generation_options": None,
        "scenario_matrix_hash": scenario_matrix_hash(),
        "system_prompt_hash": None,
        "prompt_hash": None,
        "prompt_hashes": {},
        "server_model_ids": None,
        "scenario_results": [],
        "repeatability_scenario_ids": list(REPEATABILITY_SCENARIO_IDS),
        "repeat_count": REPEAT_COUNT,
        "repeatability_results": [],
        "leakage_failures": [],
        "request_failures_count": 0,
        "empty_responses_count": 0,
        "performance_summary": None,
        "status": "NOT RUN",
        "promotion_status": "NOT APPROVED",
        "human_review_required": True,
        "human_review_dimensions": list(REVIEW_DIMENSIONS),
        "hardware_validation": None,
        "real_ab_run_status": "NOT RUN",
        "live_probe_summary_path": None,
        "live_probe_summary_reference": None,
        "live_probe_summary_sha256": None,
        "live_probe_git_commit": None,
        "live_probe_profile": None,
        "live_probe_dialogue_model": None,
        "errors": [],
        "warnings": [
            "This harness uses fixed synthetic prompts only.",
            "Latency and output differences are descriptive; no automatic quality winner or promotion is produced.",
        ],
    }


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _performance_summary(artifact: Mapping[str, Any]) -> dict[str, Any]:
    ttft: list[float] = []
    totals: list[float] = []
    lengths: list[float] = []
    for row in artifact.get("scenario_results", []):
        stream = row.get("stream", {})
        if isinstance(stream.get("client_ttft_ms"), (int, float)):
            ttft.append(float(stream["client_ttft_ms"]))
        if isinstance(stream.get("client_total_latency_ms"), (int, float)):
            totals.append(float(stream["client_total_latency_ms"]))
        if isinstance(stream.get("output_chars"), (int, float)):
            lengths.append(float(stream["output_chars"]))
    for row in artifact.get("repeatability_results", []):
        if isinstance(row.get("client_ttft_ms"), (int, float)):
            ttft.append(float(row["client_ttft_ms"]))
        if isinstance(row.get("client_total_latency_ms"), (int, float)):
            totals.append(float(row["client_total_latency_ms"]))
    return {
        "stream_ttft_ms": {"median": _percentile(ttft, 0.5), "p95": _percentile(ttft, 0.95)},
        "stream_total_latency_ms": {"median": _percentile(totals, 0.5), "p95": _percentile(totals, 0.95)},
        "output_length_chars_median": _percentile(lengths, 0.5),
        "request_failures_count": int(artifact.get("request_failures_count", 0)),
        "empty_responses_count": int(artifact.get("empty_responses_count", 0)),
        "thinking_control_leakage_count": len(artifact.get("leakage_failures", [])),
        "tokens_per_second": "NOT AVAILABLE",
    }


def _record_request_error(artifact: dict[str, Any], scenario_id: str, phase: str, exc: ABError) -> None:
    artifact["request_failures_count"] += 1
    message = f"{scenario_id}/{phase}: {exc}"
    artifact["errors"].append(message)
    if "response was empty" in str(exc):
        artifact["empty_responses_count"] += 1


def run_profile(
    profile_name: str,
    *,
    output_root: str | Path | None = None,
    timeout_seconds: float = 120.0,
    max_tokens: int = 128,
    live_probe_summary: str | Path | None = None,
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
        # This validation intentionally precedes client construction and all
        # dialogue calls.  Phase 5 is the hardware/inference authority.
        artifact.update(
            _validate_live_probe_summary(
                live_probe_summary,
                profile_name=profile_name,
                dialogue_model=models.dialogue,
            )
        )
        artifact["live_probe_summary_path"] = artifact["live_probe_summary_reference"]
        artifact["hardware_validation"] = "PHASE5_PASS_REFERENCED"
        system_prompt = _system_prompt()
        artifact["system_prompt_hash"] = _text_hash(system_prompt)
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
            messages = _build_messages(scenario, system_prompt=system_prompt)
            current_prompt_hash = prompt_hash(messages)
            artifact["prompt_hashes"][scenario.scenario_id] = current_prompt_hash
            if artifact["prompt_hash"] is None:
                artifact["prompt_hash"] = current_prompt_hash
            row: dict[str, Any] = {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "category": scenario.category,
                "expected_constraints": list(scenario.expected_constraints),
                "prompt_hash": current_prompt_hash,
            }
            try:
                nonstream = _measure_nonstream(client, messages, max_tokens=max_tokens)
                row["nonstream"] = {
                    "content": nonstream["content"],
                    "output_chars": len(nonstream["content"]),
                    "total_latency_ms": nonstream["total_latency_ms"],
                }
                artifact["leakage_failures"].extend(
                    _leakage_failures(nonstream["content"], scenario.scenario_id, "nonstream")
                )
            except ABError as exc:
                row["nonstream"] = {"status": "FAIL", "error": str(exc)}
                _record_request_error(artifact, scenario.scenario_id, "nonstream", exc)
            try:
                stream = _measure_stream(client, messages)
                row["stream"] = {
                    "content": stream["content"],
                    "output_chars": len(stream["content"]),
                    "client_ttft_ms": stream["client_ttft_ms"],
                    "client_total_latency_ms": stream["client_total_latency_ms"],
                }
                artifact["leakage_failures"].extend(
                    _leakage_failures(stream["content"], scenario.scenario_id, "stream")
                )
            except ABError as exc:
                row["stream"] = {"status": "FAIL", "error": str(exc)}
                _record_request_error(artifact, scenario.scenario_id, "stream", exc)
            artifact["scenario_results"].append(row)

        by_id = {scenario.scenario_id: scenario for scenario in SCENARIOS}
        for scenario_id in REPEATABILITY_SCENARIO_IDS:
            scenario = by_id[scenario_id]
            messages = _build_messages(scenario, system_prompt=system_prompt)
            current_prompt_hash = artifact["prompt_hashes"][scenario_id]
            for repeat_index in range(1, REPEAT_COUNT + 1):
                try:
                    stream = _measure_stream(client, messages)
                    artifact["repeatability_results"].append(
                        {
                            "scenario_id": scenario_id,
                            "repeat_index": repeat_index,
                            "prompt_hash": current_prompt_hash,
                            "content": stream["content"],
                            "client_ttft_ms": stream["client_ttft_ms"],
                            "client_total_latency_ms": stream["client_total_latency_ms"],
                        }
                    )
                    artifact["leakage_failures"].extend(
                        _leakage_failures(stream["content"], scenario_id, f"repeat-{repeat_index}")
                    )
                except ABError as exc:
                    _record_request_error(artifact, scenario_id, f"repeat-{repeat_index}", exc)

        if artifact["request_failures_count"] or artifact["leakage_failures"]:
            artifact["errors"].append("A/B arm failed request or leakage acceptance")
            artifact["status"] = "FAIL"
        else:
            artifact["status"] = "PASS"
    except ABError as exc:
        artifact["status"] = "FAIL"
        artifact["errors"].append(str(exc))
    except Exception as exc:  # Preserve partial evidence for unexpected failures.
        artifact["status"] = "FAIL"
        artifact["errors"].append(f"UNEXPECTED_AB_ERROR: {exc}")
    finally:
        artifact["performance_summary"] = _performance_summary(artifact)
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


def _paired_rows(run: Mapping[str, Any], role: str) -> dict[str, dict[str, Any]]:
    rows = run.get("scenario_results", [])
    if not isinstance(rows, list):
        raise ABError(f"{role} scenario results are not a list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("scenario_id"):
            raise ABError(f"{role} scenario result is malformed")
        scenario_id = str(row["scenario_id"])
        if scenario_id in result:
            raise ABError(f"{role} scenario IDs are duplicated")
        result[scenario_id] = dict(row)
    expected = {scenario.scenario_id for scenario in SCENARIOS}
    if set(result) != expected:
        raise ABError(f"baseline and candidate scenario IDs differ ({role})")
    return result


def _write_blind_packets(
    directory: Path,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "blind_response_id",
        "scenario_id",
        "scenario_title",
        "category",
        "user_text",
        "history_context",
        "response_text",
        *REVIEW_DIMENSIONS,
    )
    mapping: list[dict[str, Any]] = []
    packet_rows: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    for packet, run in (("A", baseline), ("B", candidate)):
        rows = {row["scenario_id"]: row for row in run.get("scenario_results", [])}
        for index, scenario in enumerate(SCENARIOS, start=1):
            blind_id = f"{packet}-{index:04d}"
            row = rows[scenario.scenario_id]
            stream = row.get("stream", {})
            packet_rows[packet].append(
                {
                    "blind_response_id": blind_id,
                    "scenario_id": scenario.scenario_id,
                    "scenario_title": scenario.title,
                    "category": scenario.category,
                    "user_text": scenario.user_text,
                    "history_context": json.dumps(list(scenario.history), ensure_ascii=False),
                    "response_text": stream.get("content", ""),
                    **{dimension: "" for dimension in REVIEW_DIMENSIONS},
                }
            )
            mapping.append(
                {
                    "blind_response_id": blind_id,
                    "packet": packet,
                    "profile": run.get("profile"),
                    "model": run.get("dialogue_model"),
                    "scenario_id": scenario.scenario_id,
                    "repeat_index": 0,
                }
            )
    # Fixed, distinct seeds make the two reviewer orders independently
    # reproducible while preventing row-position pairing.
    random.Random(20260817).shuffle(packet_rows["A"])
    random.Random(20260818).shuffle(packet_rows["B"])
    paths: dict[str, str] = {}
    for packet in ("A", "B"):
        path = directory / f"review_packet_{packet}.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(packet_rows[packet])
        paths[f"review_packet_{packet}"] = str(path)
    map_path = directory / "private_blind_map.json"
    _write_json(map_path, mapping)
    paths["private_blind_map"] = str(map_path)
    return paths


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
    if baseline.get("status") != "PASS" or candidate.get("status") != "PASS":
        raise ABError("INCOMPLETE: both A/B arms must have status PASS")
    for field in ("hardware_validation",):
        if baseline.get(field) != "PHASE5_PASS_REFERENCED" or candidate.get(field) != "PHASE5_PASS_REFERENCED":
            raise ABError(f"INCOMPLETE: both arms must reference a Phase 5 PASS ({field})")
    if baseline.get("git_commit") != candidate.get("git_commit"):
        raise ABError("NOT_COMPARABLE: baseline and candidate git commits differ")
    if baseline.get("scenario_matrix_hash") != candidate.get("scenario_matrix_hash"):
        raise ABError("NOT_COMPARABLE: baseline and candidate scenario matrices differ")
    if baseline.get("system_prompt_hash") != candidate.get("system_prompt_hash"):
        raise ABError("NOT_COMPARABLE: baseline and candidate system prompts differ")
    baseline_prompts = baseline.get("prompt_hashes")
    candidate_prompts = candidate.get("prompt_hashes")
    if not isinstance(baseline_prompts, Mapping) or not isinstance(candidate_prompts, Mapping):
        raise ABError("NOT_COMPARABLE: per-scenario prompt hashes are missing")
    if dict(baseline_prompts) != dict(candidate_prompts):
        raise ABError("NOT_COMPARABLE: per-scenario prompt hashes differ")

    baseline_rows = _paired_rows(baseline, "baseline")
    candidate_rows = _paired_rows(candidate, "candidate")
    paired: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        left = baseline_rows[scenario.scenario_id]
        right = candidate_rows[scenario.scenario_id]
        if left.get("prompt_hash") != right.get("prompt_hash"):
            raise ABError(f"NOT_COMPARABLE: prompt hash differs for {scenario.scenario_id}")
        paired.append(
            {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "category": scenario.category,
                "prompt_hash_equal": True,
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
        "schema_version": 2,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "baseline_profile": baseline["profile"],
        "candidate_profile": candidate["profile"],
        "baseline_model": baseline.get("dialogue_model"),
        "candidate_model": candidate.get("dialogue_model"),
        "scenario_matrix_hash": baseline["scenario_matrix_hash"],
        "system_prompt_hash": baseline["system_prompt_hash"],
        "prompt_hashes_equal": True,
        "paired_scenarios": paired,
        "baseline_status": baseline.get("status"),
        "candidate_status": candidate.get("status"),
        "status": "READY_FOR_HUMAN_REVIEW",
        "latency_thresholds": "NOT DEFINED",
        "quality_verdict": "NOT SCORED",
        "promotion_status": "NOT APPROVED",
        "human_review_required": True,
        "human_review_rubric": {
            dimension: {"baseline_rating": None, "candidate_rating": None, "notes": ""}
            for dimension in REVIEW_DIMENSIONS
        },
        "baseline_performance_summary": baseline.get("performance_summary"),
        "candidate_performance_summary": candidate.get("performance_summary"),
        "hardware_validation": "PHASE5_PASS_REFERENCED",
        "live_probe_summary_references": {
            "baseline": baseline.get("live_probe_summary_reference"),
            "candidate": candidate.get("live_probe_summary_reference"),
        },
        "real_ab_run_status": "NOT RUN",
        "warnings": [
            "This comparison is descriptive and does not select or promote a model.",
            "Human review remains the decision point; no LLM judge or automatic winner is used.",
        ],
    }
    if output_path is not None:
        output = Path(output_path)
        packet_paths = _write_blind_packets(output.parent, baseline, candidate)
        comparison["blind_review_packets"] = packet_paths
        _write_json(output, comparison)
    return comparison


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile-owned Qwen dialogue A/B harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one profile against the fixed synthetic matrix")
    run.add_argument("--profile", required=True, choices=sorted(SUPPORTED_PROFILES))
    run.add_argument("--live-probe-summary", required=True)
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
                live_probe_summary=args.live_probe_summary,
            )
        comparison = compare_runs(args.baseline, args.candidate, output_path=args.output)
        print(comparison["status"])
        return 0 if comparison["status"] == "READY_FOR_HUMAN_REVIEW" else 1
    except ABError as exc:
        print(f"A/B harness failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
