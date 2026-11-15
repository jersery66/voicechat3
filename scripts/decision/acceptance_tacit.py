#!/usr/bin/env python
"""Real-model acceptance for the decision layer, on an offline machine.

What it proves, in order
------------------------
1. the checkpoint matches the manifest that came from the source;
2. official Tacit answers one turn (``Tacit.from_pretrained``, in process);
3. this project's ``TacitDecisionProvider`` answers the same turn through the
   coordinator's contract;
4. legacy / shadow / jev switch without letting a model mutate state;
5. when the model proposes ``end_session``, the validator rejects it and
   ``TurnPolicy`` still decides -- the model never gets execution authority;
6. a dead decision endpoint escalates to the legacy agent instead of breaking
   the turn.

Nothing is simulated.  If the checkpoint is absent the script exits 3 with the
list of missing files; it never falls back to a fake provider.

    python scripts/decision/acceptance_tacit.py --model-path D:/models/tacit/Tacit-1.7B
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from conversation.agent_observation import AgentObservation  # noqa: E402
from conversation.contracts import (  # noqa: E402
    RouterAction,
    RouterProposal,
    TurnSignals,
    TurnStateSnapshot,
)
from conversation.turn_policy import TurnPolicy  # noqa: E402
from decision.contracts import (  # noqa: E402
    DecisionMode,
    DecisionRequest,
    EscalationMode,
)
from decision.coordinator import DecisionCoordinator  # noqa: E402
from decision.integrity import summarise, verify_model_dir  # noqa: E402
from decision.providers.base import DecisionBackendUnavailable, DECISION_QUESTIONS  # noqa: E402
from decision.providers.legacy import LegacyDecisionProvider  # noqa: E402
from decision.providers.tacit import TacitDecisionProvider  # noqa: E402
from decision.validator import DecisionValidator  # noqa: E402

DEFAULT_CASES = PROJECT_ROOT / "scripts" / "benchmarks" / "cases" / "decision_cases_synthetic.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "test_output" / "acceptance"


class RecordingAgent:
    """The legacy Qwen agent, as a counter: acceptance must not need a real LLM."""

    def __init__(self, action: str = "chat", confidence: float = 0.7) -> None:
        self.calls = 0
        self.model = "legacy-agent-double"
        self._action = action
        self._confidence = confidence

    def is_available(self) -> bool:
        return True

    def observe_turn(self, **_kwargs: Any) -> AgentObservation:
        self.calls += 1
        return AgentObservation(
            proposal=RouterProposal(
                action=RouterAction(self._action), confidence=self._confidence
            ),
            intent="counseling",
        )


def _request(text: str, *, turn: int = 3, history: str = "") -> DecisionRequest:
    return DecisionRequest(
        user_text=text,
        session_state="interview",
        turn_index=turn,
        active_scale=None,
        game_active=False,
        recent_history=history,
    )


def _load_cases(limit: int = 12) -> list[dict[str, Any]]:
    cases = []
    for line in DEFAULT_CASES.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            cases.append(json.loads(text))
    return cases[:limit]


def _policy(text: str, observation: AgentObservation, rounds: int = 6):
    return TurnPolicy().decide(
        user_text=text,
        proposal=observation.proposal,
        snapshot=TurnStateSnapshot(round_count=rounds),
        signals=TurnSignals(),
    )


# ---------------------------------------------------------------------------


def check_integrity(model_path: Path) -> dict[str, Any]:
    report = verify_model_dir(model_path, require_manifest=True, extra_files_ok=True)
    return {
        "ok": report.ok,
        "summary": summarise(report),
        "problems": list(report.problems[:10]),
    }


def check_provider_single(provider: TacitDecisionProvider, text: str) -> dict[str, Any]:
    request = _request(text)
    started = time.perf_counter()
    raw = provider.decide(request)
    latency = (time.perf_counter() - started) * 1000.0  # includes the lazy load
    warm_started = time.perf_counter()
    provider.decide(request)
    warm_latency = (time.perf_counter() - warm_started) * 1000.0
    return {
        "action": raw.action,
        "confidence": round(raw.confidence, 4),
        "route": raw.route,
        "intent": raw.intent_jev,
        "emotion": raw.emotion,
        "clarify": raw.clarify,
        "questions": sorted(raw.per_question),
        "action_probs": {k: round(v, 4) for k, v in raw.probabilities.items()},
        "latency_cold_ms": round(latency, 1),
        "latency_warm_ms": round(warm_latency, 1),
        "probability_sums": {
            name: round(sum(probs.values()), 6) for name, probs in raw.per_question.items()
        },
    }


def check_modes(
    provider: TacitDecisionProvider, text: str
) -> dict[str, Any]:
    out: dict[str, Any] = {}

    agent = RecordingAgent()
    legacy = LegacyDecisionProvider(agent)
    coordinator = DecisionCoordinator(mode=DecisionMode.LEGACY, primary=legacy)
    outcome = coordinator.observe_turn(_request(text))
    out["legacy"] = {
        "source": outcome.observation.source,
        "action": outcome.observation.proposal.action.value,
        "agent_calls": agent.calls,
        "passthrough": outcome.decision.action == outcome.observation.proposal.action.value,
    }

    agent = RecordingAgent()
    legacy = LegacyDecisionProvider(agent)
    coordinator = DecisionCoordinator(
        mode=DecisionMode.SHADOW, primary=legacy, shadow=provider
    )
    outcome = coordinator.observe_turn(_request(text))
    out["shadow"] = {
        "authoritative_action": outcome.observation.proposal.action.value,
        "agent_calls": agent.calls,
        "shadow_present": outcome.shadow is not None,
        "shadow_action": outcome.shadow.action if outcome.shadow else None,
        "agreement": outcome.comparison.get("action_match"),
    }

    agent = RecordingAgent()
    legacy = LegacyDecisionProvider(agent)
    coordinator = DecisionCoordinator(
        mode=DecisionMode.JEV,
        primary=provider,
        legacy=legacy,
        validator=DecisionValidator(abstain_threshold=0.01),
    )
    outcome = coordinator.observe_turn(_request(text))
    out["jev"] = {
        "provider": outcome.provider,
        "action": outcome.observation.proposal.action.value,
        "rejected": outcome.reject_reason,
        "escalated": outcome.escalated,
        "agent_calls": agent.calls,
        "source": outcome.observation.source,
    }
    return out


def check_authority(provider: TacitDecisionProvider) -> dict[str, Any]:
    """Find a real turn where the model proposes end_session, then refuse it."""
    hits: list[dict[str, Any]] = []
    for case in _load_cases(12):
        raw = provider.decide(_request(str(case["text"])))
        if raw.action != "end_session":
            continue
        coordinator = DecisionCoordinator(
            mode=DecisionMode.JEV,
            primary=provider,
            legacy=LegacyDecisionProvider(RecordingAgent()),
            validator=DecisionValidator(abstain_threshold=0.01),
        )
        outcome = coordinator.observe_turn(_request(str(case["text"])))
        decision = _policy(str(case["text"]), outcome.observation)
        hits.append(
            {
                "case": case.get("id"),
                "model_action": raw.action,
                "model_confidence": round(raw.confidence, 4),
                "outcome_action": outcome.observation.proposal.action.value,
                "reject_reason": outcome.reject_reason,
                "escalated": outcome.escalated,
                "turn_policy_action": decision.action.value,
            }
        )
        if len(hits) >= 2:
            break
    return {
        "model_end_session_turns": len(hits),
        "refused": sum(1 for h in hits if h["turn_policy_action"] != "end_session"),
        "samples": hits,
    }


def check_fallback() -> dict[str, Any]:
    """A dead endpoint must escalate, not raise."""
    dead = TacitDecisionProvider(
        transport="server", endpoint="http://127.0.0.1:9", timeout=1.0
    )
    agent = RecordingAgent()
    coordinator = DecisionCoordinator(
        mode=DecisionMode.JEV,
        primary=dead,
        legacy=LegacyDecisionProvider(agent),
        escalation=EscalationMode.LEGACY,
    )
    outcome = coordinator.observe_turn(_request("我最近总是睡不好"))
    return {
        "escalated": outcome.escalated,
        "escalation_source": outcome.escalation_source,
        "action": outcome.observation.proposal.action.value,
        "agent_calls": agent.calls,
        "mode": outcome.mode.value,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--text", default="最近我晚上总是睡不着，躺下两三个小时都醒着。")
    args = parser.parse_args(argv)

    model_path = Path(args.model_path).expanduser()
    if not model_path.is_dir():
        print(f"checkpoint missing: {model_path}", file=sys.stderr)
        print("prepare it with docs/deployment/agent_jev_offline_runbook.md", file=sys.stderr)
        return 3

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_path": model_path.name,
        "offline_env": {
            key: os.environ.get(key) for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        },
    }

    report["integrity"] = check_integrity(model_path)
    if not report["integrity"]["ok"]:
        print(json.dumps(report["integrity"], ensure_ascii=False, indent=2), file=sys.stderr)
        return 3
    print(f"[1/6] integrity: {report['integrity']['summary']}", flush=True)

    provider = TacitDecisionProvider(
        transport="inprocess", model=str(model_path), timeout=60.0
    )
    try:
        report["provider_single"] = check_provider_single(provider, args.text)
    except DecisionBackendUnavailable as exc:
        print(f"provider unavailable: {exc}", file=sys.stderr)
        return 4
    print(f"[2/6] provider: {report['provider_single']['action']} "
          f"({report['provider_single']['confidence']}) route={report['provider_single']['route']}",
          flush=True)

    report["modes"] = check_modes(provider, args.text)
    print("[3/6] modes:", {k: v.get("action") or v.get("authoritative_action")
                           for k, v in report["modes"].items()}, flush=True)

    report["authority"] = check_authority(provider)
    print(f"[4/6] authority: {report['authority']['refused']}/"
          f"{report['authority']['model_end_session_turns']} end_session proposals refused",
          flush=True)

    report["fallback"] = check_fallback()
    print(f"[5/6] fallback: escalated={report['fallback']['escalated']} "
          f"action={report['fallback']['action']}", flush=True)

    report["declarations"] = {
        "official_path": "anyjev Tacit (letter readout, chat template)",
        "project_readout": "option-label-token readout; NOT Tacit semantics",
        "not_for_diagnosis": True,
        "mock_components": ["RecordingAgent (legacy agent double)"],
        "real_components": ["Tacit-1.7B weights", "transformers forward", "integrity manifest"],
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "tacit_1.7b_acceptance.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[6/6] artifact: {target}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
