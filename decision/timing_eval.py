"""Replay multi-turn interview trajectories through the real decision chain.

The runner deliberately reuses the production objects instead of a bespoke
simulator: the same ``DecisionRequest``, the same ``DecisionValidator``, the
same ``build_observation``, the same ``TurnPolicy``, the same ``OfferLedger``,
and the real ``ScaleRuntime``.  If a comparison here says the model proposed an
assessment at turn 7, that is what the production chain does with the same
input -- not a second implementation agreeing with itself.

What the runner owns is only the *world*: the round counter, the scripted
utterances, and the annotated state changes (symptom domains, completed
relaxation, session ending).  Those annotations are facts of the test set, not
inferences, so a trajectory is reproducible without a language model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnDecision,
    TurnStateSnapshot,
)
from conversation.offer_ledger import OfferLedger, offer_snapshot_fields
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import (
    collect_turn_signals,
    detect_offer_question,
    detect_risk_marker,
    detect_wants_to_talk,
)
from decision.contracts import DecisionRequest, RawDecision, build_observation
from decision.timing import (
    OFFER_REASON_CLARIFYING,
    OFFER_REASON_DEFERRED,
    OFFER_REASON_RELAXATION_ACCEPTED,
    OFFER_REASON_RELAXATION_DECLINED,
    OFFER_REASON_SCALE_ACCEPTED,
    OFFER_REASON_SCALE_DECLINED,
    OfferKind,
    OfferOutcome,
    mentions_offer,
    rank_scale_candidates,
)
from decision.timing import offer_kind_for
from decision.validator import DecisionValidator


@dataclass
class _World:
    """Mutable facts of one simulated session."""

    round: int = 0
    symptom_turns: int = 0
    relaxation_done: bool = False
    relaxation_active: bool = False
    clarification_pending: bool = False
    session_state: str = "CHATTING"


@dataclass
class TrajectoryRunner:
    """Drive one provider through one trajectory with production components."""

    provider: Any
    validator: DecisionValidator = field(default_factory=DecisionValidator)
    policy: TurnPolicy = field(default_factory=TurnPolicy)
    registered_scales: tuple[str, ...] = ()
    latency_samples: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.registered_scales:
            self.registered_scales = self._registry_names()
        self._runtime = self._new_runtime()

    # -- setup -------------------------------------------------------------

    @staticmethod
    def _registry_names() -> tuple[str, ...]:
        try:
            from services.scales import SCALES

            return tuple(SCALES.keys())
        except Exception:  # pragma: no cover - offline contract tests
            return ("PHQ-9", "GAD-7", "PCL-5")

    @staticmethod
    def _new_runtime():
        try:
            from assessment.scale_runtime import ScaleRuntime

            return ScaleRuntime()
        except Exception:  # pragma: no cover
            return None

    # -- main loop ---------------------------------------------------------

    def run(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        ledger = OfferLedger()
        world = _World()
        if self._runtime is not None:
            self._runtime.reset()
        turns: list[dict[str, Any]] = []

        for index, script in enumerate(trajectory.get("turns", ()), start=1):
            turns.append(self._run_turn(index, script, trajectory, world, ledger))

        return {
            "id": trajectory.get("id"),
            "title": trajectory.get("title"),
            "split": trajectory.get("split"),
            "turns": turns,
        }

    def _run_turn(
        self,
        index: int,
        script: dict[str, Any],
        trajectory: dict[str, Any],
        world: _World,
        ledger: OfferLedger,
    ) -> dict[str, Any]:
        world.round = int(script.get("round") or index)

        domains = script.get("domains") or ()
        if domains:
            world.symptom_turns += 1
        if script.get("session_state"):
            world.session_state = str(script["session_state"])
        if script.get("event") == "relaxation_done":
            world.relaxation_done = True
            world.relaxation_active = False
        if script.get("event") == "start_scale" and self._runtime is not None:
            # A trajectory may begin mid-assessment (scenario: do not interrupt
            # a questionnaire that is already running).
            name = str(script.get("scale") or (self.registered_scales or ("PHQ-9",))[0])
            self._runtime.start(name)

        text = str(script.get("text") or "")
        # No answer timeout: an invitation survives until the conversation has
        # genuinely moved on for several turns without referring to it.
        ledger.expire_on_topic_drift(
            world.round,
            engaged=mentions_offer(text) or detect_offer_question(text),
        )
        runtime = self._runtime.snapshot() if self._runtime is not None else None
        snapshot = self._build_snapshot(world, ledger, runtime, text)
        request = self._build_request(
            world,
            ledger,
            runtime,
            text,
            domains,
            wants_to_talk=bool(script.get("listening")),
        )

        started = self._now()
        raw = self.provider.decide(request)
        elapsed_ms = (self._now() - started) * 1000.0
        self.latency_samples.append(elapsed_ms)

        verdict = self.validator.validate(raw, request)
        observation = (
            build_observation(verdict.decision, source="jev")
            if verdict.accepted
            else self._fallback_observation(verdict.reason or "rejected")
        )
        signals = collect_turn_signals(
            text,
            snapshot,
            semantic_ambiguity=bool(script.get("ambiguous")),
            semantic_target=script.get("semantic_target"),
            semantic_reason="trajectory_annotation" if script.get("ambiguous") else "",
        )
        decision = self.policy.decide(
            user_text=text,
            proposal=observation.proposal,
            snapshot=snapshot,
            signals=signals,
        )

        if decision.action is TurnAction.END_SESSION:
            ledger.cancel_all(world.round, "session_end")
        elif signals.risk_marker:
            ledger.cancel_all(world.round, "risk")
        offers = self._apply(decision, world, ledger)
        world.clarification_pending = decision.action is TurnAction.CLARIFY_INPUT

        executed = decision.action.value
        if script.get("event") == "complete_scale" and runtime is not None:
            self._complete_active_scale()

        active_after = None
        if self._runtime is not None:
            active_after = self._runtime.snapshot().active_scale

        return {
            "round": world.round,
            "text_len": len(text),
            "expect": script.get("expect"),
            "expect_action": script.get("expect_action"),
            "listening": bool(script.get("listening")),
            "intervention": raw.normalized_intervention() if raw is not None else None,
            "validate_reason": verdict.reason,
            "offers": offers,
            "policy_action": decision.action.value,
            "policy_reason": decision.reason,
            "executed": executed,
            "active_scale": active_after,
            "accepted_offer_kind": self._last_accepted_kind,
            "accepted_offer_target": self._last_accepted_target,
            "declined_kind": self._last_declined_kind,
            "consent_basis": self._consent_basis(decision),
            "latency_ms": round(elapsed_ms, 2),
        }

    @staticmethod
    def _consent_basis(decision: TurnDecision) -> Optional[str]:
        """Why an executable step was allowed, or ``None`` if it was not.

        This is what makes "no execution without consent" measurable instead of
        merely asserted: ``start_scale`` / ``recommend_relaxation`` without a
        basis is a permission violation.
        """
        if decision.reason == OFFER_REASON_SCALE_ACCEPTED:
            return "offer_accepted"
        if decision.reason == OFFER_REASON_RELAXATION_ACCEPTED:
            return "offer_accepted"
        if decision.reason == "user_scale_request":
            return "explicit_request"
        if decision.reason == "user_relaxation_request":
            return "explicit_request"
        return None

    # -- construction helpers ---------------------------------------------

    def _build_snapshot(
        self, world: _World, ledger: OfferLedger, runtime: Any, text: str = ""
    ) -> TurnStateSnapshot:
        completed = tuple(getattr(runtime, "completed_scales", ()) or ())
        administered = tuple(getattr(runtime, "administered_scales", ()) or ())
        ranked = rank_scale_candidates(
            registered=self.registered_scales,
            completed=completed,
            administered=administered,
            declined=ledger.state().declined_scales(),
        )
        fields = dict(offer_snapshot_fields(ledger.state()))
        # The ledger flattens offer bookkeeping; the candidate ranking is owned
        # by the scale registry, so the caller overwrites that one key.
        fields["scale_candidates"] = tuple(candidate.name for candidate in ranked)
        return TurnStateSnapshot(
            session_state=world.session_state,
            round_count=world.round,
            active_scale=getattr(runtime, "active_scale", None),
            current_item=getattr(runtime, "current_item", None),
            waiting_for_answer=bool(getattr(runtime, "waiting_for_answer", False)),
            completed_scales=completed,
            relaxation_used=world.relaxation_done,
            game_active=False,
            time_limit_reached=False,
            risk_marker=detect_risk_marker(text),
            **fields,
        )

    def _build_request(
        self,
        world: _World,
        ledger: OfferLedger,
        runtime: Any,
        text: str,
        domains: Sequence[str],
        *,
        wants_to_talk: bool = False,
    ) -> DecisionRequest:
        return DecisionRequest(
            user_text=text,
            recent_history="",
            current_round=world.round,
            active_scale=getattr(runtime, "active_scale", None),
            collected_scales={
                name: dict(answers)
                for name, answers in (getattr(runtime, "answers_by_scale", {}) or {}).items()
            },
            relaxation_done=world.relaxation_done,
            session_state=world.session_state,
            waiting_for_answer=bool(getattr(runtime, "waiting_for_answer", False)),
            completed_scales=tuple(getattr(runtime, "completed_scales", ()) or ()),
            relaxation_used=world.relaxation_done,
            offer_state=ledger.state(),
            registered_scales=self.registered_scales,
            administered_scales=tuple(getattr(runtime, "administered_scales", ()) or ()),
            relaxation_active=world.relaxation_active,
            clarification_pending=world.clarification_pending,
            risk_marker=detect_risk_marker(text),
            participant_wants_to_talk=bool(wants_to_talk) or detect_wants_to_talk(text),
            symptom_turns=world.symptom_turns,
            symptom_summary=("、".join(domains) + "方面的主诉") if domains else "",
        )

    @staticmethod
    def _fallback_observation(reason: str):
        return build_observation(
            RawDecision(
                provider="fallback",
                action=RouterAction.CHAT.value,
                reason=f"decision_fallback:{reason}",
                fallback_used=True,
            ),
            source="deterministic",
            fallback_used=True,
        )

    # -- execution ---------------------------------------------------------

    _last_accepted_kind: Optional[str] = None
    _last_accepted_target: Optional[str] = None
    _last_declined_kind: Optional[str] = None

    def _apply(
        self, decision: TurnDecision, world: _World, ledger: OfferLedger
    ) -> list[dict[str, Any]]:
        self._last_accepted_kind = None
        self._last_accepted_target = None
        self._last_declined_kind = None
        offers: list[dict[str, Any]] = []

        if decision.action is TurnAction.OFFER_SCALE:
            ledger.record_offer(
                OfferKind.SCALE,
                target=decision.scale_name,
                round_count=world.round,
                session_state=world.session_state,
            )
            offers.append({"kind": "scale", "target": decision.scale_name})
        elif decision.action is TurnAction.OFFER_RELAXATION:
            ledger.record_offer(
                OfferKind.RELAXATION,
                target=None,
                round_count=world.round,
                session_state=world.session_state,
            )
            offers.append({"kind": "relaxation", "target": None})
        elif decision.reason == OFFER_REASON_SCALE_ACCEPTED:
            ledger.resolve_pending(OfferOutcome.ACCEPTED, world.round, note="consent")
            self._last_accepted_kind = "scale"
            self._last_accepted_target = decision.scale_name
            if self._runtime is not None and decision.scale_name:
                self._runtime.start(decision.scale_name)
        elif decision.reason == "user_scale_request":
            ledger.resolve_pending(OfferOutcome.ACCEPTED, world.round, note="explicit_request")
            if self._runtime is not None and decision.scale_name:
                self._runtime.start(decision.scale_name)
        elif decision.action is TurnAction.START_SCALE:
            # A scale can be started without an invitation (the participant
            # asked for one, or a deterministic signal fired).  Execution must
            # follow whichever route approved it, or the harness would report a
            # start that never happened.
            if self._runtime is not None and decision.scale_name:
                self._runtime.start(decision.scale_name)
        elif decision.reason == OFFER_REASON_RELAXATION_ACCEPTED:
            ledger.resolve_pending(OfferOutcome.ACCEPTED, world.round, note="consent")
            self._last_accepted_kind = "relaxation"
            world.relaxation_active = True
        elif decision.reason == "user_relaxation_request":
            ledger.resolve_pending(OfferOutcome.ACCEPTED, world.round, note="explicit_request")
            world.relaxation_active = True
        elif decision.reason == OFFER_REASON_SCALE_DECLINED:
            ledger.resolve_pending(OfferOutcome.DECLINED, world.round, note="refusal")
            self._last_declined_kind = "scale"
        elif decision.reason == OFFER_REASON_RELAXATION_DECLINED:
            ledger.resolve_pending(OfferOutcome.DECLINED, world.round, note="refusal")
            self._last_declined_kind = "relaxation"
        elif decision.reason == OFFER_REASON_CLARIFYING:
            ledger.mark_clarifying(world.round)
        elif decision.reason == OFFER_REASON_DEFERRED:
            ledger.touch_pending(world.round)
        return offers

    def _complete_active_scale(self) -> None:
        if self._runtime is None:
            return
        snap = self._runtime.snapshot()
        name = snap.active_scale
        if not name:
            return
        from services.scales import SCALES

        total = len((SCALES.get(name) or {}).get("questions") or ())
        for _ in range(total + 1):
            snap = self._runtime.snapshot()
            if not snap.active_scale or not snap.current_item:
                break
            self._runtime.accept_answer(
                item=snap.current_item, score=1, scale_name=snap.active_scale
            )

    @staticmethod
    def _now() -> float:
        import time

        return time.perf_counter()


def latency_percentile(samples: Sequence[float], fraction: float) -> Optional[float]:
    if not samples:
        return None
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def run_trajectories(
    provider: Any = None,
    trajectories: Sequence[dict[str, Any]] = (),
    *,
    provider_for: Optional[Any] = None,
    validator: Optional[DecisionValidator] = None,
    registered_scales: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run every trajectory and score it against its windows.

    ``provider_for`` builds one provider per trajectory.  It exists because the
    replay self-check is per-trajectory by construction (its plan *is* that
    trajectory's annotation), while a real model is one object for the whole
    set.  Pooling latency across both keeps the reported percentiles comparable.
    """
    from decision.timing_metrics import aggregate, evaluate_trajectory

    validator = validator or DecisionValidator()
    samples: list[float] = []
    rows = []
    evaluated = []
    for trajectory in trajectories:
        active = provider_for(trajectory) if provider_for is not None else provider
        runner = TrajectoryRunner(
            provider=active,
            validator=validator,
            registered_scales=registered_scales,
        )
        result = runner.run(trajectory)
        samples.extend(runner.latency_samples)
        rows.append(result)
        evaluated.append(evaluate_trajectory(trajectory, result["turns"]))

    summary = aggregate(evaluated)
    summary["latency_ms"] = {
        "p50": latency_percentile(samples, 0.50),
        "p95": latency_percentile(samples, 0.95),
    }
    return {"summary": summary, "trajectories": rows, "per_trajectory": evaluated}


__all__ = ["TrajectoryRunner", "latency_percentile", "run_trajectories", "offer_kind_for"]
