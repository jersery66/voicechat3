#!/usr/bin/env python
"""One-off diagnostic: why did every graded scale window miss on the holdout?

Re-runs ONLY the five graded scale-window trajectories (HOLD-01/02/05/06/10)
with the frozen configuration (Tacit-1.7B inprocess, zh template,
structured_history) and records, per turn, everything the decision chain saw:

* the Tacit four-way intervention distribution and confidence,
* the conditional scale-choice answer and its resolved target,
* the validator verdict and reason,
* the policy action and reason, the offer ledger state, the final execution.

The trajectories are synthetic benchmark material (scripts/benchmarks/cases/),
not real interviews; no participant identity exists in them or in the output.

Usage:
    python scripts/benchmarks/diagnose_scale_misses.py \
        --model-path D:/models/tacit/Tacit-1.7B \
        --out test_output/timing/scale_miss_diagnosis.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from decision.timing_eval import TrajectoryRunner  # noqa: E402
from decision.validator import DecisionValidator  # noqa: E402

CASES = PROJECT_ROOT / "scripts" / "benchmarks" / "cases" / "interview_trajectories_holdout.jsonl"
TARGET_IDS = ("HOLD-01", "HOLD-02", "HOLD-05", "HOLD-06", "HOLD-10")


class _CaptureProvider:
    """Wrap a provider and remember the request and raw decision per turn."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_request: Any = None
        self.last_raw: Any = None

    def decide(self, request: Any) -> Any:
        raw = self._inner.decide(request)
        self.last_request = request
        self.last_raw = raw
        return raw

    def __getattr__(self, name: str) -> Any:  # delegate the rest
        return getattr(self._inner, name)


class _CaptureValidator:
    """Wrap the validator and remember the verdict per turn."""

    def __init__(self, inner: DecisionValidator) -> None:
        self._inner = inner
        self.last_verdict: Any = None

    def validate(self, raw: Any, request: Any) -> Any:
        verdict = self._inner.validate(raw, request)
        self.last_verdict = verdict
        return verdict

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _DiagnosticRunner(TrajectoryRunner):
    """Attach the captured model/validator internals to each turn row."""

    def _run_turn(self, index, script, trajectory, world, ledger, state, scores):
        row = super()._run_turn(index, script, trajectory, world, ledger, state, scores)
        provider = self.provider
        raw = getattr(provider, "last_raw", None)
        request = getattr(provider, "last_request", None)
        verdict = self.validator.last_verdict
        row["diagnostic"] = {
            "utterance": str(script.get("text") or ""),
            "intervention_probs": dict(getattr(raw, "probabilities", None) or {}),
            "confidence": getattr(raw, "confidence", None),
            "intervention_raw": getattr(raw, "intervention", None),
            "scale_choice_answer": getattr(raw, "scale_choice", None),
            "scale_choice_probs": dict(
                (getattr(raw, "per_question", None) or {}).get("scale_choice", {})
            ),
            "offer_target": getattr(raw, "offer_target", None),
            "scale_candidates": [
                getattr(candidate, "name", str(candidate))
                for candidate in (request.scale_candidates() if request else ())
            ],
            "offer_pending": (
                {"kind": str(request.offer_state.pending.kind), "id": request.offer_state.pending.offer_id}
                if request is not None and request.offer_state.pending is not None
                else None
            ),
            "declined_scales": (
                sorted(request.offer_state.declined_scales()) if request is not None else []
            ),
            "completed_scales": sorted(getattr(request, "completed_scales", None) or []),
            "active_scale": getattr(request, "active_scale", None),
            "risk_marker": getattr(request, "risk_marker", None),
            "risk_in_session": getattr(request, "risk_in_session", None),
            "current_round": getattr(request, "current_round", None),
            "symptom_summary": getattr(request, "symptom_summary", ""),
            "recent_history_chars": len(getattr(request, "recent_history", "") or ""),
            "validator_accepted": bool(verdict.accepted) if verdict is not None else None,
            "validator_reason": verdict.reason if verdict is not None else None,
            "verdict_abstained": bool(verdict.abstained) if verdict is not None else None,
        }
        return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--template", default="zh", choices=("en", "zh"))
    parser.add_argument("--history-mode", default="structured_history")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in CASES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    targets = [row for row in rows if row.get("id") in TARGET_IDS]
    assert len(targets) == len(TARGET_IDS), f"missing trajectories: {TARGET_IDS}"

    from decision.providers.tacit import TacitDecisionProvider

    provider = TacitDecisionProvider(
        transport="inprocess",
        model=args.model_path,
        allow_model_download=False,
        template=args.template,
    )
    captured = _CaptureProvider(provider)
    validator = _CaptureValidator(DecisionValidator())

    runner = _DiagnosticRunner(
        provider=captured,
        validator=validator,
        history_mode=args.history_mode,
    )
    out = {
        "config": {
            "model": args.model_path,
            "template": args.template,
            "history_mode": args.history_mode,
            "provider": "tacit inprocess",
        },
        "trajectories": [runner.run(row) for row in targets],
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
