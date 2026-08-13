"""OpenAI-compatible streaming client for a separately deployed vLLM server."""

from __future__ import annotations

from typing import Any, Iterator, Sequence

from openai import OpenAI


class VLLMOpenAIClient:
    """Dialogue adapter for vLLM's OpenAI-compatible `/v1` API.

    vLLM runs outside the desktop application. This adapter intentionally has
    no import dependency on vLLM itself, which keeps Windows development and
    Linux/A100 deployment cleanly separated.
    """

    def __init__(self, *, model: str, base_url: str, api_key: str = "EMPTY",
                 timeout: float = 120.0, request_mode: str = "chat",
                 system_role_mode: str = "native", max_tokens: int = 1024) -> None:
        if request_mode not in {"chat", "completion"}:
            raise ValueError(
                "request_mode must be either 'chat' or 'completion', "
                f"got {request_mode!r}"
            )
        if system_role_mode not in {"native", "prepend_user"}:
            raise ValueError(
                "system_role_mode must be either 'native' or 'prepend_user', "
                f"got {system_role_mode!r}"
            )
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.request_mode = request_mode
        self.system_role_mode = system_role_mode
        self.max_tokens = max_tokens
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
        )

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        """Read a field from either an OpenAI SDK object or a test dict."""
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def list_model_ids(self) -> list[str]:
        """Return the model IDs currently exposed by the selected vLLM server."""
        response = self._client.models.list()
        data = self._field(response, "data", []) or []
        return [
            model_id
            for item in data
            if (model_id := self._field(item, "id"))
        ]

    @staticmethod
    def _completion_prompt(messages: Sequence[dict[str, str]]) -> str:
        """Render OpenAI-style messages for base models without a chat template.

        This route is profile-selected for the local Gemma smoke server only.
        Production instruction models keep their native chat template and use
        the regular OpenAI chat endpoint.
        """
        role_names = {
            "system": "System",
            "user": "User",
            "assistant": "Assistant",
        }
        turns = []
        for message in messages:
            role = str(message.get("role", "user")).strip().lower()
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            turns.append(f"{role_names.get(role, role.title())}: {content}")
        turns.append("Assistant:")
        return "\n\n".join(turns)

    def _uses_completion_endpoint(self) -> bool:
        return getattr(self, "request_mode", "chat") == "completion"

    def _prepare_chat_messages(self, messages: Sequence[dict[str, str]]) -> list[dict[str, str]]:
        """Adapt a system prompt only for templates that cannot accept it."""
        prepared = [dict(message) for message in messages]
        if getattr(self, "system_role_mode", "native") != "prepend_user":
            return prepared
        if not prepared or prepared[0].get("role") != "system":
            return prepared
        system_text = str(prepared.pop(0).get("content", "")).strip()
        if not prepared or prepared[0].get("role") != "user":
            raise ValueError(
                "prepend_user system mode requires the first conversational message to be a user turn"
            )
        if system_text:
            user_text = str(prepared[0].get("content", "")).strip()
            prepared[0]["content"] = f"{system_text}\n\n{user_text}".strip()
        return prepared

    def _max_tokens(self, requested: int | None = None) -> int:
        configured = int(getattr(self, "max_tokens", 1024))
        if requested is None:
            return configured
        return min(max(1, int(requested)), configured)

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
        if self._uses_completion_endpoint():
            stream = self._client.completions.create(
                model=self.model,
                prompt=self._completion_prompt(messages),
                stream=True,
                temperature=0.35,
                top_p=0.8,
                max_tokens=self._max_tokens(),
                stop=["User:", "Visitor:", "用户:", "来访者:", "Human:"],
            )
            for chunk in stream:
                choices = self._field(chunk, "choices", []) or []
                if not choices:
                    continue
                content = self._field(choices[0], "text", "") or ""
                if content:
                    yield content
            return

        stream = self._client.chat.completions.create(
            model=self.model,
            messages=self._prepare_chat_messages(messages),
            stream=True,
            temperature=0.35,
            top_p=0.8,
            max_tokens=self._max_tokens(),
            stop=["User:", "Visitor:", "用户:", "来访者:", "Human:"],
        )
        for chunk in stream:
            choices = self._field(chunk, "choices", []) or []
            if not choices:
                continue
            delta = self._field(choices[0], "delta")
            content = self._field(delta, "content") if delta is not None else None
            if content:
                yield content

    def complete_messages(self, *, messages: Sequence[dict[str, str]],
                          max_tokens: int) -> str:
        """Return a short non-streaming completion without mutating dialogue state."""
        if self._uses_completion_endpoint():
            completion = self._client.completions.create(
                model=self.model,
                prompt=self._completion_prompt(messages),
                stream=False,
                temperature=0.35,
                top_p=0.8,
                max_tokens=self._max_tokens(max_tokens),
                stop=["User:", "Visitor:", "用户:", "来访者:", "Human:"],
            )
            choices = self._field(completion, "choices", []) or []
            if not choices:
                return ""
            return str(self._field(choices[0], "text", "") or "")

        completion = self._client.chat.completions.create(
            model=self.model,
            messages=self._prepare_chat_messages(messages),
            stream=False,
            temperature=0.35,
            top_p=0.8,
            max_tokens=self._max_tokens(max_tokens),
            stop=["User:", "Visitor:", "用户:", "来访者:", "Human:"],
        )
        choices = self._field(completion, "choices", []) or []
        if not choices:
            return ""
        message = self._field(choices[0], "message")
        return str(self._field(message, "content", "") or "")

    def warmup(self) -> bool:
        """Verify that the already-managed vLLM server exposes this model."""
        return self.model in self.list_model_ids()
