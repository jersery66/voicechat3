"""Annotation-replay provider: the harness' own self-check.

This is **not** a model and must never be reported as one.  It replays the
annotations of a trajectory so that the surrounding chain (contracts, validator,
observation mapping, ``TurnPolicy``, ledger, runtime) can be tested in
isolation:

* if the replay cannot reach the annotated window, the *chain* is broken,
  not the model;
* when a model later scores worse than the replay, that gap is measured against
  a chain that is known to be able to succeed.

Two behaviours keep the replay realistic rather than cheating:

1. it answers an outstanding invitation with "continue" (a model must not stack
   a second invitation on top of an unanswered one);
2. it never proposes anything on a turn whose annotation says the participant
   is in a safety situation.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from decision.contracts import DecisionRequest, RawDecision
from decision.providers.fake import make_intervention_decision
from decision.timing import InterventionIntent, normalize_intervention


class ScriptedTimingProvider:
    """Replay ``{round: intervention}`` annotations through the real chain."""

    name = "scripted_replay"

    def __init__(
        self,
        plan: Mapping[int, str],
        *,
        targets: Optional[Mapping[int, str]] = None,
        forced: Iterable[int] = (),
        confidence: float = 0.95,
    ) -> None:
        self.plan = {int(round_number): label for round_number, label in plan.items()}
        self.targets = {int(k): v for k, v in (targets or {}).items()}
        self.forced = {int(round_number) for round_number in forced}
        self.confidence = float(confidence)

    @classmethod
    def from_trajectory(cls, trajectory: Mapping[str, Any]) -> "ScriptedTimingProvider":
        """Build a replay plan from one trajectory's annotations.

        Two annotation styles are supported, and they answer different
        questions:

        ``expect`` / ``force``  a per-turn label.  Used by the older fixtures and
                                by the guard cases ("propose this here even
                                though it is wrong, and check the policy refuses").
        ``windows``             a *range* of acceptable rounds.  A turn inside a
                                window is where an invitation is defensible, so
                                the replay opens one there.  This is what makes
                                "can the chain reach the window at all" a real
                                self-check for window-annotated trajectories,
                                which by design carry no per-turn ``expect`` on
                                the turns inside their windows (a range has no
                                single correct round).
        """
        from decision.timing_metrics import _normalise_windows

        plan: dict[int, str] = {}
        targets: dict[int, str] = {}
        forced: set[int] = set()

        windows: dict[str, list[tuple[int, int]]] = {}
        for kind in ("scale", "relaxation"):
            found, _certainty = _normalise_windows((trajectory.get("windows") or {}).get(kind))
            windows[kind] = found

        for index, turn in enumerate(trajectory.get("turns", ()), start=1):
            round_number = int(turn.get("round") or index)
            label = turn.get("force") or turn.get("expect")
            if label:
                normalized = normalize_intervention(str(label))
                if normalized:
                    plan[round_number] = normalized
                    if turn.get("force"):
                        # ``force`` means "propose this even though the state says
                        # otherwise", used to prove that the surrounding guards refuse.
                        forced.add(round_number)
                    if turn.get("gold_target"):
                        targets[round_number] = str(turn["gold_target"])
                    continue

            for kind, spans in windows.items():
                if any(start <= round_number <= end for start, end in spans):
                    plan[round_number] = (
                        InterventionIntent.OFFER_SCALE.value
                        if kind == "scale"
                        else InterventionIntent.OFFER_RELAXATION.value
                    )
                    if kind == "scale" and trajectory.get("gold_scale"):
                        targets[round_number] = str(trajectory["gold_scale"])
                    break

        return cls(plan, targets=targets, forced=forced)

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "turns": len(self.plan)}

    def decide(self, request: DecisionRequest) -> RawDecision:
        current = int(request.current_round)
        label = self.plan.get(current, InterventionIntent.CONTINUE_INTERVIEW.value)

        if current not in self.forced:
            if request.offer_state.pending is not None:
                label = InterventionIntent.CONTINUE_INTERVIEW.value
            elif request.active_scale or request.risk_marker:
                label = InterventionIntent.CONTINUE_INTERVIEW.value

        target = self.targets.get(current)
        if normalize_intervention(label) == InterventionIntent.OFFER_SCALE.value and not target:
            candidates = request.scale_candidates()
            target = candidates[0].name if candidates else None

        return make_intervention_decision(
            label,
            offer_target=target,
            confidence=self.confidence,
            provider=self.name,
            reason="scripted_replay",
        )


def plans_for(trajectories: Iterable[Mapping[str, Any]]) -> dict[str, ScriptedTimingProvider]:
    """One replay provider per trajectory, keyed by id."""
    return {
        str(trajectory.get("id")): ScriptedTimingProvider.from_trajectory(trajectory)
        for trajectory in trajectories
    }


__all__ = ["ScriptedTimingProvider", "plans_for"]
