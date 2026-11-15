"""Readout engine: restricted softmax, option-order handling, failure modes."""

from __future__ import annotations

import math

import pytest

from decision.engine.readout import (
    Readout,
    ReadoutError,
    ReadoutQuestion,
    confidence_of,
    entropy,
    softmax,
)


class StubScorer:
    """Deterministic stand-in for a causal LM."""

    def __init__(self, token_map, *, next_logits=None, seq_scores=None, label_tokens_fail=()):
        self.token_map = dict(token_map)
        self.next_logits = dict(next_logits or {})
        self.seq_scores = list(seq_scores or [])
        self.label_tokens_fail = set(label_tokens_fail)
        self.calls: list[tuple[str, list]] = []

    def label_tokens(self, label):
        if label in self.label_tokens_fail:
            raise RuntimeError(f"cannot tokenise {label}")
        return tuple(self.token_map.get(label, (hash(label) % 5000 + 10,)))

    def token_logprobs(self, prompt, token_ids):
        self.calls.append(("token", list(token_ids)))
        return [self.next_logits.get(tid, -20.0) for tid in token_ids]

    def sequence_logprobs(self, prompt, continuations):
        self.calls.append(("sequence", list(continuations)))
        if not self.seq_scores:
            return [0.0 for _ in continuations]
        return self.seq_scores[: len(continuations)]

    def describe(self):
        return {"backend": "stub"}


def _question(options=("chat", "start_scale", "end_session")):
    return ReadoutQuestion(name="action", text="下一步动作是？", options=options)


def test_softmax_is_a_distribution():
    probs = softmax([0.0, 0.0, 0.0])
    assert math.isclose(sum(probs), 1.0)
    assert all(math.isclose(p, 1 / 3, rel_tol=1e-9) for p in probs)


@pytest.mark.parametrize("bad", [[], [float("nan")], [float("inf")], [0.0, float("-inf")]])
def test_softmax_rejects_degenerate_input(bad):
    with pytest.raises(ReadoutError):
        softmax(bad)


def test_next_token_readout_reads_the_distribution():
    scorer = StubScorer(
        {"chat": (11,), "start_scale": (12,), "end_session": (13,)},
        next_logits={11: 0.0, 12: -1.0, 13: -2.0},
    )
    readout = Readout(scorer, mode="next_token")
    result = readout.decide("state", _question())

    assert result.answer == "chat"
    assert result.method == "next_token"
    assert math.isclose(sum(result.probabilities.values()), 1.0)
    assert result.probabilities["chat"] > result.probabilities["start_scale"]
    assert scorer.calls == [("token", [11, 12, 13])]


def test_prompt_lists_options_in_order_and_ends_with_a_cue():
    scorer = StubScorer({"chat": (1,), "start_scale": (2,)})
    readout = Readout(scorer, mode="next_token")
    prompt = readout.build_prompt("【本轮发言】x", _question(("chat", "start_scale")))
    assert "A. chat" in prompt
    assert "B. start_scale" in prompt
    assert prompt.rstrip().endswith("答案：")


def test_multi_token_label_falls_back_to_candidate_scoring():
    scorer = StubScorer(
        {"chat": (1, 2), "start_scale": (3,)},
        next_logits={1: 0.0, 3: 0.0},
        seq_scores=[-5.0, -0.5],
    )
    readout = Readout(scorer, mode="auto")
    result = readout.decide("state", _question(("chat", "start_scale")))
    assert result.method == "candidate"
    assert result.answer == "start_scale"
    assert "candidate_scoring" in result.notes


def test_strict_next_token_mode_refuses_multi_token_labels():
    scorer = StubScorer({"chat": (1, 2), "start_scale": (3,)})
    readout = Readout(scorer, mode="next_token")
    with pytest.raises(ReadoutError, match="single-token"):
        readout.decide("state", _question(("chat", "start_scale")))


def test_rotations_average_over_presentation_order():
    """With a scorer that always prefers the first option, rotations must cancel it."""
    order_sensitive = StubScorer({"a": (1,), "b": (2,), "c": (3,)})

    def always_first(prompt, token_ids):
        # Highest score for whichever option appears first in the prompt.
        best = token_ids[0]
        return [0.0 if tid == best else -6.0 for tid in token_ids]

    order_sensitive.token_logprobs = always_first  # type: ignore[assignment]

    single = Readout(order_sensitive, mode="next_token", rotations=1).decide("s", _question(("a", "b", "c")))
    rotated = Readout(order_sensitive, mode="next_token", rotations=3).decide("s", _question(("a", "b", "c")))

    assert single.probabilities["a"] > 0.9          # biased toward position 0
    assert rotated.rounds == 3
    for label in ("a", "b", "c"):
        assert math.isclose(rotated.probabilities[label], 1 / 3, abs_tol=1e-6)


def test_degenerate_distribution_is_rejected():
    scorer = StubScorer({"a": (1,), "b": (2,)})
    scorer.token_logprobs = lambda prompt, ids: [float("nan")] * len(ids)  # type: ignore[assignment]
    with pytest.raises(ReadoutError):
        Readout(scorer, mode="next_token").decide("s", _question(("a", "b")))


def test_scorer_returning_wrong_length_is_rejected():
    scorer = StubScorer({"a": (1,), "b": (2,)})
    scorer.token_logprobs = lambda prompt, ids: [0.0]  # type: ignore[assignment]
    with pytest.raises(ReadoutError, match="logprobs"):
        Readout(scorer, mode="next_token").decide("s", _question(("a", "b")))


def test_question_validation():
    with pytest.raises(ReadoutError):
        ReadoutQuestion(name="", text="t", options=("a", "b"))
    with pytest.raises(ReadoutError):
        ReadoutQuestion(name="q", text="t", options=("a",))
    with pytest.raises(ReadoutError):
        ReadoutQuestion(name="q", text="t", options=("a", "a"))


def test_readout_option_validation():
    scorer = StubScorer({})
    with pytest.raises(ReadoutError):
        Readout(scorer, mode="nope")
    with pytest.raises(ReadoutError):
        Readout(scorer, rotations=0)
    with pytest.raises(ReadoutError):
        Readout(scorer, temperature=0)


def test_describe_carries_no_prompt_text():
    scorer = StubScorer({"a": (1,), "b": (2,)})
    payload = Readout(scorer, mode="next_token").describe()
    assert payload["mode"] == "next_token"
    assert payload["scorer"] == {"backend": "stub"}
    assert "答案" not in str(payload)


def test_describe_survives_a_broken_scorer():
    class Broken(StubScorer):
        def describe(self):
            raise RuntimeError("boom")

    payload = Readout(Broken({})).describe()
    assert payload["scorer"] == {"status": "unavailable"}


def test_entropy_and_confidence_helpers():
    uniform = {"a": 0.5, "b": 0.5}
    peaked = {"a": 1.0, "b": 0.0}
    assert math.isclose(entropy(uniform), math.log(2))
    assert entropy(peaked) == 0.0
    assert confidence_of(uniform) == 0.5
    assert confidence_of(peaked, "b") == 0.0
    assert confidence_of({}) == 0.0


def test_decide_all_covers_every_question():
    scorer = StubScorer({"a": (1,), "b": (2,)}, next_logits={1: 0.0, 2: -1.0})
    readout = Readout(scorer, mode="next_token")
    questions = [
        ReadoutQuestion(name="q1", text="t1", options=("a", "b")),
        ReadoutQuestion(name="q2", text="t2", options=("a", "b")),
    ]
    results = readout.decide_all("s", questions)
    assert set(results) == {"q1", "q2"}
    assert all(result.answer == "a" for result in results.values())
