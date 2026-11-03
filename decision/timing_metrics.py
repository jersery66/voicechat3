"""Timing metrics for multi-turn interview trajectories.

Accuracy over single labels cannot answer the question this branch exists to
ask.  "Did the system propose an assessment at a sensible moment in this
interview?" is a question about *when*, so every metric here is computed
against an annotated time window rather than a single correct turn.

Four rules keep the numbers honest:

1. A window is a range, not a point.  Offering one turn early or late inside
   the window is not a different outcome, it is inside the window.
2. An annotation may carry more than one acceptable window, and it may be
   marked ``uncertain``.  An uncertain window is reported but excluded from the
   primary rates: a human annotator saying "maybe here" cannot be scored the
   same way as "here".
3. A refusal is a fact that must survive.  Re-offering the same instrument
   inside its cooldown is counted as a violation, not as enthusiasm.
4. Micro and macro averages are reported separately and never pooled into one
   number.  A system that acts on three easy trajectories and ignores twenty
   hard ones must not look identical to one that is mediocre everywhere.

Permission violations are derived from **events** -- an invitation id resolved
to ACCEPTED, an explicit request signal, and an execution that actually changed
runtime state -- not from the wording of ``TurnDecision.reason``.  A reason
string can be renamed; an accepted offer id and a started questionnaire cannot.

The module is pure: it takes per-turn records produced by
:mod:`decision.timing_eval` and returns dictionaries.  Nothing here runs a
model or touches runtime state.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from config import OFFER_RELAXATION_COOLDOWN_ROUNDS, OFFER_SCALE_COOLDOWN_ROUNDS

OFFER_KINDS = ("scale", "relaxation")

#: Actions that change what the participant is doing.  Anything in this set
#: needs evidence of consent; the check below reads the evidence, not a label.
EXECUTABLE_ACTIONS = frozenset({"start_scale", "recommend_relaxation"})


# ---------------------------------------------------------------------------
# Window annotations
# ---------------------------------------------------------------------------


def _normalise_windows(value: Any) -> tuple[list[tuple[int, int]], str]:
    """Read one ``windows[kind]`` annotation into ``(windows, certainty)``.

    Accepted shapes, all optional:

    ``[5, 7]``                                   one window
    ``[[5, 7], [9, 10]]``                        several acceptable windows
    ``{"any": [[5, 7]], "certainty": "uncertain"}``  explicit uncertainty

    ``certainty`` is ``"certain"`` unless the annotation says otherwise.
    """
    if value is None:
        return [], "certain"

    certainty = "certain"
    raw = value
    if isinstance(value, dict):
        certainty = str(value.get("certainty") or "certain").lower()
        raw = value.get("any") or value.get("windows") or []

    if not raw:
        return [], certainty
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and all(
        isinstance(item, int) for item in raw
    ):
        return [(int(raw[0]), int(raw[1]))], certainty

    windows: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            windows.append((int(item[0]), int(item[1])))
    return windows, certainty


def _first_offer_round(turns: Sequence[dict[str, Any]], kind: str) -> Optional[int]:
    for turn in turns:
        if any(offer.get("kind") == kind for offer in turn.get("offers", ())):
            return int(turn["round"])
    return None


def _outcome_for(
    first: Optional[int], windows: Sequence[tuple[int, int]]
) -> tuple[str, Optional[int], Optional[int]]:
    """Classify the first offer against every acceptable window.

    ``delay`` keeps its original definition -- first offer round minus the
    *start* of the earliest acceptable window -- so an early offer reads
    negative and a late one reads as "how far past the opening", which is the
    quantity the earlier reports quoted.  ``late_by`` is the stricter reading
    for a late offer: how many turns past the *end* of the last window.  Both
    are reported; neither replaces the other.
    """
    if not windows:
        return ("spurious" if first is not None else "not_applicable"), None, None
    if first is None:
        return "missed", None, None
    for start, end in windows:
        if start <= first <= end:
            return "hit", 0, 0
    earliest = min(start for start, _ in windows)
    latest = max(end for _, end in windows)
    if first < earliest:
        return "early", first - earliest, None
    if first > latest:
        return "late", first - earliest, first - latest
    # Between two acceptable windows: too late for the first, too early for the
    # next.  Reported as "late" against the window it missed, which is the
    # stricter reading.
    return "late", first - earliest, None


# ---------------------------------------------------------------------------
# Per-trajectory scoring
# ---------------------------------------------------------------------------


def evaluate_trajectory(
    trajectory: dict[str, Any], turns: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Score one trajectory against its annotated windows."""
    result: dict[str, Any] = {
        "id": trajectory.get("id"),
        "title": trajectory.get("title"),
        "split": trajectory.get("split"),
        "turns": len(turns),
        "history_mode": trajectory.get("history_mode"),
    }

    windows_by_kind = trajectory.get("windows") or {}
    for kind in OFFER_KINDS:
        windows, certainty = _normalise_windows(windows_by_kind.get(kind))
        first = _first_offer_round(turns, kind)
        prefix = kind
        primary = windows[0] if windows else None
        result[f"{prefix}_window"] = list(primary) if primary else None
        result[f"{prefix}_windows"] = [list(window) for window in windows]
        result[f"{prefix}_certainty"] = certainty if windows else None
        result[f"{prefix}_first_offer"] = first
        outcome, delay, late_by = _outcome_for(first, windows)
        result[f"{prefix}_outcome"] = outcome
        result[f"{prefix}_delay"] = delay
        result[f"{prefix}_late_by"] = late_by
        result[f"{prefix}_scored"] = bool(windows) and certainty == "certain"

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
        first_target = next(target for target in scale_offers if target)
        result["scale_choice_correct"] = first_target == gold_scale
        result["scale_chosen"] = first_target
    else:
        result["scale_choice_correct"] = None
        result["scale_chosen"] = None

    # -- execution --------------------------------------------------------
    accepted = [turn for turn in turns if turn.get("accepted_offer_kind") == "scale"]
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
    # counting it as such would flatter a timid model.  The same applies to a
    # turn that *resolves* an outstanding invitation (accepted/declined): its
    # validator reason says "offer_already_pending" because an invitation is
    # open, but the turn did not refuse anything -- it consummated one.
    proposed_offers = [
        turn
        for turn in turns
        if turn.get("intervention") in ("offer_scale", "offer_relaxation")
    ]
    executed_instead = {"start_scale", "recommend_relaxation"}
    blocked = sum(
        1
        for turn in proposed_offers
        if not turn.get("offers") and turn.get("executed") not in executed_instead
    )
    result["offer_blocked"] = blocked
    result["offer_proposed"] = len(proposed_offers)

    # -- consent handling and policy authority ----------------------------
    #
    # Evidence, in order of strength: an invitation id that this turn resolved
    # to ACCEPTED, or an explicit request from the participant.  A turn that
    # executed an activity with neither is a permission violation regardless of
    # what its reason string says.
    violations = 0
    for turn in turns:
        if turn.get("executed") not in EXECUTABLE_ACTIONS:
            continue
        if turn.get("accepted_offer_id") or turn.get("explicit_request"):
            continue
        violations += 1
    result["permission_violations"] = violations
    result["consent_parse_errors"] = sum(
        1 for turn in turns if turn.get("policy_reason") == "offer_answer_ambiguous"
    )

    # -- agreement with the turn annotation -------------------------------
    #
    # Two different agreements, deliberately kept apart: ``intervention`` is what
    # the decision model proposed, ``action`` is what TurnPolicy finally did.
    # A model that proposes well and a policy that refuses well are both scored,
    # and neither can hide behind the other.
    graded = [turn for turn in turns if turn.get("expect")]
    if graded:
        agree = sum(1 for turn in graded if turn.get("intervention") == turn.get("expect"))
        result["turn_agreement"] = agree / len(graded)
        result["turn_graded"] = len(graded)
        result["turn_agreed"] = agree
    else:
        result["turn_agreement"] = None
        result["turn_graded"] = 0
        result["turn_agreed"] = 0

    graded_actions = [turn for turn in turns if turn.get("expect_action")]
    if graded_actions:
        agree_actions = sum(
            1 for turn in graded_actions if turn.get("policy_action") == turn.get("expect_action")
        )
        result["policy_agreement"] = agree_actions / len(graded_actions)
        result["policy_graded"] = len(graded_actions)
        result["policy_agreed"] = agree_actions
    else:
        result["policy_agreement"] = None
        result["policy_graded"] = 0
        result["policy_agreed"] = 0

    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return (numerator / denominator) if denominator else None


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return (sum(present) / len(present)) if present else None


def aggregate(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-trajectory results into headline, micro and macro views."""
    rows = list(results)

    def scored_rows(kind: str) -> list[dict[str, Any]]:
        return [row for row in rows if row.get(f"{kind}_scored")]

    def summarise(kind: str) -> dict[str, Any]:
        scored = scored_rows(kind)
        values = [row[f"{kind}_outcome"] for row in scored]
        total = len(values)
        hits = values.count("hit")
        delays = [
            row[f"{kind}_delay"]
            for row in scored
            if row.get(f"{kind}_outcome") == "hit" and row.get(f"{kind}_delay") is not None
        ]
        spurious = sum(
            1
            for row in rows
            if row.get(f"{kind}_outcome") == "spurious"
            and row.get(f"{kind}_certainty") in (None, "certain")
        )
        uncertain_spurious = sum(
            1
            for row in rows
            if row.get(f"{kind}_outcome") == "spurious"
            and row.get(f"{kind}_certainty") == "uncertain"
        )
        return {
            "opportunities": total,
            "hit_rate": _rate(hits, total),
            "early_rate": _rate(values.count("early"), total),
            "late_rate": _rate(values.count("late"), total),
            "miss_rate": _rate(values.count("missed"), total),
            "spurious_offers": spurious,
            "uncertain_spurious": uncertain_spurious,
            "mean_delay_turns": (sum(delays) / len(delays)) if delays else None,
            "uncertain_opportunities": sum(
                1 for row in rows if row.get(f"{kind}_certainty") == "uncertain"
            ),
        }

    scale_choice = [row["scale_choice_correct"] for row in rows if row.get("scale_choice_correct") is not None]
    execution = [row["execution_success"] for row in rows if row.get("execution_success") is not None]
    agreement = [row["turn_agreement"] for row in rows if row.get("turn_agreement") is not None]
    policy_agreement = [
        row["policy_agreement"] for row in rows if row.get("policy_agreement") is not None
    ]

    proposed = sum(row.get("offer_proposed", 0) for row in rows)
    blocked = sum(row.get("offer_blocked", 0) for row in rows)
    turn_graded = sum(row.get("turn_graded", 0) for row in rows)
    turn_agreed = sum(row.get("turn_agreed", 0) for row in rows)
    policy_graded = sum(row.get("policy_graded", 0) for row in rows)
    policy_agreed = sum(row.get("policy_agreed", 0) for row in rows)

    scale = summarise("scale")
    relaxation = summarise("relaxation")

    summary = {
        "trajectories": len(rows),
        "turns": sum(row.get("turns", 0) for row in rows),
        "scale": scale,
        "relaxation": relaxation,
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

    # -- micro: pooled over turns, not over trajectories -------------------
    #
    # Every turn and every window opportunity counts once, so a long interview
    # contributes as much as it actually contains.
    summary["micro"] = {
        "turns_graded": turn_graded,
        "turn_agreement": _rate(turn_agreed, turn_graded),
        "actions_graded": policy_graded,
        "policy_agreement": _rate(policy_agreed, policy_graded),
        "scale_opportunities": scale["opportunities"],
        "scale_hit": scale["hit_rate"],
        "relaxation_opportunities": relaxation["opportunities"],
        "relaxation_hit": relaxation["hit_rate"],
    }

    # -- macro: every trajectory counts once -------------------------------
    #
    # For single-window annotations this equals the micro rate; they diverge as
    # soon as annotations carry several acceptable windows or trajectories
    # differ in length, and that divergence is the point of reporting both.
    summary["macro"] = {
        "turn_agreement": summary["turn_agreement"],
        "policy_agreement": summary["policy_agreement"],
        "scale_hit": _mean(
            [1.0 if row["scale_outcome"] == "hit" else 0.0 for row in scored_rows("scale")]
        ),
        "relaxation_hit": _mean(
            [
                1.0 if row["relaxation_outcome"] == "hit" else 0.0
                for row in scored_rows("relaxation")
            ]
        ),
        "scale_delay": scale["mean_delay_turns"],
        "relaxation_delay": relaxation["mean_delay_turns"],
    }

    # -- the four stages, reported apart -----------------------------------
    #
    # A high final accuracy can hide a model that proposes nonsense and a policy
    # that refuses all of it.  These blocks make that impossible to hide; the
    # per-turn distribution behind them is built by :func:`aggregate_turns`.
    #
    # ``policy_offered`` is filled by the caller that still holds the turn rows
    # (``run_trajectories``); this function only sees per-trajectory scores.
    summary["stage"] = {
        "jev_proposed_offer": proposed,
        "validator_blocked": blocked,
        "permission_violations": summary["permission_violations"],
    }

    return summary


def aggregate_turns(results: Iterable[dict[str, Any]], turns_by_id: dict[str, Any]) -> dict[str, Any]:
    """Stage distribution over individual turns (validator / policy / execution).

    Kept separate from :func:`aggregate` because it needs the raw turn rows,
    which the per-trajectory scores deliberately do not carry.
    """
    stage = {
        "proposed": {"continue_interview": 0, "offer_scale": 0, "offer_relaxation": 0, "clarify_first": 0},
        "validator": {"accepted": 0, "rejected": 0},
        "reject_reasons": {},
        "policy_actions": {},
        "executed": {},
    }
    for _trajectory_id, turns in turns_by_id.items():
        for turn in turns:
            intervention = turn.get("intervention")
            if intervention in stage["proposed"]:
                stage["proposed"][intervention] += 1
            if turn.get("validate_accepted"):
                stage["validator"]["accepted"] += 1
            else:
                stage["validator"]["rejected"] += 1
                reason = str(turn.get("validate_reason") or "unknown")
                stage["reject_reasons"][reason] = stage["reject_reasons"].get(reason, 0) + 1
            action = str(turn.get("policy_action") or "unknown")
            stage["policy_actions"][action] = stage["policy_actions"].get(action, 0) + 1
            executed = str(turn.get("executed") or "unknown")
            stage["executed"][executed] = stage["executed"].get(executed, 0) + 1
    return stage


def format_report(summary: dict[str, Any]) -> str:
    """Human-readable block for the CLI and the comparison table."""

    def pct(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    scale = summary["scale"]
    relaxation = summary["relaxation"]
    micro = summary.get("micro") or {}
    macro = summary.get("macro") or {}
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
        (
            "micro    "
            f"turn_agreement={pct(micro.get('turn_agreement'))} "
            f"(n={micro.get('turns_graded', 0)}) "
            f"policy_agreement={pct(micro.get('policy_agreement'))} "
            f"(n={micro.get('actions_graded', 0)})"
        ),
        (
            "macro    "
            f"turn_agreement={pct(macro.get('turn_agreement'))} "
            f"policy_agreement={pct(macro.get('policy_agreement'))} "
            f"scale_hit={pct(macro.get('scale_hit'))} "
            f"relax_hit={pct(macro.get('relaxation_hit'))}"
        ),
    ]
    return "\n".join(lines)
