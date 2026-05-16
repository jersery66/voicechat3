# LLM Service - Ollama Integration

import os
import sys
from typing import Generator, List, Dict, Any, Optional

from services.logger import get_logger
from services._ollama_pool import get_ollama_client

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OLLAMA_MODEL, OLLAMA_HOST, SYSTEM_PROMPT

try:
    # Optional: configurable timeout (seconds) for LLM requests
    from config import LLM_REQUEST_TIMEOUT  # type: ignore
except Exception:
    LLM_REQUEST_TIMEOUT = 120  # safe default

logger = get_logger(__name__)


class LLMService:
    """LLM service using Ollama for text generation.

    Manages a streaming chat interface, an in-memory conversation history,
    optional RAG/profile context injection, and graceful summarization /
    truncation when the history grows past ``MAX_HISTORY_TURNS``.
    """

    MAX_HISTORY_TURNS = 20  # Summarize when conversation exceeds this many turns

    def __init__(self, model: Optional[str] = None, host: str = OLLAMA_HOST):
        self.host = host
        # Reuse a shared ollama.Client per host to avoid repeated handshakes
        self.client = get_ollama_client(host)
        self.conversation_history: List[Dict[str, str]] = []
        self.system_prompt = SYSTEM_PROMPT
        self.history_context: str = ""

        # Auto-detect model if not specified
        if model is None:
            available = self.get_available_models()
            self.model = available[0] if available else OLLAMA_MODEL
        else:
            self.model = model
        
    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []
        
    def set_system_prompt(self, prompt: str):
        """Set the system prompt."""
        self.system_prompt = prompt
        
    def set_history_context(self, context: str):
        """Set the history context (user profile and past summaries)."""
        self.history_context = context
        
        # Build messages list with system prompt
        current_system_prompt = self.system_prompt
        # Check for system suffix in options (passed via kwargs or we can add explicit arg)
        # But we need to change signature. Let's start with just modifying chat.
        
    def chat(self, user_message: str, system_suffix: Optional[str] = None) -> Generator[str, None, None]:
        """Send a message and yield streamed response chunks.

        Args:
            user_message: The user's input message.
            system_suffix: Optional temporary instruction appended to the system
                prompt for this turn only.

        Yields:
            Response text chunks as they arrive.

        Notes:
            On a streaming exception:
              * If no chunk has been yielded yet → roll back the user message.
              * If at least one chunk has been yielded → persist the partial
                ``full_response`` as an assistant message so UI / history stay
                consistent, then re-raise.
        """
        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Build messages list with system prompt
        current_system_prompt = self.system_prompt
        if self.history_context:
            current_system_prompt += self.history_context
        if system_suffix:
            current_system_prompt += "\n" + system_suffix

        messages = [{"role": "system", "content": current_system_prompt}]
        messages.extend(self.conversation_history)

        # Stream response
        full_response = ""
        chunks_yielded = 0
        stream_options = {
            "stop": ["User:", "Visitor:", "用户:", "来访者:", "Human:", "Assistant:", "薇薇老师:", "薇薇老师："]
        }

        try:
            stream = self.client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options=stream_options,
            )

            for chunk in stream:
                if "message" in chunk and "content" in chunk["message"]:
                    content = chunk["message"]["content"]
                    if not content:
                        continue
                    full_response += content
                    chunks_yielded += 1
                    yield content

        except Exception as e:
            logger.error(f"LLM Generation Failed (chunks_yielded={chunks_yielded}): {e}")
            if chunks_yielded == 0:
                # No chunk reached the UI: safe to drop the user turn entirely.
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
            else:
                # Partial output already streamed: persist it so history matches UI.
                self.conversation_history.append({
                    "role": "assistant",
                    "content": full_response,
                    "partial_response_recovered": True,
                })
            raise

        # Add assistant response to history
        self.conversation_history.append({
            "role": "assistant",
            "content": full_response
        })

        # Compress history if too long
        self._maybe_summarize()
        
    def _maybe_summarize(self):
        """Compress conversation history using the 3B agent when it grows too long."""
        if len(self.conversation_history) < self.MAX_HISTORY_TURNS * 2:
            return  # Not long enough yet

        pre_len = len(self.conversation_history)
        try:
            from services.agent_service import get_agent_service
            agent = get_agent_service()
            summary = agent.summarize_history(self.conversation_history)
            if summary:
                # Keep last 4 turns + replace older history with summary
                recent = self.conversation_history[-8:]
                self.history_context = f"\n\n【之前的对话摘要】\n{summary}"
                self.conversation_history = recent
                logger.debug(
                    f"History compressed: {pre_len} turns → summary + {len(recent)} recent turns"
                )
                return
        except Exception as e:
            logger.warning(f"History summarization failed: {e}")

        # Fallback: truncate oldest turns to prevent unbounded growth
        if len(self.conversation_history) > self.MAX_HISTORY_TURNS * 3:
            recent = self.conversation_history[-8:]
            self.conversation_history = recent
            self.history_context = ""
            logger.warning(
                f"History truncated from {pre_len} to {len(recent)} recent turns "
                f"(summarization unavailable)"
            )

    def chat_sync(self, user_message: str) -> str:
        """
        Send a message and return the complete response.
        
        Args:
            user_message: The user's input message
            
        Returns:
            Complete response text
        """
        return "".join(self.chat(user_message))
    
    def get_available_models(self) -> List[str]:
        """Get list of available Ollama models.

        Compatible with both legacy ollama responses (``name`` key) and newer
        versions which use the ``model`` key.
        """
        try:
            models = self.client.list()
            result: List[str] = []
            for m in models.get("models", []):
                if isinstance(m, dict):
                    name = m.get("model") or m.get("name")
                else:
                    # Newer ollama returns objects with a `.model` attribute
                    name = getattr(m, "model", None) or getattr(m, "name", None)
                if name:
                    result.append(name)
            return result
        except Exception as e:
            logger.debug(f"get_available_models failed: {e}")
            return []
        
    def test_connection(self) -> bool:
        """Test if Ollama is reachable."""
        try:
            self.client.list()
            return True
        except Exception:
            return False

    def warmup(self) -> bool:
        """Warmup the model by sending a dummy request."""
        try:
            # Send a short request to force model load
            self.client.chat(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                stream=False
            )
            return True
        except Exception as e:
            logger.warning(f"LLM Warmup failed: {e}")
            return False


# Singleton instance
_llm_service = None

def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
