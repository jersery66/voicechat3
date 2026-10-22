"""Build the compatibility LLM service selected by the runtime configuration."""

from __future__ import annotations

from typing import Generator, Optional

from deployment.runtime_config import (
    PROVIDER_OPENAI_COMPATIBLE,
    RuntimeConfig,
    get_runtime_config,
)
from inference.factory import build_dialogue_client
from inference.openai_client import OpenAICompatibleClient


class OpenAICompatibleLLMService:
    """Preserves the legacy LLMService surface over an OpenAI-compatible backend.

    This lets the current report, UI, and pipeline code talk to any
    OpenAI-compatible model server (llama.cpp, vLLM, ...) without changing
    their conversation-history semantics.
    """

    MAX_HISTORY_TURNS = 20

    def __init__(self, backend: OpenAICompatibleClient, *, model: str,
                 system_prompt: Optional[str] = None) -> None:
        from config import SYSTEM_PROMPT

        self.backend = backend
        self.model = model
        self.conversation_history: list[dict[str, str]] = []
        self.system_prompt = SYSTEM_PROMPT if system_prompt is None else system_prompt
        self.history_context = ""

    def reset_conversation(self, clear_context: bool = False) -> None:
        self.conversation_history = []
        if clear_context:
            self.history_context = ""

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    def set_history_context(self, context: str) -> None:
        self.history_context = context

    def _system_context(self, system_suffix: Optional[str] = None) -> str:
        parts = [self.system_prompt, self.history_context, system_suffix or ""]
        return "\n".join(part for part in parts if part)

    def chat(
        self,
        user_message: str,
        system_suffix: Optional[str] = None,
        *,
        commit_history: bool = True,
    ) -> Generator[str, None, None]:
        # Keep the outgoing request to at most ``MAX_HISTORY_TURNS`` dialogue
        # turns, counting the current user message.  Trim complete prior turns
        # before appending so no orphaned assistant message is retained.
        max_prior_messages = (self.MAX_HISTORY_TURNS - 1) * 2
        if len(self.conversation_history) > max_prior_messages:
            self.conversation_history = self.conversation_history[-max_prior_messages:]
        user_entry = {"role": "user", "content": user_message}
        self.conversation_history.append(user_entry)

        def _rollback_user_entry() -> None:
            # Overlapping generations may append a newer user turn while this
            # provider iterator is winding down; remove only this request.
            for index in range(len(self.conversation_history) - 1, -1, -1):
                if self.conversation_history[index] is user_entry:
                    self.conversation_history.pop(index)
                    return
        full_response = ""
        messages = [{"role": "system", "content": self._system_context(system_suffix)}]
        messages.extend(self.conversation_history)
        try:
            for chunk in self.backend.stream_messages(messages=messages):
                full_response += chunk
                yield chunk
        except Exception:
            if not full_response and commit_history:
                _rollback_user_entry()
            elif full_response and commit_history:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": self._history_visible_text(full_response),
                })
            raise

        if not full_response.strip():
            if commit_history:
                _rollback_user_entry()
            raise RuntimeError("LLM_NO_FINAL_CONTENT")
        if commit_history:
            self.conversation_history.append({
                "role": "assistant",
                "content": self._history_visible_text(full_response),
            })
            self._maybe_summarize()

    @staticmethod
    def _history_visible_text(text: str) -> str:
        from services.llm_service import LLMService

        return LLMService._history_visible_text(text)

    def _maybe_summarize(self) -> None:
        """Keep model-server requests below a bounded recent-turn context window.

        The previous Ollama-specific summarizer is intentionally not reused:
        it would introduce a hidden second model dependency.  Retaining only
        complete recent dialogue turns gives the runtime configuration a
        deterministic bound that works when the dialogue service is any
        OpenAI-compatible server.
        """
        max_messages = self.MAX_HISTORY_TURNS * 2
        if len(self.conversation_history) > max_messages:
            self.conversation_history = self.conversation_history[-max_messages:]

    def chat_sync(self, user_message: str) -> str:
        return "".join(self.chat(user_message))

    def generate_short_text(self, prompt: str, max_tokens: int = 60) -> str:
        try:
            return self.backend.complete_messages(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            ).strip()
        except Exception:
            return ""

    def get_available_models(self) -> list[str]:
        try:
            return self.backend.list_model_ids()
        except Exception:
            return []

    def test_connection(self) -> bool:
        try:
            return self.model in self.backend.list_model_ids()
        except Exception:
            return False

    def warmup(self) -> bool:
        try:
            return bool(self.backend.warmup())
        except Exception:
            return False


def build_llm_service(config: Optional[RuntimeConfig] = None):
    """Return the dialogue service for the configured provider.

    OpenAI-compatible providers (llama.cpp, vLLM, ...) are served through
    the compatibility service; the Ollama provider keeps its native
    ``LLMService`` path.
    """
    config = config if config is not None else get_runtime_config()
    if config.dialogue.provider != PROVIDER_OPENAI_COMPATIBLE:
        from services.llm_service import LLMService

        return LLMService(model=config.dialogue.model, host=config.dialogue.base_url)
    backend = build_dialogue_client(config)
    return OpenAICompatibleLLMService(
        backend,
        model=config.dialogue.model,
        system_prompt=config.dialogue.system_prompt_override,
    )
