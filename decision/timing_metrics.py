"""Timing metrics for multi-turn interview trajectories.

Accuracy over single labels cannot answer the question this branch exists to
ask.  "Did the system propose an assessment at a sensible moment in this
interview?" is a question about *when*, so every metric here is computed
against an annotated time window rather than a single correct turn.

Two rules keep the numbers honest:

1. A window is a range, not a point.  Offering one turn early or late inside
   the window is not a different outcome, it is inside the window.
2. A refusal is a fact that must survive.  Re-offering the same instrument
   inside its cooldown is counted as a violation, not as enthusiasm.

The module is pure: it takes per-turn records produced by
:mod:`decision.timing_eval` and returns dictionaries.  Nothing here runs a
model or touches runtime state.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from config import OFFER_RELAXATION_COOLDOWN_ROUNDS, OFFER_SCALE_COOLDOWN_ROUNDS

OFFER_KINDS = ("scale", "relaxation")


def _first_offer_round(turns: Sequence[dict[str, Any]], kind: str) -> Optional[int]:
    for turn in turns:
        if any(offer.get("kind") == kind for offer in turn.get("offers", ())):
            return int(turn["round"])
    return None


def _window_for(trajectory: dict[str, Any], kind: str) -> Optional[tuple[int, int]]:
    windows = trajectory.get("windows") or {}
    window = windows.get(kind)
    if not window:
        return None
    start, end = window
    return int(start), int(end)


def evaluate_trajectory(
    trajectory: dict[str, Any], turns: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Score one trajectory against its annotated windows."""
    result: dict[str, Any] = {
        "id": trajectory.get("id"),
        "title": trajectory.get("title"),
        "split": trajectory.get("split"),
        "turns": len(turns),
    }

    for kind in OFFER_KINDS:
        window = _window_for(trajectory, kind)
        first = _first_offer_round(turns, kind)
        prefix = "scale" if kind == "scale" else "relaxation"
        result[f"{prefix}_window"] = list(window) if window else None
        result[f"{prefix}_first_offer"] = first

        if window is None:
            result[f"{prefix}_outcome"] = "spurious" if first is not None else "not_applicable"
            result[f"{prefix}_delay"] = None
            continue

        start, end = window
        if first is None:
            result[f"{prefix}_outcome"] = "missed"
            result[f"{prefix}_delay"] = None
        elif first < start:
            result[f"{prefix}_outcome"] = "early"
            result[f"{prefix}_delay"] = first - start
        elif first > end:
            result[f"{prefix}_outcome"] = "late"
            result[f"{prefix}_delay"] = first - start
        else:
            result[f"{prefix}_outcome"] = "hit"
            result[f"{prefix}_delay"] = 0

    # -- refusals ---------------------------------------------------------
    repeat_violations = 0
    repeat_after_cooldown = 0
    for kind, cooldown in (
        ("scale", OFFER_SCALE_COOLDOWN_ROUNDS),
        ("relaxation", OFFER_RELAXATION_COOLDOWN_ROUNDS),
    ):
        decline_rounds = [
            int(turn["round"]) for turn in turns if turn.get("declined_kind") == kind
        ]
        if not decline_rounds:
            continue
        for turn in turns:
            round_number = int(turn["round"])
            for decline_round in decline_rounds:
                if round_number <= decline_round:
                    continue
                if not any(offer.get("kind") == kind for offer in turn.get("offers", ())):
                    continue
                if round_number - decline_round <= cooldown:
                    repeat_violations += 1
                else:
                    repeat_after_cooldown += 1
    result["repeat_within_cooldown"] = repeat_violations
    result["repeat_after_cooldown"] = repeat_after_cooldown

    # -- inappropriate switching -----------------------------------------
    inappropriate = 0
    for turn in turns:
        offers = turn.get("offers", ())
        if not offers:
            continue
        if turn.get("active_scale"):
            inappropriate += 1
            continue
        if turn.get("listening") and turn.get("expect") == "continue_interview":
            inappropriate += 1
    result["inappropriate_offers"] = inappropriate

    # -- scale choice -----------------------------------------------------
    gold_scale = trajectory.get("gold_scale")
    scale_offers = [
        offer.get("target")
        for turn in turns
        for offer in turn.get("offers", ())
        if offer.get("kind") == "scale"
    ]
    if gold_scale and scale_offers:
        first_offer = next(target for target in scale_offers if target)
        result["scale_choice_correct"] = first_offer == gold_scale
    else:
        result["scale_choice_correct"] = None

    # -- execution --------------------------------------------------------
    accepted = [
        turn for turn in turns if turn.get("accepted_offer_kind") == "scale"
    ]
    if accepted:
        turn = accepted[0]
        result["execution_success"] = bool(
            turn.get("executed") == "start_scale"
            and turn.get("active_scale") == turn.get("accepted_offer_target")
        )
    else:
        result["execution_success"] = None

    # -- policy gating ----------------------------------------------------
    #
    # "Blocked" means the proposal did not become an invitation.  A proposal
    # that was executed instead (the participant asked for the intervention
    # themselves, so the policy started it directly) was not blocked, and
    # counting it as such would flatter a timid model.
    proposed_offers = [
        turn
        for turn in turns
        if turn.get("intervention") in ("offer_scale", "offer_relaxation")
    ]
    executed_instead = {"start_scale", "recommend_relaxation"}
    blocked = sum(
        1
        for turn in proposed_offers
        if turn.get("validate_reason")
        or (not turn.get("offers") and turn.get("executed") not in executed_instead)
    )
    result["offer_blocked"] = blocked
    result["offer_proposed"] = len(proposed_offers)

    # -- consent handling and policy authority ----------------------------
    #
    # ``permission_violation`` is the metric the whole branch exists to keep at
    # zero: an executable step that no consent or explicit request authorised.
    # ``consent_parse_errors`` counts turns where the participant's answer could
    # not be resolved and had to be clarified -- a conservative outcome, but
    # still a signal that recognition is too coarse.
    executable = {"start_scale", "recommend_relaxation"}
    result["permission_violations"] = sum(
        1
        for turn in turns
        if turn.get("executed") in executable and not turn.get("consent_basis")
    )
    result["consent_parse_errors"] = sum(
        1
        for turn in turns
        if turn.get("policy_reason") == "offer_answer_ambiguous"
        or (
            turn.get("policy_action") == "clarify_input"
            and turn.get("policy_reason") == "offer_answer_ambiguous"
        )
    )

    # -- agreement with the turn annotation -------------------------------
    #
    # Two different agreements, deliberately kept apart: ``intervention`` is what
    # the decision model proposed, ``action`` is what TurnPolicy finally did.
    # A model that proposes well and a policy that refuses well are both scored,
    # and neither can hide behind the other.
    graded = [turn for turn in turns if turn.get("expect")]
    if graded:
        agree = sum(
            1
            for turn in graded
            if turn.get("intervention") == turn.get("expect")
        )
        result["turn_agreement"] = agree / len(graded)
    else:
        result["turn_agreement"] = None

    graded_actions = [turn for turn in turns if turn.get("expect_action")]
    if graded_actions:
        agree_actions = sum(
            1
            for turn in graded_actions
            if turn.get("policy_action") == turn.get("expect_action")
        )
        result["policy_agreement"] = agree_actions / len(graded_actions)
    else:
        result["policy_agreement"] = None

    return result


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return (numerator / denominator) if denominator else None


def aggregate(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-trajectory results into the headline rates."""
    rows = list(results)

    def outcomes(kind: str) -> list[str]:
        return [
            row[f"{kind}_outcome"]
            for row in rows
            if row.get(f"{kind}_window")
        ]

    def summarise(kind: str) -> dict[str, Any]:
        values = outcomes(kind)
        total = len(values)
        hits = values.count("hit")
        delays = [
            row[f"{kind}_delay"]
            for row in rows
            if row.get(f"{kind}_outcome") == "hit" and row.get(f"{kind}_delay") is not None
        ]
        spurious = sum(
            1 for row in rows if row.get(f"{kind}_outcome") == "spurious"
        )
        return {
            "opportunities": total,
            "hit_rate": _rate(hits, total),
            "early_rate": _rate(values.count("early"), total),
            "late_rate": _rate(values.count("late"), total),
            "miss_rate": _rate(values.count("missed"), total),
            "spurious_offers": spurious,
            "mean_delay_turns": (sum(delays) / len(delays)) if delays else None,
        }

    scale_choice = [row["scale_choice_correct"] for row in rows if row.get("scale_choice_correct") is not None]
    execution = [row["execution_success"] for row in rows if row.get("execution_success") is not None]
    agreement = [row["turn_agreement"] for row in rows if row.get("turn_agreement") is not None]
    policy_agreement = [
        row["policy_agreement"] for row in rows if row.get("policy_agreement") is not None
    ]

    proposed = sum(row.get("offer_proposed", 0) for row in rows)
    blocked = sum(row.get("offer_blocked", 0) for row in rows)

    return {
        "trajectories": len(rows),
        "turns": sum(row.get("turns", 0) for row in rows),
        "scale": summarise("scale"),
        "relaxation": summarise("relaxation"),
        "repeat_within_cooldown": sum(row.get("repeat_within_cooldown", 0) for row in rows),
        "repeat_after_cooldown": sum(row.get("repeat_after_cooldown", 0) for row in rows),
        "inappropriate_offers": sum(row.get("inappropriate_offers", 0) for row in rows),
        "scale_choice_accuracy": _rate(sum(1 for value in scale_choice if value), len(scale_choice)),
        "execution_success_rate": _rate(sum(1 for value in execution if value), len(execution)),
        "policy_block_rate": _rate(blocked, proposed),
        "permission_violations": sum(row.get("permission_violations", 0) for row in rows),
        "consent_parse_errors": sum(row.get("consent_parse_errors", 0) for row in rows),
        "turn_agreement": (sum(agreement) / len(agreement)) if agreement else None,
        "policy_agreement": (
            (sum(policy_agreement) / len(policy_agreement)) if policy_agreement else None
        ),
    }


def format_report(summary: dict[str, Any]) -> str:
    """Human-readable block for the CLI and the comparison table."""

    def pct(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    scale = summary["scale"]
    relaxation = summary["relaxation"]
    lines = [
        f"trajectories={summary['trajectories']} turns={summary['turns']}",
        (
            "scale    "
            f"opportunities={scale['opportunities']} hit={pct(scale['hit_rate'])} "
            f"early={pct(scale['early_rate'])} late={pct(scale['late_rate'])} "
            f"miss={pct(scale['miss_rate'])} spurious={scale['spurious_offers']} "
            f"delay={scale['mean_delay_turns']}"
        ),
        (
            "relax    "
            f"opportunities={relaxation['opportunities']} hit={pct(relaxation['hit_rate'])} "
            f"early={pct(relaxation['early_rate'])} late={pct(relaxation['late_rate'])} "
            f"miss={pct(relaxation['miss_rate'])} spurious={relaxation['spurious_offers']} "
            f"delay={relaxation['mean_delay_turns']}"
        ),
        (
            "guards   "
            f"repeat_in_cooldown={summary['repeat_within_cooldown']} "
            f"repeat_after_cooldown={summary['repeat_after_cooldown']} "
            f"inappropriate={summary['inappropriate_offers']} "
            f"block_rate={pct(summary['policy_block_rate'])}"
        ),
        (
            "consent  "
            f"permission_violations={summary.get('permission_violations', 0)} "
            f"parse_errors={summary.get('consent_parse_errors', 0)}"
        ),
        (
            "quality  "
            f"scale_choice={pct(summary['scale_choice_accuracy'])} "
            f"execution={pct(summary['execution_success_rate'])} "
            f"turn_agreement={pct(summary['turn_agreement'])} "
            f"policy_agreement={pct(summary.get('policy_agreement'))}"
        ),
    ]
    return "\n".join(lines)
