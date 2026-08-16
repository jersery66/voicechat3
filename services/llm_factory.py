"""Build the compatibility LLM service selected by the deployment profile."""

from __future__ import annotations

import os
from typing import Generator, Optional

from deployment.profiles import get_deployment_profile, resolve_runtime_models
from inference.vllm_client import VLLMOpenAIClient


class VLLMCompatibleLLMService:
    """Preserves the legacy LLMService surface over the vLLM HTTP backend.

    This lets the current report, UI, and pipeline code move to vLLM without
    changing their conversation-history semantics in the same release.
    """

    MAX_HISTORY_TURNS = 20

    def __init__(self, backend: VLLMOpenAIClient, *, model: str,
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
        """Keep vLLM requests below a bounded recent-turn context window.

        The previous Ollama-specific summarizer is intentionally not reused:
        it would introduce a hidden second model dependency.  Retaining only
        complete recent dialogue turns gives the A100 profile a deterministic
        bound that works when every language model is served by vLLM.
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


def build_llm_service(*, profile_name: str | None = None):
    """Return Ollama LLMService for development or vLLM compatibility service."""
    profile = get_deployment_profile(profile_name)
    models = resolve_runtime_models(profile)
    if profile.runtime_backend == "vllm":
        backend = VLLMOpenAIClient(
            model=models.dialogue,
            base_url=(
                profile.dialogue_base_url
                if profile.immutable_runtime_contract
                else os.environ.get("VOICECHAT_DIALOGUE_BASE_URL", profile.dialogue_base_url)
            ),
            request_mode=profile.vllm_request_mode,
            system_role_mode=profile.vllm_system_role_mode,
            max_tokens=profile.dialogue_max_tokens,
            dialogue_temperature=profile.dialogue_temperature,
            dialogue_top_p=profile.dialogue_top_p,
            dialogue_top_k=profile.dialogue_top_k,
            dialogue_presence_penalty=profile.dialogue_presence_penalty,
            dialogue_enable_thinking=profile.dialogue_enable_thinking,
        )
        return VLLMCompatibleLLMService(
            backend,
            model=models.dialogue,
            system_prompt=profile.system_prompt_override,
        )

    from services.llm_service import LLMService

    return LLMService(model=models.dialogue, host=profile.dialogue_base_url)
