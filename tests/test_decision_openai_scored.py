"""Contract tests for ``openai_scored`` -- any served model as the decider.

The readout is the part worth pinning.  A letter-masked sampler still reports
the *unmasked* top-logprobs, so a naive "argmax of the first token" would read
whitespace and punctuation as option mass.  These tests hold the line on:

* only option letters compete, and they are renormalised to a distribution;
* the winning letter maps back to the option *text*, because everything
  downstream normalises option text into labels;
* a missing endpoint, a remote endpoint, or a response without probabilities
  fails loudly instead of producing a decision that looks valid.
"""

from __future__ import annotations

import json

import pytest

from decision.contracts import DecisionBackend, DecisionRequest
from decision.providers.base import DecisionBackendUnavailable, DecisionProviderError
from decision.providers.openai_scored import (
    OpenAIScoredDecisionProvider,
    _completion_url,
    _letter_probs,
)


def _request() -> DecisionRequest:
    return DecisionRequest(
        user_text="最近老是睡不着，三四点就醒了，白天上班特别累",
        session_state="interview",
        turn_index=2,
        active_scale=None,
        game_active=False,
        recent_history="咨询师：最近睡得怎么样？",
    )


def _probs_payload(top_letter: str, letters: str, *, extra: bool = True) -> dict:
    """Build a llama.cpp-shaped response: chosen letter masked, rest unmasked."""
    logprobs = [{"token": "\n\n", "logprob": -0.01}] if extra else []
    for letter in letters:
        logprob = -0.05 if letter == top_letter else -4.0
        logprobs.append({"token": letter, "logprob": logprob})
    return {
        "content": top_letter,
        "completion_probabilities": [{"token": top_letter, "logprob": -0.05, "top_logprobs": logprobs}],
    }


def _poster(letters: str, top: str = "A"):
    calls = []

    def post(url, payload, timeout):
        calls.append((url, payload))
        return _probs_payload(top, letters)

    post.calls = calls
    return post


# -- readout ----------------------------------------------------------------


def test_letter_probs_ignore_non_letter_tokens():
    first = {
        "top_logprobs": [
            {"token": "\n\n", "logprob": -0.001},
            {"token": "A", "logprob": -2.0},
            {"token": "B", "logprob": -0.7},
            {"token": "你", "logprob": -0.2},
        ]
    }
    probs = _letter_probs(first, 2)
    assert set(probs) == {"A", "B"}
    assert probs["B"] > probs["A"]
    assert pytest.approx(sum(probs.values()), abs=1e-9) == 1.0


def test_letter_probs_empty_when_nothing_usable():
    assert _letter_probs({"top_logprobs": [{"token": "x", "logprob": -1.0}]}, 3) == {}


@pytest.mark.parametrize(
    "given,expected",
    [
        ("http://127.0.0.1:8001", "http://127.0.0.1:8001/completion"),
        ("http://127.0.0.1:8001/", "http://127.0.0.1:8001/completion"),
        ("http://127.0.0.1:8001/v1", "http://127.0.0.1:8001/completion"),
        ("http://127.0.0.1:8001/completion", "http://127.0.0.1:8001/completion"),
    ],
)
def test_completion_url_normalisation(given, expected):
    assert _completion_url(given) == expected


# -- provider ---------------------------------------------------------------


def test_decide_maps_letters_back_to_option_text():
    provider = OpenAIScoredDecisionProvider(
        endpoint="http://127.0.0.1:8001",
        poster=_poster("ABCDEFGH", top="A"),
    )
    decision = provider.decide(_request())

    # Every question answered "A", so each label is the first option of its set.
    assert decision.focus == "sleep"
    assert decision.stance == "probe"
    assert decision.strategy == "explore"
    assert decision.provider == "openai_scored"
    assert decision.route == "one_forward"
    assert pytest.approx(sum(decision.probabilities.values()), abs=1e-6) == 1.0
    assert decision.confidence > 0.5
    # The per-question distributions survive for the journal and for debugging.
    assert "focus" in decision.per_question
    assert any(key.startswith("Sleep") for key in decision.per_question["focus"])


def test_decide_sends_one_constrained_request_per_question():
    poster = _poster("ABCDEFGH", top="A")
    provider = OpenAIScoredDecisionProvider(endpoint="http://127.0.0.1:8001", poster=poster)
    provider.decide(_request())

    assert len(poster.calls) == len(provider.questions)
    for _url, payload in poster.calls:
        assert payload["n_predict"] == 1
        assert payload["temperature"] == 0.0
        assert payload["grammar"].startswith("root ::=")
        assert "只回答一个字母" in payload["prompt"]


def test_missing_probabilities_is_a_backend_failure():
    provider = OpenAIScoredDecisionProvider(
        endpoint="http://127.0.0.1:8001",
        poster=lambda url, payload, timeout: {"content": "A"},
    )
    with pytest.raises(DecisionBackendUnavailable):
        provider.decide(_request())


def test_remote_endpoint_is_refused():
    with pytest.raises(DecisionProviderError):
        OpenAIScoredDecisionProvider(endpoint="http://10.0.0.7:8001")


def test_missing_endpoint_is_a_backend_failure():
    with pytest.raises(DecisionBackendUnavailable):
        OpenAIScoredDecisionProvider(endpoint=None)


def test_describe_reports_the_model_it_will_drive():
    provider = OpenAIScoredDecisionProvider(
        endpoint="http://127.0.0.1:8001",
        model="qwen3-4b-instruct-2507",
        poster=_poster("ABC", top="A"),
    )
    detail = provider.describe()
    assert detail["provider"] == "openai_scored"
    assert detail["model"] == "qwen3-4b-instruct-2507"
    assert detail["url"].endswith("/completion")
    assert detail["grammar"] is True


# -- wiring -----------------------------------------------------------------


def test_registry_builds_the_provider_from_config():
    from dataclasses import replace

    from decision.registry import build_decision_coordinator
    from deployment.runtime_config import DecisionConfig

    config = DecisionConfig(
        mode="jev",
        backend=DecisionBackend.OPENAI_SCORED.value,
        endpoint="http://127.0.0.1:8001",
        timeout=30.0,
    )
    coordinator = build_decision_coordinator(decision_config=config, agent=None)
    assert coordinator.primary.name == "openai_scored"
    assert coordinator.primary.is_available()
    assert coordinator.primary.describe()["endpoint"] == "http://127.0.0.1:8001"


_BASE_CONFIG = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: contract-model
  timeout: 90
  max_tokens: 256
  temperature: 0.7
  top_p: 0.9
  enable_thinking: false
  request_mode: chat
  system_role_mode: native
agent:
  enabled: false
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime:
  strict_preflight: true
  immutable_contract: true
"""


def _load(tmp_path, decision_yaml: str):
    from deployment.runtime_config import load_runtime_config

    path = tmp_path / "runtime.yaml"
    path.write_text(_BASE_CONFIG + "decision:\n" + decision_yaml, encoding="utf-8")
    return load_runtime_config(path)


def test_runtime_config_requires_an_endpoint_for_openai_scored(tmp_path):
    with pytest.raises(Exception) as exc:
        _load(tmp_path, "  mode: jev\n  backend: openai_scored\n")
    assert "openai_scored" in str(exc.value)


def test_runtime_config_accepts_use_grammar(tmp_path):
    config = _load(
        tmp_path,
        "  mode: jev\n"
        "  backend: openai_scored\n"
        "  endpoint: http://127.0.0.1:8001\n"
        "  use_grammar: false\n",
    ).decision
    assert config.use_grammar is False
    assert config.endpoint == "http://127.0.0.1:8001"
    assert config.backend == "openai_scored"
