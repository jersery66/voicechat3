"""Replay multi-turn interview trajectories through the real decision chain.

The runner deliberately reuses the production objects instead of a bespoke
simulator: the same ``DecisionRequest`` builder, the same ``DecisionValidator``,
the same ``build_observation``, the same ``TurnPolicy``, the same
``OfferLedger``, and the real ``ScaleRuntime``.  If a comparison here says the
model proposed an assessment at turn 7, that is what the production chain does
with the same input -- not a second implementation agreeing with itself.

What the runner owns is only the *world*: the round counter, the scripted
utterances, and the annotated state changes (completed relaxation, a scale
started mid-trajectory, session ending).  Symptom evidence is **not** taken from
the annotations: it is accumulated from the utterance text with the same
deterministic extractor production uses, because feeding ``turns[].domains``
into the prompt would hand the model the answer key and would give the rule
baseline information no live session can produce.

See ``decision.request_builder`` for the input contract and
``conversation.interview_state`` for the evidence accumulation.
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
from conversation.interview_state import InterviewState
from conversation.offer_ledger import OfferLedger, offer_snapshot_fields
from conversation.turn_policy import TurnPolicy
from conversation.turn_signals import (
    collect_turn_signals,
    detect_offer_question,
    detect_risk_marker,
    detect_wants_to_talk,
)
from core.scoring import score_symptom_signals
from decision.contracts import DecisionRequest, RawDecision, build_observation
from decision.request_builder import HISTORY_MODES, build_decision_request
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
    relaxation_done: bool = False
    relaxation_active: bool = False
    clarification_pending: bool = False
    session_state: str = "CHATTING"
    #: A safety disclosure persists for the rest of the session.
    risk_disclosed: bool = False


@dataclass
class TrajectoryRunner:
    """Drive one provider through one trajectory with production components."""

    provider: Any
    validator: DecisionValidator = field(default_factory=DecisionValidator)
    policy: TurnPolicy = field(default_factory=TurnPolicy)
    registered_scales: tuple[str, ...] = ()
    latency_samples: list[float] = field(default_factory=list)
    #: One of ``decision.request_builder.HISTORY_MODES``.  Only the input varies
    #: between ablation arms; model, template and policy stay fixed.
    history_mode: str = "structured_history"

    def __post_init__(self) -> None:
        if not self.registered_scales:
            self.registered_scales = self._registry_names()
        self._runtime = self._new_runtime()
        if self.history_mode not in HISTORY_MODES:
            raise ValueError(
                f"unknown history_mode {self.history_mode!r}; expected {HISTORY_MODES}"
            )

    # -- setup -------------------------------------------------------------

    @staticmethod
    def _registry_names() -> tuple[str, ...]:
        """The scale names the *production* registry can actually start.

        Reading ``services.scales.SCALES`` directly used to be close enough,
        but "close enough" is how the two request builders drifted apart: the
        candidate list is part of the model's input, so it has to come from the
        same manager production uses.
        """
        try:
            from services.scales import get_scale_manager

            names = tuple(get_scale_manager().get_scale_names())
            if names:
                return names
        except Exception:  # pragma: no cover - offline contract tests
            pass
        try:
            from services.scales import SCALES

            return tuple(SCALES.keys())
        except Exception:  # pragma: no cover
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
        state = InterviewState()
        scores = {name: 0 for name in (self.registered_scales or ("PHQ-9", "GAD-7", "PCL-5"))}
        if self._runtime is not None:
            self._runtime.reset()
        turns: list[dict[str, Any]] = []

        for index, script in enumerate(trajectory.get("turns", ()), start=1):
            turns.append(
                self._run_turn(index, script, trajectory, world, ledger, state, scores)
            )

        return {
            "id": trajectory.get("id"),
            "title": trajectory.get("title"),
            "split": trajectory.get("split"),
            "turns": turns,
            "evidence": state.evidence(),
            "history_mode": self.history_mode,
        }

    def _run_turn(
        self,
        index: int,
        script: dict[str, Any],
        trajectory: dict[str, Any],
        world: _World,
        ledger: OfferLedger,
        state: InterviewState,
        scores: dict[str, int],
    ) -> dict[str, Any]:
        world.round = int(script.get("round") or index)

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
        active_before = getattr(runtime, "active_scale", None)
        snapshot = self._build_snapshot(world, ledger, runtime, text)
        request = self._build_request(
            world,
            ledger,
            runtime,
            text,
            state,
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
        if signals.risk_marker or detect_risk_marker(text):
            world.risk_disclosed = True

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
        effects = self._apply(decision, world, ledger)
        world.clarification_pending = decision.action is TurnAction.CLARIFY_INPUT

        # -- accumulate this utterance *after* the decision -----------------
        # Production hands the current sentence to the model verbatim and folds
        # it into the summary only from the next turn on; matching that keeps
        # the two paths' inputs identical (see tests/test_decision_input_consistency).
        deltas: dict[str, int] = {}
        if not active_before:
            deltas, _reasons = score_symptom_signals(text, scores)
        state.record_user(
            text,
            round_count=world.round,
            assessment_active=bool(active_before),
            deltas=deltas,
        )
        if deltas and any(value > 0 for value in deltas.values()):
            for scale_name, delta in deltas.items():
                if delta > 0:
                    scores[scale_name] = scores.get(scale_name, 0) + delta
        if script.get("assistant"):
            state.record_assistant(str(script["assistant"]))

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
            "validate_accepted": bool(verdict.accepted),
            "offers": effects["offers"],
            "offer_ids": effects["offer_ids"],
            "policy_action": decision.action.value,
            "policy_reason": decision.reason,
            "executed": executed,
            "executed_scale": effects["started_scale"],
            "active_scale": active_after,
            "accepted_offer_kind": effects["accepted_kind"],
            "accepted_offer_target": effects["accepted_target"],
            "accepted_offer_id": effects["accepted_offer_id"],
            "declined_kind": effects["declined_kind"],
            "declined_offer_id": effects["declined_offer_id"],
            "explicit_request": effects["explicit_request"],
            # Kept for readability of the artifacts.  Permission violations are
            # *not* derived from this string; they are computed from the offer
            # id and the execution event (see decision.timing_metrics).
            "consent_basis": self._consent_basis(decision),
            "history_chars": len(request.recent_history or ""),
            "state_chars": len(request.symptom_summary or "") + len(request.recent_history or ""),
            "symptom_summary": request.symptom_summary,
            "latency_ms": round(elapsed_ms, 2),
        }

    @staticmethod
    def _consent_basis(decision: TurnDecision) -> Optional[str]:
        """Why an executable step was allowed, or ``None`` if it was not.

        Reported for humans; the machine check lives in
        :mod:`decision.timing_metrics` and reads events, not reason strings.
        """
        if decision.reason in (
            OFFER_REASON_SCALE_ACCEPTED,
            OFFER_REASON_RELAXATION_ACCEPTED,
        ):
            return "offer_accepted"
        if decision.reason in ("user_scale_request", "user_relaxation_request"):
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
            risk_in_session=world.risk_disclosed,
            **fields,
        )

    def _build_request(
        self,
        world: _World,
        ledger: OfferLedger,
        runtime: Any,
        text: str,
        state: InterviewState,
        *,
        wants_to_talk: bool = False,
    ) -> DecisionRequest:
        return build_decision_request(
            user_text=text,
            round_count=world.round,
            interview_state=state,
            symptom_turns=state.symptom_turns,
            runtime=runtime,
            offer_state=ledger.state(),
            registered_scales=self.registered_scales,
            session_state=world.session_state,
            clarification_pending=world.clarification_pending,
            relaxation_done=world.relaxation_done,
            relaxation_active=world.relaxation_active,
            history_mode=self.history_mode,
            risk_marker=detect_risk_marker(text),
            wants_to_talk=bool(wants_to_talk) or detect_wants_to_talk(text),
        )

    @staticmethod
    def _fallback_observation(reason: str):
        from decision.contracts import rejection_observation

        # A stage-1 offer whose stage-2 target stayed undetermined degrades to
        # a clarification, not to a silent continue -- see rejection_observation.
        mapped = rejection_observation(reason, source="jev")
        if mapped is not None:
            return mapped
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

    def _apply(
        self, decision: TurnDecision, world: _World, ledger: OfferLedger
    ) -> dict[str, Any]:
        effects: dict[str, Any] = {
            "offers": [],
            "offer_ids": [],
            "accepted_kind": None,
            "accepted_target": None,
            "accepted_offer_id": None,
            "declined_kind": None,
            "declined_offer_id": None,
            "explicit_request": None,
            "started_scale": None,
        }

        if decision.action is TurnAction.OFFER_SCALE:
            record = ledger.record_offer(
                OfferKind.SCALE,
                target=decision.scale_name,
                round_count=world.round,
                session_state=world.session_state,
            )
            effects["offers"].append({"kind": "scale", "target": decision.scale_name})
            effects["offer_ids"].append(record.offer_id)
        elif decision.action is TurnAction.OFFER_RELAXATION:
            record = ledger.record_offer(
                OfferKind.RELAXATION,
                target=None,
                round_count=world.round,
                session_state=world.session_state,
            )
            effects["offers"].append({"kind": "relaxation", "target": None})
            effects["offer_ids"].append(record.offer_id)
        elif decision.reason == OFFER_REASON_SCALE_ACCEPTED:
            record = ledger.resolve_pending(OfferOutcome.ACCEPTED, world.round, note="consent")
            effects["accepted_kind"] = "scale"
            effects["accepted_target"] = decision.scale_name
            effects["accepted_offer_id"] = record.offer_id if record else None
            if self._runtime is not None and decision.scale_name:
                self._runtime.start(decision.scale_name)
                effects["started_scale"] = decision.scale_name
        elif decision.reason == "user_scale_request":
            record = ledger.resolve_pending(
                OfferOutcome.ACCEPTED, world.round, note="explicit_request"
            )
            effects["explicit_request"] = "scale"
            effects["accepted_offer_id"] = record.offer_id if record else None
            if self._runtime is not None and decision.scale_name:
                self._runtime.start(decision.scale_name)
                effects["started_scale"] = decision.scale_name
        elif decision.action is TurnAction.START_SCALE:
            # A scale can be started without an invitation (the participant
            # asked for one, or a deterministic signal fired).  Execution must
            # follow whichever route approved it, or the harness would report a
            # start that never happened.
            if self._runtime is not None and decision.scale_name:
                self._runtime.start(decision.scale_name)
                effects["started_scale"] = decision.scale_name
        elif decision.reason == OFFER_REASON_RELAXATION_ACCEPTED:
            record = ledger.resolve_pending(OfferOutcome.ACCEPTED, world.round, note="consent")
            effects["accepted_kind"] = "relaxation"
            effects["accepted_offer_id"] = record.offer_id if record else None
            world.relaxation_active = True
        elif decision.reason == "user_relaxation_request":
            record = ledger.resolve_pending(
                OfferOutcome.ACCEPTED, world.round, note="explicit_request"
            )
            effects["explicit_request"] = "relaxation"
            effects["accepted_offer_id"] = record.offer_id if record else None
            world.relaxation_active = True
        elif decision.reason == OFFER_REASON_SCALE_DECLINED:
            record = ledger.resolve_pending(OfferOutcome.DECLINED, world.round, note="refusal")
            effects["declined_kind"] = "scale"
            effects["declined_offer_id"] = record.offer_id if record else None
        elif decision.reason == OFFER_REASON_RELAXATION_DECLINED:
            record = ledger.resolve_pending(OfferOutcome.DECLINED, world.round, note="refusal")
            effects["declined_kind"] = "relaxation"
            effects["declined_offer_id"] = record.offer_id if record else None
        elif decision.reason == OFFER_REASON_CLARIFYING:
            ledger.mark_clarifying(world.round)
        elif decision.reason == OFFER_REASON_DEFERRED:
            ledger.touch_pending(world.round)
        return effects

    def _complete_active_scale(self) -> None:
        """Walk a running questionnaire to completion through the real runtime.

        ``accept_answer`` clears ``waiting_for_answer`` and moves to the next
        item, so the next answer has to be *presented* first.  Without the
        re-present call every answer after the first is rejected as
        ``not_waiting`` and the scale never completes -- which silently turned
        "a completed scale" trajectories into "still running" ones.
        """
        if self._runtime is None:
            return
        snap = self._runtime.snapshot()
        name = snap.active_scale
        if not name:
            return
        from services.scales import SCALES

        total = len((SCALES.get(name) or {}).get("questions") or ())
        for _ in range(total + 2):
            snap = self._runtime.snapshot()
            if not snap.active_scale or not snap.current_item:
                break
            update = self._runtime.accept_answer(
                item=snap.current_item, score=1, scale_name=snap.active_scale
            )
            if update.completed:
                break
            if not update.accepted:
                break
            self._runtime.present_current_item()

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
    history_mode: str = "structured_history",
) -> dict[str, Any]:
    """Run every trajectory and score it against its windows.

    ``provider_for`` builds one provider per trajectory.  It exists because the
    replay self-check is per-trajectory by construction (its plan *is* that
    trajectory's annotation), while a real model is one object for the whole
    set.  Pooling latency across both keeps the reported percentiles comparable.
    """
    from decision.timing_metrics import aggregate, aggregate_turns, evaluate_trajectory

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
            history_mode=history_mode,
        )
        result = runner.run(trajectory)
        samples.extend(runner.latency_samples)
        rows.append(result)
        evaluated.append(evaluate_trajectory(trajectory, result["turns"]))

    summary = aggregate(evaluated)
    turns_by_id = {row["id"]: row["turns"] for row in rows}
    summary["stage_detail"] = aggregate_turns(evaluated, turns_by_id)
    # Counts that need the raw turn rows, kept out of ``aggregate`` so that
    # function stays a pure reducer over per-trajectory scores.
    actions = summary["stage_detail"]["policy_actions"]
    summary["stage"]["policy_offered"] = actions.get("offer_scale", 0) + actions.get(
        "offer_relaxation", 0
    )
    summary["stage"]["policy_executed"] = actions.get("start_scale", 0) + actions.get(
        "recommend_relaxation", 0
    )
    summary["latency_ms"] = {
        "p50": latency_percentile(samples, 0.50),
        "p95": latency_percentile(samples, 0.95),
    }
    summary["history_mode"] = history_mode
    return {"summary": summary, "trajectories": rows, "per_trajectory": evaluated}


__all__ = ["TrajectoryRunner", "latency_percentile", "run_trajectories", "offer_kind_for"]
