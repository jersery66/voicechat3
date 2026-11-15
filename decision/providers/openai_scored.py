"""Any OpenAI-compatible chat model as a scored decision backend.

Why this exists
---------------
``tacit`` and ``anyjev_decider`` are tied to their own readouts: Tacit reads a
distilled checkpoint's label distribution in one prefill, the AnyJev gateway
speaks ``/v1/decide``.  Neither can drive a plain served model, so "swap in a
bigger decision model" used to mean writing a new adapter.

This backend does the scoring itself, over the same question set every other
provider uses:

1. render ``state + question + lettered options`` (A, B, C ...);
2. constrain the sampler to a single letter (llama.cpp ``grammar``);
3. read the first-token distribution over those letters;
4. renormalise it into a probability over options.

The result is a ``RawDecision`` shaped exactly like Tacit's, so the validator,
the abstain threshold, the posture/focus resolution and the journal all work
unchanged.  The model behind it is a config field, not a code change.

Loopback-only by default, for the same reason the other providers are: the
state text contains interview transcript.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from decision.contracts import DecisionRequest, RawDecision
from decision.providers.base import (
    DecisionBackendUnavailable,
    DecisionTransportError,
    QuestionSpec,
    build_questions,
    build_scale_choice_question,
    build_state_text,
    compose_decision,
    require_loopback,
)
from decision.timing import InterventionIntent, normalize_intervention

_LETTERS = "ABCDEFGHIJKLMNOP"


def _completion_url(endpoint: str) -> str:
    """Accept ``http://host:port``, ``.../v1`` or a full ``/completion`` URL."""
    base = str(endpoint).strip().rstrip("/")
    if base.endswith("/completion"):
        return base
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base + "/completion"


def _letter_probs(first: dict[str, Any], count: int) -> dict[str, float]:
    """Renormalise the first-token distribution over the option letters.

    The sampler is grammar-masked, so a bare letter is what gets emitted, but
    ``top_logprobs`` is the *unmasked* distribution: other tokens still appear
    and would otherwise be read as "the model is unsure".  Only the letters
    compete here, which is what makes the result a distribution over options
    rather than over the vocabulary.
    """
    best: dict[str, float] = {}
    for item in first.get("top_logprobs") or []:
        token = str(item.get("token", ""))
        if len(token) != 1 or token not in _LETTERS[:count]:
            continue
        logprob = float(item.get("logprob", -1e9))
        if token not in best or logprob > best[token]:
            best[token] = logprob
    if not best:
        return {}
    top = max(best.values())
    weights = {letter: math.exp(value - top) for letter, value in best.items()}
    total = sum(weights.values())
    return {letter: weight / total for letter, weight in weights.items()}


class OpenAIScoredDecisionProvider:
    """Score the shared question set with any llama.cpp/vLLM-style endpoint."""

    name = "openai_scored"

    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
        allow_remote_endpoint: bool = False,
        use_grammar: bool = True,
        n_probs: int = 200,
        template: str = "en",
        poster: Optional[Callable[[str, dict[str, Any], float], dict[str, Any]]] = None,
    ) -> None:
        if not endpoint:
            raise DecisionBackendUnavailable(
                "openai_scored needs decision.endpoint (e.g. http://127.0.0.1:8001)"
            )
        self.url = _completion_url(require_loopback(endpoint, allow_remote=allow_remote_endpoint))
        self.endpoint = endpoint
        self.model = model
        self.timeout = float(timeout)
        self.use_grammar = bool(use_grammar)
        self.n_probs = int(n_probs)
        self.template = str(template or "en").strip().lower()
        self.questions = build_questions(self.template)
        self._poster = poster

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        return self._poster is not None or self.url.startswith("http")

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "endpoint": self.endpoint,
            "url": self.url,
            "model": self.model,
            "grammar": self.use_grammar,
            "template": self.template,
        }

    def decide(self, request: DecisionRequest) -> RawDecision:
        state = build_state_text(request)
        started = time.perf_counter()
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        for spec in self.questions:
            answers[spec.name] = self._ask(state, spec)

        intervention = normalize_intervention(answers["intervention"][0])
        scale_choice: Optional[tuple[str, dict[str, float]]] = None
        if intervention == InterventionIntent.OFFER_SCALE.value:
            spec = build_scale_choice_question(request)
            if spec is not None:
                scale_choice = self._ask(state, spec)
                answers[spec.name] = scale_choice

        return compose_decision(
            provider=self.name,
            request=request,
            answers=answers,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            route="one_forward",
            reason="openai_scored",
            scale_choice=scale_choice,
        )

    # -- transport ---------------------------------------------------------

    def _ask(self, state: str, spec: QuestionSpec) -> tuple[str, dict[str, float]]:
        options = list(spec.options)
        prompt = self._prompt(state, spec, options)
        payload: dict[str, Any] = {
            "prompt": prompt,
            "n_predict": 1,
            "temperature": 0.0,
            "n_probs": self.n_probs,
            "cache_prompt": False,
        }
        if self.use_grammar:
            payload["grammar"] = "root ::= " + " | ".join(
                f'"{_LETTERS[i]}"' for i in range(len(options))
            )
        response = self._post(payload)
        first = (response.get("completion_probabilities") or [{}])[0]
        letter_probs = _letter_probs(first, len(options))
        if not letter_probs:
            raise DecisionBackendUnavailable(
                f"openai_scored got no option-letter probabilities for {spec.name!r}; "
                "the endpoint must be llama.cpp (n_probs) or vLLM with prompt_logprobs"
            )
        probabilities = {
            options[_LETTERS.index(letter)]: value for letter, value in letter_probs.items()
        }
        answer = max(probabilities.items(), key=lambda kv: kv[1])[0]
        return answer, probabilities

    def _prompt(self, state: str, spec: QuestionSpec, options: list[str]) -> str:
        lines = [state, "", spec.text, ""]
        for index, option in enumerate(options):
            lines.append(f"{_LETTERS[index]}. {option}")
        lines += ["", "只回答一个字母，不要解释。"]
        return "\n".join(lines)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._poster is not None:
            return self._poster(self.url, payload, self.timeout)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise DecisionTransportError(
                f"openai_scored could not reach {self.url}: {exc}"
            ) from exc
        except TimeoutError as exc:
            raise DecisionTransportError(
                f"openai_scored timed out after {self.timeout}s at {self.url}"
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DecisionTransportError(
                f"openai_scored got non-JSON from {self.url}: {raw[:120]!r}"
            ) from exc
