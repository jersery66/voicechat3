"""OpenAI-compatible streaming client for a separately deployed vLLM server."""

from __future__ import annotations

from typing import Iterator, Sequence

from openai import OpenAI


class VLLMOpenAIClient:
    """Dialogue adapter for vLLM's OpenAI-compatible `/v1` API.

    vLLM runs outside the desktop application. This adapter intentionally has
    no import dependency on vLLM itself, which keeps Windows development and
    Linux/A100 deployment cleanly separated.
    """

    def __init__(self, *, model: str, base_url: str, api_key: str = "EMPTY",
                 timeout: float = 120.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
        )

    def stream_reply(self, *, user_text: str, system_context: str = "") -> Iterator[str]:
        messages = []
        if system_context:
            messages.append({"role": "system", "content": system_context})
        messages.append({"role": "user", "content": user_text})
        yield from self.stream_messages(messages=messages)

    def stream_messages(self, *, messages: Sequence[dict[str, str]]) -> Iterator[str]:
        """Stream a complete OpenAI chat message list through vLLM.

        Keeping the message list intact is essential: the legacy service
        retains multi-turn counselling context, so a vLLM adapter must not
        reduce every request to only the most recent utterance.
        """
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            stream=True,
            temperature=0.35,
            top_p=0.8,
            max_tokens=1024,
            stop=["User:", "Visitor:", "用户:", "来访者:", "Human:"],
        )
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                yield content
