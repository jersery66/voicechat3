"""Regression tests: participant text and model replies must not reach logs.

Covers the A1 audit finding.  Every previously leaking log line now emits
metadata only (text length, error class, event type) unless raw text logging
is explicitly opted into via ``VOICECHAT_LOG_SENSITIVE_TEXT``.
"""

from __future__ import annotations

import logging
import types

from services.logger import (
    SENSITIVE_TEXT_LOG_ENV,
    sensitive_text_logging_enabled,
    text_meta,
)

SENSITIVE_USER_TEXT = "患者张三住在幸福路12号，今晚想结束生命"
SENSITIVE_REPLY_TEXT = "张三，我听到你说想结束生命，我们一起来面对"


def test_text_meta_default_hides_text_and_reports_length():
    meta = text_meta("raw", SENSITIVE_USER_TEXT)
    assert meta == f"raw_len={len(SENSITIVE_USER_TEXT)}"
    assert "张三" not in meta
    assert "幸福路" not in meta


def test_text_meta_never_substitutes_a_hash():
    # Hashes are deliberately rejected: short utterances stay vulnerable to
    # dictionary attacks and cross-record linkage even when hashed.
    meta = text_meta("user", SENSITIVE_USER_TEXT)
    assert "hash" not in meta.lower()


def test_text_meta_opt_in_requires_explicit_environment(monkeypatch):
    monkeypatch.delenv(SENSITIVE_TEXT_LOG_ENV, raising=False)
    assert sensitive_text_logging_enabled() is False
    assert "张三" not in text_meta("user", SENSITIVE_USER_TEXT)

    monkeypatch.setenv(SENSITIVE_TEXT_LOG_ENV, "1")
    assert sensitive_text_logging_enabled() is True
    assert "张三" in text_meta("user", SENSITIVE_USER_TEXT)


def test_text_meta_handles_none_and_non_string_values():
    assert text_meta("raw", None) == "raw_len=0"
    assert text_meta("raw", 12345) == "raw_len=5"


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.message = types.SimpleNamespace(content=content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.choices = [_FakeChoice(content, finish_reason)]


class _FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


def _agent_service_with(client_content: str):
    from services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    service.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_FakeCompletions(client_content))
    )
    service.model = "test-agent-model"
    return service


def test_agent_raw_log_does_not_leak_model_output(caplog):
    service = _agent_service_with(f"not-json::{SENSITIVE_REPLY_TEXT}")
    with caplog.at_level(logging.WARNING):
        try:
            service._call_json("system", SENSITIVE_USER_TEXT)
        except Exception:
            pass
    assert "张三" not in caplog.text
    assert SENSITIVE_REPLY_TEXT not in caplog.text
    assert "content_len=" in caplog.text


def test_agent_route_raw_log_does_not_leak_model_output(caplog):
    service = _agent_service_with(SENSITIVE_REPLY_TEXT)
    with caplog.at_level(logging.WARNING):
        try:
            service.route_conversation_actions(SENSITIVE_USER_TEXT)
        except Exception:
            pass
    assert "张三" not in caplog.text
    assert SENSITIVE_REPLY_TEXT not in caplog.text
    assert "finish_reason=stop" in caplog.text


def test_pipeline_agent_route_log_does_not_leak_user_text(caplog):
    from conversation.agent_observation import AgentObservation
    from conversation.contracts import RouterAction, RouterProposal
    from services.pipeline import ConversationPipeline

    observed_kwargs = {}

    class FakeAgent:
        def is_available(self):
            return True

        def observe_turn(self, **kwargs):
            # The real AgentService receives the raw user text; the log line
            # must still not carry it.
            observed_kwargs.update(kwargs)
            return AgentObservation(
                proposal=RouterProposal(action=RouterAction.CHAT, confidence=0.5)
            )

    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline.agent = FakeAgent()
    pipeline._agent_route_cooldown = 0
    pipeline._get_recent_dialogue_text = lambda: ""
    pipeline._get_relaxation_done = lambda: False
    pipeline.scale_runtime = types.SimpleNamespace(
        snapshot=lambda: types.SimpleNamespace(active_scale=None, answers_by_scale={})
    )

    with caplog.at_level(logging.WARNING):
        observation = pipeline._request_agent_observation(
            user_text=SENSITIVE_USER_TEXT,
            current_rounds=1,
        )

    assert observed_kwargs["user_text"] == SENSITIVE_USER_TEXT
    assert observation.proposal.action == RouterAction.CHAT
    assert "张三" not in caplog.text
    assert "幸福路" not in caplog.text
    assert "[AgentRoute] action=chat" in caplog.text


def test_asr_correction_log_does_not_leak_transcript(caplog):
    from services.pipeline import correct_asr_text

    transcript = f"  {SENSITIVE_USER_TEXT}  "
    with caplog.at_level(logging.WARNING):
        correct_asr_text(transcript)
    assert "张三" not in caplog.text
    assert "幸福路" not in caplog.text
    assert "whitespace_normalized" in caplog.text


def test_llm_raw_full_log_does_not_leak_model_reply(caplog):
    from services.llm_service import LLMService

    class Client:
        def chat(self, **kwargs):
            if kwargs["stream"]:
                return iter([{"message": {"content": SENSITIVE_REPLY_TEXT}}])
            return {"message": {"content": ""}}

    service = LLMService.__new__(LLMService)
    service.client = Client()
    service.model = "test-model"
    service.system_prompt = "system"
    service.history_context = ""
    service.conversation_history = []
    service._maybe_summarize = lambda: None

    with caplog.at_level(logging.WARNING):
        list(service.chat(SENSITIVE_USER_TEXT))

    assert "张三" not in caplog.text
    assert SENSITIVE_REPLY_TEXT not in caplog.text
    assert "[LLMRawFull]" in caplog.text
    assert f"text_len={len(SENSITIVE_REPLY_TEXT)}" in caplog.text
