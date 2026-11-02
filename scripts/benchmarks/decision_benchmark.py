#!/usr/bin/env python
"""Decision-layer benchmark: compare checkpoints on the same case set.

Designed for the question this project actually has to answer -- "does the 2B
upgrade beat the 1.7B baseline on Chinese counselling turns?" -- rather than for
a leaderboard.  It therefore reports, per question:

* accuracy and macro-F1 (the label space is imbalanced, so accuracy alone lies),
* a confusion matrix (which confusions actually happen),
* Brier score and ECE for the ``action`` question (a decision component that is
  confidently wrong is worse than one that abstains),
* latency P50/P90/P95/P99,
* option-order sensitivity: how often the answer flips when the option list is
  presented in a different order.

Run it once per checkpoint and pass both artifacts to ``--compare`` to get the
side-by-side table.

    python scripts/benchmarks/decision_benchmark.py --model-path D:/models/Tacit-1.7B \
        --label Tacit-1.7B --out test_output/benchmarks
    python scripts/benchmarks/decision_benchmark.py --model-path D:/models/Tacit-2B \
        --label Tacit-2B --out test_output/benchmarks
    python scripts/benchmarks/decision_benchmark.py --compare \
        test_output/benchmarks/tacit-1.7b.json test_output/benchmarks/tacit-2b.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "decision"))

from decision.providers.base import DECISION_QUESTIONS  # noqa: E402
from decision.timing import normalize_intervention  # noqa: E402
from decision.providers.http_transport import HttpDecisionTransport  # noqa: E402
from service_process import (  # noqa: E402
    LocalDecisionService,
    ServiceStartError,
    free_port,
)

DEFAULT_CASES = PROJECT_ROOT / "scripts" / "benchmarks" / "cases" / "decision_cases_synthetic.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "test_output" / "benchmarks"

#: Which expected field in the case file grades which question.
#: Question name -> case field.  ``intervention`` is the primary graded
#: question; intent and emotion stay graded as auxiliary observations, and
#: ``complex_review`` is only recorded (the case set does not label it).
EXPECTED_FIELDS = {
    "intervention": "expected_intervention",
    "emotion": "expected_emotion",
    "intent": "expected_intent",
    "complex_review": "expected_complex_review",
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def accuracy(gold: Sequence[str], pred: Sequence[str]) -> float:
    if not gold:
        return 0.0
    return sum(1 for g, p in zip(gold, pred) if g == p) / len(gold)


def confusion(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, int]]:
    matrix = {label: {other: 0 for other in labels} for label in labels}
    for g, p in zip(gold, pred):
        row = matrix.get(g)
        if row is None:
            row = matrix.setdefault(g, {other: 0 for other in labels})
        row[p] = row.get(p, 0) + 1
    return matrix


def macro_f1(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> float:
    scores: list[float] = []
    for label in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, pred) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, pred) if g == label and p != label)
        if tp == 0 and (fp or fn):
            scores.append(0.0)
            continue
        if tp == 0 and fp == 0 and fn == 0:
            continue  # label absent from both gold and prediction
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return sum(scores) / len(scores) if scores else 0.0


def brier_multiclass(gold: Sequence[str], probs: Sequence[dict[str, float]], labels: Sequence[str]) -> float:
    """Mean squared error between the predicted distribution and one-hot truth."""
    if not gold:
        return 0.0
    total = 0.0
    for g, distribution in zip(gold, probs):
        for label in labels:
            target = 1.0 if label == g else 0.0
            total += (float(distribution.get(label, 0.0)) - target) ** 2
    return total / len(gold)


def expected_calibration_error(
    gold: Sequence[str], pred: Sequence[str], probs: Sequence[dict[str, float]], bins: int = 10
) -> float:
    if not gold:
        return 0.0
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for g, p, distribution in zip(gold, pred, probs):
        confidence = max((float(v) for v in distribution.values()), default=0.0)
        correct = 1.0 if g == p else 0.0
        index = min(bins - 1, max(0, int(confidence * bins)))
        buckets[index].append((confidence, correct))
    total = 0.0
    count = len(gold)
    for bucket in buckets:
        if not bucket:
            continue
        mean_conf = sum(c for c, _ in bucket) / len(bucket)
        mean_acc = sum(a for _, a in bucket) / len(bucket)
        total += (len(bucket) / count) * abs(mean_conf - mean_acc)
    return total


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[index], 2)


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------


def _argmax(distribution: dict[str, float]) -> str:
    best_label = ""
    best_value = -1.0
    for label, value in distribution.items():
        if value > best_value:
            best_label, best_value = label, value
    return best_label


def _rotate(options: Sequence[str], offset: int) -> list[str]:
    if not options:
        return []
    step = offset % len(options)
    return list(options[step:]) + list(options[:step])


def http_decider(endpoint: str, timeout: float):
    """Adapt a decision service endpoint to the ``decide(items) -> items`` shape."""

    transport = HttpDecisionTransport(endpoint, timeout=timeout)

    def decide(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = transport.post({"items": list(items)})
        results = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(results, list) or len(results) != len(items):
            raise RuntimeError("unexpected_batch_shape")
        return results

    return decide


def run_cases(
    decide,
    cases: Sequence[dict[str, Any]],
    *,
    order_rounds: int,
) -> dict[str, Any]:
    """Grade one decision backend.

    ``decide`` takes a list of request items and returns one result per item, so
    the same grading code serves the HTTP service, the official Tacit API and
    the experimental readout.  Keeping one grader is what makes their numbers
    comparable at all.
    """
    per_question: dict[str, dict[str, Any]] = {
        spec.name: {"gold": [], "pred": [], "probs": []} for spec in DECISION_QUESTIONS
    }
    latencies: list[float] = []
    order_flips = {spec.name: [0, 0] for spec in DECISION_QUESTIONS}
    failures: list[dict[str, Any]] = []

    for case in cases:
        state = str(case["text"])
        batch = [
            {"state": state, "name": spec.name, "question": spec.text, "options": list(spec.options)}
            for spec in DECISION_QUESTIONS
        ]
        started = time.perf_counter()
        try:
            items = decide(batch)
        except Exception as exc:
            failures.append({"id": case.get("id"), "error": f"{type(exc).__name__}: {exc}"})
            continue
        latency = (time.perf_counter() - started) * 1000.0
        latencies.append(latency)

        if not isinstance(items, list) or len(items) != len(batch):
            failures.append({"id": case.get("id"), "error": "unexpected_batch_shape"})
            continue

        for spec, item in zip(DECISION_QUESTIONS, items):
            distribution = item.get("probs") or item.get("probabilities") or {}
            answer = str(item.get("answer") or _argmax(distribution))
            if spec.name == "intervention":
                # Compare in label space: the wire answer is the option text
                # ("Offer a scale assessment"), the case set is labelled with
                # the decision vocabulary ("offer_scale").
                answer = normalize_intervention(answer) or answer
            slot = per_question[spec.name]
            expected_field = EXPECTED_FIELDS[spec.name]
            gold = case.get(expected_field)
            if isinstance(gold, str):
                slot["gold"].append(gold)
                slot["pred"].append(answer)
                slot["probs"].append({str(k): float(v) for k, v in distribution.items()})

        if order_rounds > 0:
            for spec in DECISION_QUESTIONS:
                base_options = list(spec.options)
                try:
                    base = decide(
                        [{"state": state, "name": spec.name, "question": spec.text, "options": base_options}]
                    )[0]
                except Exception:
                    continue
                base_answer = str(base.get("answer") or _argmax(base.get("probs") or {}))
                for offset in range(1, min(order_rounds, len(base_options)) + 1):
                    rotated = _rotate(base_options, offset)
                    try:
                        other = decide(
                            [{"state": state, "name": spec.name, "question": spec.text, "options": rotated}]
                        )[0]
                    except Exception:
                        continue
                    other_answer = str(other.get("answer") or _argmax(other.get("probs") or {}))
                    counter = order_flips[spec.name]
                    counter[1] += 1
                    if other_answer != base_answer:
                        counter[0] += 1

    report_per_question: dict[str, Any] = {}
    for spec in DECISION_QUESTIONS:
        slot = per_question[spec.name]
        gold, pred, probs = slot["gold"], slot["pred"], slot["probs"]
        entry: dict[str, Any] = {
            "graded": len(gold),
            "accuracy": round(accuracy(gold, pred), 4),
            "macro_f1": round(macro_f1(gold, pred, list(spec.options)), 4),
            "confusion": confusion(gold, pred, list(spec.options)),
        }
        if gold:
            entry["brier"] = round(brier_multiclass(gold, probs, list(spec.options)), 4)
            entry["ece"] = round(expected_calibration_error(gold, pred, probs), 4)
        flips, trials = order_flips[spec.name]
        if trials:
            entry["order_flip_rate"] = round(flips / trials, 4)
        report_per_question[spec.name] = entry

    return {
        "cases": len(cases),
        "failed_cases": failures,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p90": percentile(latencies, 0.90),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
        "per_question": report_per_question,
        "order_rounds": order_rounds,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    label = report.get("label") or "model"
    result = report["result"]
    lines = [
        f"# Decision benchmark — {label}",
        "",
        f"- cases: {result['cases']} (failed {len(result['failed_cases'])})",
        f"- case source: {report.get('case_source')}",
        f"- model: {report.get('model', {})}",
        f"- order sensitivity rounds: {result['order_rounds']}",
        "",
        "## Per question",
        "",
        "| question | graded | accuracy | macro-F1 | Brier | ECE | order-flip |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, entry in result["per_question"].items():
        lines.append(
            "| {name} | {graded} | {acc} | {f1} | {brier} | {ece} | {flip} |".format(
                name=name,
                graded=entry["graded"],
                acc=_fmt(entry["accuracy"]),
                f1=_fmt(entry["macro_f1"]),
                brier=_fmt(entry.get("brier", "-")),
                ece=_fmt(entry.get("ece", "-")),
                flip=_fmt(entry.get("order_flip_rate", "-")),
            )
        )
    latency = result["latency_ms"]
    lines += [
        "",
        "## Latency (ms, one batched 5-question call)",
        "",
        f"P50 {latency['p50']} · P90 {latency['p90']} · P95 {latency['p95']} · P99 {latency['p99']}",
        "",
        "## Action confusion",
        "",
    ]
    action = result["per_question"]["action"]["confusion"]
    labels = list(action)
    lines.append("| gold \\ pred | " + " | ".join(labels) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in labels) + " |")
    for gold_label in labels:
        row = action[gold_label]
        lines.append(
            f"| {gold_label} | " + " | ".join(str(row.get(pred, 0)) for pred in labels) + " |"
        )
    vram = (report.get("stats", {}).get("vram") or {})
    if vram.get("available"):
        lines += [
            "",
            "## Memory",
            "",
            f"- torch allocated peak: {vram.get('allocated_peak_mib')} MiB",
            f"- torch reserved peak: {vram.get('reserved_peak_mib')} MiB",
            f"- device total: {vram.get('total_mib')} MiB",
        ]
    return "\n".join(lines) + "\n"


def compare(paths: Sequence[str]) -> int:
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    labels = [r.get("label") or Path(p).stem for r, p in zip(reports, paths)]
    questions = list(reports[0]["result"]["per_question"])
    print("| question | metric | " + " | ".join(labels) + " | delta |")
    print("| --- | --- | " + " | ".join("---" for _ in labels) + " | --- |")
    for question in questions:
        for metric in ("accuracy", "macro_f1", "brier", "ece", "order_flip_rate"):
            values = []
            for report in reports:
                entry = report["result"]["per_question"].get(question, {})
                values.append(entry.get(metric))
            if all(v is None for v in values):
                continue
            cells = ["-" if v is None else f"{v:.4f}" for v in values]
            delta = ""
            if len(values) == 2 and None not in values:
                delta = f"{values[1] - values[0]:+.4f}"
            print(f"| {question} | {metric} | " + " | ".join(cells) + f" | {delta} |")
    for metric in ("p50", "p95", "p99"):
        cells = [f"{r['result']['latency_ms'][metric]:.2f}" for r in reports]
        delta = ""
        if len(reports) == 2:
            delta = f"{reports[1]['result']['latency_ms'][metric] - reports[0]['result']['latency_ms'][metric]:+.2f}"
        print(f"| latency_ms | {metric} | " + " | ".join(cells) + f" | {delta} |")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark decision checkpoints on a shared case set",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model-path", help="local checkpoint directory (spawns a service)")
    parser.add_argument("--endpoint", help="attach to a running service")
    parser.add_argument("--label", default=None, help="name used in the artifact and report")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--order-rounds", type=int, default=0,
                        help="option-order rotations per case (0 disables; costs N extra calls)")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--compare", nargs="+", metavar="JSON", help="compare existing artifacts")
    args = parser.parse_args(argv)

    if args.compare:
        return compare(args.compare)

    cases = []
    for line in Path(args.cases).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            cases.append(json.loads(text))

    service: Optional[LocalDecisionService] = None
    try:
        if args.endpoint:
            service = LocalDecisionService.attach(args.endpoint)
            health = service.health()
        else:
            if not args.model_path:
                parser.error("--model-path or --endpoint is required")
            service = LocalDecisionService.spawn(
                model_path=args.model_path,
                dtype=args.dtype,
                device=args.device,
                port=free_port(),
                project_root=str(PROJECT_ROOT),
            )
            health = service.wait_healthy(timeout=args.timeout)

        result = run_cases(
            http_decider(service.base_url, 30.0),
            cases,
            order_rounds=max(0, args.order_rounds),
        )
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "label": args.label or (Path(args.model_path).name if args.model_path else "external"),
            "case_file": str(args.cases),
            "case_source": "synthetic template utterances (no patient data)",
            "model": health.get("model") or {},
            "stats": service.stats(),
            "result": result,
        }
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = str(report["label"]).replace("/", "_").replace("\\", "_").lower()
        json_path = out_dir / f"{slug}.json"
        md_path = out_dir / f"{slug}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(render_markdown(report))
        print(f"artifact: {json_path}\nreport:   {md_path}")
        return 0
    except ServiceStartError as exc:
        print(f"service unavailable: {exc}", file=sys.stderr)
        return 4
    finally:
        if service is not None and service.spawned:
            service.stop()


if __name__ == "__main__":
    sys.exit(main())
