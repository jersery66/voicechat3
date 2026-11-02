"""Benchmark metrics and harness: numbers must be right before they are trusted."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH_PATH = PROJECT_ROOT / "scripts" / "benchmarks" / "decision_benchmark.py"
CASES_PATH = PROJECT_ROOT / "scripts" / "benchmarks" / "cases" / "decision_cases_synthetic.jsonl"


def _load_bench():
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "decision"))
    sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("decision_benchmark", BENCH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bench():
    return _load_bench()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_accuracy(bench):
    assert bench.accuracy(["a", "b"], ["a", "b"]) == 1.0
    assert bench.accuracy(["a", "b"], ["b", "a"]) == 0.0
    assert bench.accuracy(["a", "a"], ["a", "b"]) == 0.5
    assert bench.accuracy([], []) == 0.0


def test_macro_f1_perfect_and_absent_labels(bench):
    assert bench.macro_f1(["a", "b", "a"], ["a", "b", "a"], ["a", "b", "c"]) == pytest.approx(1.0)
    # 'c' never appears in gold or prediction: it must not drag the mean down
    assert bench.macro_f1(["a"], ["a"], ["a", "c"]) == pytest.approx(1.0)


def test_macro_f1_penalises_a_constant_prediction(bench):
    gold = ["a", "b", "b", "a"]
    pred = ["a", "a", "a", "a"]
    value = bench.macro_f1(gold, pred, ["a", "b"])
    assert 0.0 < value < 1.0


def test_confusion_matrix_counts_every_pair(bench):
    matrix = bench.confusion(["a", "b"], ["a", "a"], ["a", "b"])
    assert matrix["a"]["a"] == 1
    assert matrix["b"]["a"] == 1
    assert matrix["a"]["b"] == 0


def test_brier_is_zero_for_a_confident_correct_prediction(bench):
    probs = [{"a": 1.0, "b": 0.0}]
    assert bench.brier_multiclass(["a"], probs, ["a", "b"]) == 0.0


def test_brier_penalises_confident_errors(bench):
    wrong = bench.brier_multiclass(["a"], [{"a": 0.0, "b": 1.0}], ["a", "b"])
    hedged = bench.brier_multiclass(["a"], [{"a": 0.5, "b": 0.5}], ["a", "b"])
    assert wrong > hedged > 0.0


def test_ece_is_zero_when_confidence_matches_accuracy(bench):
    gold = ["a", "a", "b", "b"]
    pred = ["a", "a", "b", "b"]
    probs = [{"a": 1.0, "b": 0.0}] * 2 + [{"a": 0.0, "b": 1.0}] * 2
    assert bench.expected_calibration_error(gold, pred, probs) == pytest.approx(0.0, abs=1e-9)


def test_ece_grows_with_overconfidence(bench):
    gold = ["a", "b"]
    pred = ["a", "a"]
    probs = [{"a": 0.99, "b": 0.01}, {"a": 0.99, "b": 0.01}]
    assert bench.expected_calibration_error(gold, pred, probs) > 0.4


def test_percentile_handles_empty_and_single(bench):
    assert bench.percentile([], 0.5) == 0.0
    assert bench.percentile([7.0], 0.95) == 7.0
    assert bench.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 3.0  # nearest-rank, banker rounding


# ---------------------------------------------------------------------------
# Case file
# ---------------------------------------------------------------------------


def test_shipped_case_file_is_well_formed(bench):
    cases = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) >= 20
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    actions = {case["expected_action"] for case in cases}
    assert actions == {"chat", "start_scale", "recommend_relaxation", "recommend_game", "end_session"}
    for case in cases:
        assert case["note"] == "synthetic"
        assert case["text"].strip()
        assert case["expected_clarify"] in {"no", "yes"}


def test_expected_fields_match_the_question_names(bench):
    from decision.providers.base import DECISION_QUESTIONS

    names = {spec.name for spec in DECISION_QUESTIONS}
    assert set(bench.EXPECTED_FIELDS) == names


# ---------------------------------------------------------------------------
# Harness over a stub service
# ---------------------------------------------------------------------------


class FirstOptionEngine:
    """Always answers with the first option, so accuracy is predictable."""

    def decide_one(self, state, *, name, question, options):
        return {
            "name": name,
            "answer": options[0],
            "probs": {option: (1.0 if index == 0 else 0.0) for index, option in enumerate(options)},
            "route": "one_forward",
            "latency_ms": 1.0,
        }

    def decide_many(self, items):
        return [
            self.decide_one(item["state"], name=item["name"], question=item["question"], options=item["options"])
            for item in items
        ]

    def health(self):
        return {"status": "ok", "model_path": "stub"}

    def stats(self):
        return {"vram": {"available": False}}


def _service():
    from decision.service.server import DecisionService

    service = DecisionService(FirstOptionEngine(), port=0)
    service.build()
    service.start()
    assert service.wait_ready(5.0)
    return service


def test_run_cases_grades_every_question(bench):
    cases = [
        {
            "id": "c1",
            "text": "synthetic one",
            "expected_intent": "chat",
            "expected_emotion": "calm",
            "expected_intervention": "continue_interview",
            "expected_complex_review": "no",
        },
        {
            "id": "c2",
            "text": "synthetic two",
            "expected_intent": "symptom",
            "expected_emotion": "sad",
            "expected_intervention": "offer_scale",
            "expected_complex_review": "no",
        },
    ]
    service = _service()
    try:
        result = bench.run_cases(bench.http_decider(service.url, 10.0), cases, order_rounds=0)
    finally:
        service.stop()

    assert result["cases"] == 2
    assert result["failed_cases"] == []
    for name, entry in result["per_question"].items():
        assert entry["graded"] == 2, name
        assert "accuracy" in entry and "macro_f1" in entry
    # "Continue interviewing" is the first intervention option and "chat" the
    # first intent label, so case 1 is correct and case 2 is not.
    assert result["per_question"]["intervention"]["accuracy"] == pytest.approx(0.5)
    assert result["per_question"]["intent"]["accuracy"] == pytest.approx(0.5)
    assert result["latency_ms"]["p50"] > 0


def test_order_sensitivity_is_measured(bench):
    cases = [
        {
            "id": "c1",
            "text": "synthetic",
            "expected_intervention": "continue_interview",
        }
    ]
    service = _service()
    try:
        result = bench.run_cases(bench.http_decider(service.url, 10.0), cases, order_rounds=2)
    finally:
        service.stop()
    # The stub is order-insensitive (it always takes position 0 from the options
    # it was given, and the server maps back by label), so measured flips are 0.
    entry = result["per_question"]["intervention"]
    assert "order_flip_rate" in entry
    assert 0.0 <= entry["order_flip_rate"] <= 1.0


def test_failed_case_is_reported_not_swallowed(bench, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("transport down")

    import decision.providers.http_transport as transport_module

    monkeypatch.setattr(transport_module.HttpDecisionTransport, "post", boom)
    result = bench.run_cases(bench.http_decider("http://127.0.0.1:1", 1.0), [{"id": "c1", "text": "x"}], order_rounds=0)
    assert result["failed_cases"]
    assert result["failed_cases"][0]["id"] == "c1"


def test_render_markdown_contains_the_tables(bench):
    report = {
        "label": "Tacit-1.7B",
        "case_source": "synthetic",
        "model": {"parameters": 1_700_000_000},
        "stats": {"vram": {"available": True, "allocated_peak_mib": 3400.0, "reserved_peak_mib": 3600.0, "total_mib": 32607}},
        "result": {
            "cases": 2,
            "failed_cases": [],
            "order_rounds": 0,
            "latency_ms": {"p50": 12.0, "p90": 13.0, "p95": 14.0, "p99": 15.0},
            "per_question": {
                "action": {
                    "graded": 2,
                    "accuracy": 0.5,
                    "macro_f1": 0.4,
                    "brier": 0.5,
                    "ece": 0.1,
                    "confusion": {"chat": {"chat": 1, "start_scale": 1}, "start_scale": {"chat": 0, "start_scale": 0}},
                }
            },
        },
    }
    text = bench.render_markdown(report)
    assert "# Decision benchmark — Tacit-1.7B" in text
    assert "| action | 2 | 0.5000 |" in text
    assert "torch allocated peak: 3400.0 MiB" in text
    assert "| gold \\ pred | chat | start_scale |" in text


def test_compare_prints_a_delta_table(bench, tmp_path, capsys):
    base = {
        "label": "Tacit-1.7B",
        "result": {
            "per_question": {"action": {"accuracy": 0.60, "macro_f1": 0.55}},
            "latency_ms": {"p50": 12.0, "p95": 14.0, "p99": 15.0},
        },
    }
    upgraded = {
        "label": "Tacit-2B",
        "result": {
            "per_question": {"action": {"accuracy": 0.70, "macro_f1": 0.66}},
            "latency_ms": {"p50": 20.0, "p95": 24.0, "p99": 25.0},
        },
    }
    paths = []
    for name, payload in (("a.json", base), ("b.json", upgraded)):
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(str(path))

    assert bench.compare(paths) == 0
    output = capsys.readouterr().out
    assert "| action | accuracy | 0.6000 | 0.7000 | +0.1000 |" in output
    assert "| latency_ms | p50 | 12.00 | 20.00 | +8.00 |" in output
