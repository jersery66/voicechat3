# LLM Service - Ollama Integration

import os
import re
import sys
import time
from typing import Generator, List, Dict, Any, Optional

from services.logger import get_logger
from services.metrics import get_metrics
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
            if OLLAMA_MODEL in available:
                self.model = OLLAMA_MODEL
            elif available:
                self.model = available[0]
            else:
                self.model = OLLAMA_MODEL
        else:
            self.model = model
        
    def reset_conversation(self, clear_context: bool = False):
        """Clear conversation history.

        Args:
            clear_context: If True, also clear history_context (user profile
                and past summaries). Use when switching to a new subject.
        """
        self.conversation_history = []
        if clear_context:
            self.history_context = ""
        
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
        reasoning_buffer = ""
        chunks_yielded = 0
        request_started = time.perf_counter()
        first_token_recorded = False
        stream_options = {
            # Stop sequences mandated by the design spec: prevent the model from
            # fabricating the user's next turn or role markers in its output.
            "stop": ["User:", "Visitor:", "用户:", "来访者:", "Human:"],
            "num_predict": 1024,
            "temperature": 0.35,
            "top_p": 0.8,
        }

        try:
            stream = self.client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options=stream_options,
            )

            def _msg_get(msg, key, default=""):
                """Get attribute from dict or Message object."""
                if isinstance(msg, dict):
                    return msg.get(key, default)
                return getattr(msg, key, default) or default

            def _msg_keys(msg):
                """List keys from dict or Message object."""
                if isinstance(msg, dict):
                    return list(msg.keys())
                return [k for k in ("content", "thinking", "reasoning", "reasoning_content") if hasattr(msg, k)]

            for chunk in stream:
                if chunk.get("done"):
                    logger.info(
                        f"[LLM] stream done: done_reason={chunk.get('done_reason')}, "
                        f"eval_count={chunk.get('eval_count')}, "
                        f"reasoning_len={len(reasoning_buffer)}, "
                        f"content_len={len(full_response)}"
                    )
                msg = chunk.get("message") if isinstance(chunk, dict) else getattr(chunk, "message", None)
                if msg is None:
                    continue

                if chunks_yielded == 0 and not reasoning_buffer:
                    logger.warning(
                        f"[LLMChunkDebug] type={type(msg).__name__} "
                        f"keys={_msg_keys(msg)} "
                        f"head={str(msg)[:400]}"
                    )

                # Thinking/reasoning: capture but don't yield to UI
                thinking = (
                    _msg_get(msg, "thinking")
                    or _msg_get(msg, "reasoning")
                    or _msg_get(msg, "reasoning_content")
                    or ""
                )
                if thinking:
                    reasoning_buffer += thinking
                    continue

                # Actual content: yield to UI
                content = _msg_get(msg, "content") or ""
                if not content:
                    continue
                if not first_token_recorded:
                    get_metrics().record(
                        "llm.first_token",
                        (time.perf_counter() - request_started) * 1000.0,
                    )
                    first_token_recorded = True
                full_response += content
                chunks_yielded += 1
                yield content

        except Exception as e:
            logger.error(f"LLM Generation Failed (chunks_yielded={chunks_yielded}): {e}")
            if chunks_yielded == 0:
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
            else:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": full_response,
                    "partial_response_recovered": True,
                })
            raise

        # Thinking-only: fail immediately, don't waste time retrying
        if chunks_yielded == 0 and reasoning_buffer.strip():
            logger.warning(
                f"[LLM] Thinking-only: reasoning={len(reasoning_buffer)} chars, content=0. "
                f"model={self.model}, user_msg={user_message[:80]!r}"
            )
            # Rollback user message — no assistant response was generated
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            raise RuntimeError("LLM_NO_FINAL_CONTENT")

        # Retry once if truly empty (no thinking, no content)
        recovered_from_retry = False
        if chunks_yielded == 0 or not full_response.strip():
            logger.warning(
                f"[LLM] Empty stream (no thinking, no content). Retrying. "
                f"model={self.model}, user_msg={user_message[:80]!r}"
            )
            try:
                retry_resp = self.client.chat(
                    model=self.model,
                    messages=messages,
                    stream=False,
                    options={"num_predict": 1024, "temperature": 0.35, "top_p": 0.8},
                )
                retry_msg = _msg_get(retry_resp, "message", {})
                full_response = (_msg_get(retry_msg, "content") or "").strip()
                recovered_from_retry = bool(full_response)
            except Exception as retry_err:
                logger.warning(f"[LLM] Retry also failed: {retry_err}")

            if not full_response.strip():
                # Rollback user message — no assistant response was generated
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
                raise RuntimeError("LLM_NO_FINAL_CONTENT")

        # Log raw LLM output for debugging
        logger.warning(f"[LLMRawFull] len={len(full_response)} text={full_response[:1000]!r}")

        # Note: do NOT yield full_response again — chunks were already yielded
        # in the stream loop above. Yielding here would duplicate the content.

        # Add assistant response to history — only spoken text, no analysis
        if recovered_from_retry:
            # The original streaming call produced no content. Return the
            # recovered response once so the UI and TTS receive it.
            yield full_response

        self.conversation_history.append({
            "role": "assistant",
            "content": self._history_visible_text(full_response)
        })

        # Compress history if too long
        self._maybe_summarize()

    @staticmethod
    def _history_visible_text(text: str) -> str:
        """Strip analysis, tags, and ||| separator — only keep spoken content for history.

        Mirrors the reversal handling used in pipeline._stream_llm: the spoken
        half is the side WITHOUT internal strategy markers. If the model emitted
        the reversed "spoken|||analysis" format, we keep the left side, not the
        right. We also strip any leaked internal strategy terms so they never
        enter the conversation history fed back to the model next turn.
        """
        if not text:
            return ""
        if "|||" in text:
            parts = text.split("|||")
            # Pick the half that does NOT contain internal strategy leakage;
            # the analysis half is the one carrying 内部策略 markers / bracket
            # tags. Fall back to the right side only if detection is ambiguous.
            left, right = parts[0], parts[-1]
            _analysis_markers = ("【", "[SCALE:", "[REC_", "[END_", "高防御", "量表", "问卷")
            left_is_analysis = any(m in left for m in _analysis_markers)
            right_is_analysis = any(m in right for m in _analysis_markers)
            if left_is_analysis and not right_is_analysis:
                text = right          # 正常格式 分析|||口语
            elif right_is_analysis and not left_is_analysis:
                text = left           # 反转格式 口语|||分析
            else:
                # 两侧都无法确定（ambiguous）：默认取右侧（常规格式）
                text = right
        # Strip control tags and leaked internal strategy terms
        text = re.sub(r'<think>[\s\S]*?</think>', '', text)
        text = re.sub(r'\[SCALE:[^\]]+\]', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
        text = re.sub(r'\[END_[A-Z_]+\]', '', text)
        text = re.sub(r'【.*?】', '', text)
        text = re.sub(r'\[(?:breath|laughter)\]', '', text)
        from core.tags import _FORBIDDEN_INTERNAL_TERMS
        for term in _FORBIDDEN_INTERNAL_TERMS:
            text = text.replace(term, "")
        return text.strip()

    def _fallback_reply(self, user_message: str) -> str:
        """Safe spoken fallback when Ollama returns empty output."""
        import random
        text = (user_message or "").strip("。！？!?,， ")

        if not text:
            return random.choice([
                "嗯，我在听呢。[breath]你可以慢慢说。",
                "你说，我听着。[breath]不着急。",
            ])

        if "你好" in text and len(text) <= 10:
            return "你好呀。[breath]今天感觉咋样？"

        if any(x in text for x in ["不知道", "说不出来", "不晓得"]):
            return random.choice([
                "你说'不知道'，感觉不是真的没想法，而是心里堵着不好表达。[breath]没关系，我们不急，你先说说现在最难受的是哪一块？",
                "听起来你现在有点被卡住了。[breath]没关系，我们慢慢捋，你先说说最近最让你放不下的是什么？",
            ])

        if any(x in text for x in ["不开心", "心情不好", "心情不是特别开心", "低落", "难受", "心里累"]):
            return random.choice([
                "听起来这阵子心情一直压着，不太好受。[breath]这种不开心是最近才明显起来的，还是已经持续一段时间了？",
                "你说不开心，我想多了解一下。[breath]是最近发生了什么事，还是这种感觉已经憋了挺久？",
            ])

        return random.choice([
            "嗯，我听着呢。[breath]你接着说。",
            "你说的我都有在听。[breath]再往下说说？",
        ])

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
        return "".join(self.chat(user_message))

    def generate_short_text(self, prompt: str, max_tokens: int = 60) -> str:
        """Generate a short text using the main LLM model (non-streaming)."""
        try:
            client = get_ollama_client(self.host)
            response = client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={"num_predict": max_tokens},
            )
            content = response.get("message", {}).get("content", "")
            return content.strip()
        except Exception as e:
            logger.debug(f"Short text generation failed: {e}")
            return ""
    
    def get_available_models(self) -> List[str]:
        """Get list of available Ollama models.

        Compatible with both legacy ollama responses (``name`` key) and newer
        versions which use the ``model`` attribute on ListResponse.Model objects.
        """
        try:
            models = self.client.list()
            raw_models = getattr(models, 'models', None) or models.get("models", [])
            result: List[str] = []
            for m in raw_models:
                if isinstance(m, dict):
                    name = m.get("model") or m.get("name")
                else:
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

def get_llm_service():
    global _llm_service
    if _llm_service is None:
        from services.llm_factory import build_llm_service
        _llm_service = build_llm_service()
    return _llm_service
