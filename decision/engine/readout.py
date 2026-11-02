"""Pure readout logic, independent of torch.

The heavy lifting -- one forward pass, log-softmax over the vocabulary -- lives
in :mod:`decision.engine.scorer`.  This module only decides *which* numbers to
read and how to normalise them, which means it can be unit-tested against a
hand-written fake scorer with no GPU and no checkpoint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

#: Rounding guard: a probability vector must sum to 1 within this tolerance.
SUM_TOLERANCE = 5e-3


class ReadoutError(RuntimeError):
    """The readout could not produce a trustworthy distribution."""


@dataclass(frozen=True)
class ReadoutQuestion:
    """One categorical question and its closed option set."""

    name: str
    text: str
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ReadoutError("question name must be non-empty")
        if len(self.options) < 2:
            raise ReadoutError(f"question {self.name!r} needs at least two options")
        if len(set(self.options)) != len(self.options):
            raise ReadoutError(f"question {self.name!r} has duplicate options")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "text": self.text, "options": list(self.options)}


@dataclass(frozen=True)
class LabelTokens:
    """How one option label tokenises, as seen by the model."""

    label: str
    token_ids: tuple[int, ...]

    @property
    def single_token(self) -> bool:
        return len(self.token_ids) == 1


@dataclass(frozen=True)
class ReadoutResult:
    """One question's probability vector plus the metadata needed to audit it."""

    name: str
    answer: str
    probabilities: dict[str, float]
    method: str = "next_token"
    rounds: int = 1
    entropy: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "answer": self.answer,
            "probabilities": dict(self.probabilities),
            "method": self.method,
            "rounds": self.rounds,
            "entropy": round(self.entropy, 6),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Scorer contract
# ---------------------------------------------------------------------------


class Scorer(Protocol):
    """Minimal surface the readout needs from a model backend."""

    def label_tokens(self, label: str) -> tuple[int, ...]:
        """Token ids for ``label`` when appended as a continuation."""

    def token_logprobs(self, prompt: str, token_ids: Sequence[int]) -> list[float]:
        """log P(token_id | prompt) for each candidate next token."""

    def sequence_logprobs(self, prompt: str, continuations: Sequence[str]) -> list[float]:
        """log P(continuation | prompt) for each continuation string."""

    def describe(self) -> dict[str, Any]:
        """Metadata for logs; must never contain interview text."""


# ---------------------------------------------------------------------------
# Numerics
# ---------------------------------------------------------------------------


def softmax(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax; rejects non-finite input."""
    values = [float(v) for v in logits]
    if not values:
        raise ReadoutError("softmax over an empty vector")
    if any(v != v or v in (math.inf, -math.inf) for v in values):
        raise ReadoutError("softmax over non-finite logits")
    peak = max(values)
    exps = [math.exp(v - peak) for v in values]
    total = sum(exps)
    if total <= 0 or total != total:
        raise ReadoutError("softmax normaliser is degenerate")
    return [v / total for v in exps]


def entropy(probabilities: Mapping[str, float]) -> float:
    total = 0.0
    for value in probabilities.values():
        if value > 0:
            total -= value * math.log(value)
    return total


def _argmax(probabilities: Mapping[str, float]) -> str:
    # Ties resolve by the declared option order, never by dict ordering luck.
    best_label = ""
    best_value = -1.0
    for label, value in probabilities.items():
        if value > best_value:
            best_label, best_value = label, value
    return best_label


# ---------------------------------------------------------------------------
# Readout
# ---------------------------------------------------------------------------


class Readout:
    """Restricted-softmax readout over closed option sets.

    Parameters
    ----------
    scorer:
        Anything satisfying :class:`Scorer`.
    mode:
        ``"next_token"`` reads P(first token of label | prompt) and renormalises
        across options -- the literal "read the next-token distribution" that a
        Jev-style decision means.  ``"candidate"`` scores each label as a full
        continuation and softmaxes; used automatically when a label is not a
        single token.  ``"auto"`` prefers next_token and falls back per question.
    rotations:
        0 keeps the declared option order and does a single pass.  ``k > 0``
        averages ``k`` cyclic presentation orders (mapped back to the declared
        labels) to dampen position bias.  Cost is ``k`` extra passes.
    """

    def __init__(
        self,
        scorer: Scorer,
        *,
        mode: str = "auto",
        rotations: int = 1,
        temperature: float = 1.0,
    ) -> None:
        if mode not in {"auto", "next_token", "candidate"}:
            raise ReadoutError(f"unknown readout mode {mode!r}")
        if rotations < 1:
            raise ReadoutError("rotations must be >= 1")
        if temperature <= 0:
            raise ReadoutError("temperature must be positive")
        self.scorer = scorer
        self.mode = mode
        self.rotations = int(rotations)
        self.temperature = float(temperature)

    # -- prompt shaping ---------------------------------------------------

    def build_prompt(self, state_text: str, question: ReadoutQuestion,
                     options: Optional[Sequence[str]] = None) -> str:
        """Deterministic prompt: state, question, ordered options, answer cue."""
        chosen = tuple(options) if options is not None else question.options
        lines = [state_text.strip(), "", question.text.strip()]
        for index, option in enumerate(chosen):
            lines.append(f"{chr(ord('A') + index)}. {option}")
        lines.append("")
        lines.append("答案：")
        return "\n".join(lines)

    # -- one question -----------------------------------------------------

    def decide(self, state_text: str, question: ReadoutQuestion) -> ReadoutResult:
        declared = tuple(question.options)
        totals = {label: 0.0 for label in declared}
        notes: list[str] = []
        method = "next_token"

        for rotation in range(self.rotations):
            order = _rotate(declared, rotation)
            prompt = self.build_prompt(state_text, question, order)
            per_label, used_method, note = self._read_once(prompt, order)
            if note:
                notes.append(note)
            method = used_method
            for label, value in per_label.items():
                totals[label] += value

        combined = {label: value / self.rotations for label, value in totals.items()}
        combined = _renormalise(combined, question.name)
        answer = _argmax(combined)
        return ReadoutResult(
            name=question.name,
            answer=answer,
            probabilities=combined,
            method=method,
            rounds=self.rotations,
            entropy=entropy(combined),
            notes=tuple(dict.fromkeys(notes)),
        )

    def _read_once(self, prompt: str, order: Sequence[str]) -> tuple[dict[str, float], str, str]:
        """Return (probabilities keyed by label in ``order``, method, note)."""
        tokens = {label: self.scorer.label_tokens(label) for label in order}
        single = all(len(ids) == 1 for ids in tokens.values())

        if self.mode in {"auto", "next_token"} and single:
            ids = [tokens[label][0] for label in order]
            logprobs = self.scorer.token_logprobs(prompt, ids)
            if len(logprobs) != len(ids):
                raise ReadoutError(
                    f"scorer returned {len(logprobs)} logprobs for {len(ids)} options"
                )
            scaled = [value / self.temperature for value in logprobs]
            probs = softmax(scaled)
            return {label: p for label, p in zip(order, probs)}, "next_token", ""

        if self.mode == "next_token" and not single:
            raise ReadoutError(
                "next_token readout requires single-token labels; "
                f"multi-token labels: {[l for l, ids in tokens.items() if len(ids) > 1]}"
            )

        logprobs = self.scorer.sequence_logprobs(prompt, list(order))
        if len(logprobs) != len(order):
            raise ReadoutError(
                f"scorer returned {len(logprobs)} scores for {len(order)} options"
            )
        scaled = [value / self.temperature for value in logprobs]
        probs = softmax(scaled)
        note = "candidate_scoring" if self.mode == "auto" else ""
        return {label: p for label, p in zip(order, probs)}, "candidate", note

    # -- all questions ----------------------------------------------------

    def decide_all(
        self, state_text: str, questions: Sequence[ReadoutQuestion]
    ) -> dict[str, ReadoutResult]:
        results: dict[str, ReadoutResult] = {}
        for question in questions:
            results[question.name] = self.decide(state_text, question)
        return results

    def describe(self) -> dict[str, Any]:
        payload = {
            "mode": self.mode,
            "rotations": self.rotations,
            "temperature": self.temperature,
        }
        try:
            payload["scorer"] = self.scorer.describe()
        except Exception:  # a broken scorer must not break introspection
            payload["scorer"] = {"status": "unavailable"}
        return payload


def _rotate(items: Sequence[str], offset: int) -> tuple[str, ...]:
    if not items:
        return ()
    step = offset % len(items)
    if step == 0:
        return tuple(items)
    return tuple(items[step:]) + tuple(items[:step])


def _renormalise(probabilities: Mapping[str, float], name: str) -> dict[str, float]:
    total = sum(probabilities.values())
    if not math.isfinite(total) or total <= 0:
        raise ReadoutError(f"question {name!r} produced a degenerate distribution")
    out = {label: value / total for label, value in probabilities.items()}
    if abs(sum(out.values()) - 1.0) > SUM_TOLERANCE:
        raise ReadoutError(f"question {name!r} probabilities do not sum to 1")
    if any(value < 0 for value in out.values()):
        raise ReadoutError(f"question {name!r} produced a negative probability")
    return out


def confidence_of(probabilities: Mapping[str, float],
                  label: Optional[str] = None) -> float:
    """Probability mass of the winning label (or of ``label``)."""
    if not probabilities:
        return 0.0
    if label is None:
        return float(max(probabilities.values()))
    return float(probabilities.get(label, 0.0))
