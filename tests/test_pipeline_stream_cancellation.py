"""A2 cancellation semantics for the streaming dialogue path.

Covers the audit finding that a cancelled generation left the provider
stream suspended (server stream never released) and an orphaned ``user``
message in the shared history.  All tests are headless: cancellation is
driven through the generation controller exactly as
``MainWindow._cancel_active_pipeline`` does.

Server-side decoding stop (whether llama.cpp/vLLM abort generation when the
client disconnects) is a real-hardware question and is deliberately NOT
claimed here.
"""

from __future__ import annotations

import types

import pytest

from conversation.delivery import DeliveryLedger, GenerationController, SentenceReady
from inference.openai_client import OpenAICompatibleClient
from services.llm_factory import OpenAICompatibleLLMService
from services.pipeline import ConversationPipeline


def _noop_emit(*_args):
    return None


class TrackingBackend:
    """Records requests; releases deterministically; optional mid-stream hook."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.after_chunk = None  # (chunk_index, callable) checked before yield
        self.closed = False
        self.requests = []

    def stream_messages(self, *, messages):
        self.requests.append([dict(message) for message in messages])
        try:
            for index, chunk in enumerate(self.chunks):
                if self.after_chunk is not None and index == self.after_chunk[0]:
                    self.after_chunk[1]()
                yield chunk
        finally:
            self.closed = True

    def complete_messages(self, *, messages, max_tokens):
        return ""


def _make_pipeline(chunks):
    controller = GenerationController()
    ledger = DeliveryLedger(controller)

    class Queue:
        def __init__(self):
            self.events = []

        def enqueue(self, event):
            self.events.append(event)
            return True

    backend = TrackingBackend(chunks)
    service = OpenAICompatibleLLMService(backend, model="test")
    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline.llm = service
    pipeline.delivery_controller = controller
    pipeline.delivery_queue = Queue()
    pipeline.delivery_ledger = ledger
    pipeline._last_stream_generation_id = None
    return pipeline, controller, ledger, backend, service, pipeline.delivery_queue


def test_cancelled_generation_closes_stream_and_drops_pending_user_entry():
    pipeline, controller, _ledger, backend, service, queue = _make_pipeline(
        ["第一句。", "第二句。", "第三句。"]
    )
    record = controller.start_generation()

    def supersede():
        controller.start_generation()  # new user turn replaces the old one

    backend.after_chunk = (1, supersede)

    generated, _analysis, _tts = pipeline._stream_llm(
        "第一轮用户输入", None, _noop_emit, generation_id=record.generation_id
    )

    assert generated == "第一句。"
    assert [event.text for event in queue.events] == ["第一句。"]
    # The provider stream was released deterministically, not left suspended.
    assert backend.closed is True
    # Nothing was delivered to the participant: no orphan user message.
    assert service.conversation_history == []


def test_new_turn_after_cancellation_sends_only_the_new_user_turn():
    pipeline, controller, _ledger, backend, _service, _queue = _make_pipeline(
        ["第一句。", "第二句。"]
    )
    record = controller.start_generation()
    backend.after_chunk = (1, controller.start_generation)
    pipeline._stream_llm(
        "第一轮用户输入", None, _noop_emit, generation_id=record.generation_id
    )

    record2 = controller.start_generation()
    pipeline._stream_llm(
        "第二轮用户输入", None, _noop_emit, generation_id=record2.generation_id
    )

    user_contents = [
        message["content"]
        for message in backend.requests[-1]
        if message["role"] == "user"
    ]
    assert user_contents == ["第二轮用户输入"]


def test_delivered_prefix_keeps_user_entry_for_ledger_commit():
    pipeline, controller, ledger, backend, service, queue = _make_pipeline(
        ["第一句。", "第二句。"]
    )
    record = controller.start_generation()

    def deliver_prefix_then_supersede():
        if queue.events:
            # The participant actually heard the first sentence.
            assert ledger.commit_visible(queue.events[0]) is True
        controller.start_generation()

    backend.after_chunk = (1, deliver_prefix_then_supersede)

    generated, _analysis, _tts = pipeline._stream_llm(
        "第一轮用户输入", None, _noop_emit, generation_id=record.generation_id
    )

    assert generated == "第一句。"
    assert ledger.delivered_text(record.generation_id) == "第一句。"
    # The user turn stays: the ledger commits the delivered prefix as the
    # assistant reply during finalize_history.
    assert service.conversation_history == [{"role": "user", "content": "第一轮用户输入"}]


def test_backend_failure_without_delivery_rolls_back_user_entry():
    class FailingBackend:
        def __init__(self):
            self.requests = []

        def stream_messages(self, *, messages):
            self.requests.append([dict(message) for message in messages])
            yield "第一句。"
            raise RuntimeError("connection reset by peer")

        def complete_messages(self, *, messages, max_tokens):
            return ""

    controller = GenerationController()
    ledger = DeliveryLedger(controller)

    class Queue:
        def __init__(self):
            self.events = []

        def enqueue(self, event):
            self.events.append(event)
            return True

    service = OpenAICompatibleLLMService(FailingBackend(), model="test")
    pipeline = ConversationPipeline.__new__(ConversationPipeline)
    pipeline.llm = service
    pipeline.delivery_controller = controller
    pipeline.delivery_queue = Queue()
    pipeline.delivery_ledger = ledger
    pipeline._last_stream_generation_id = None

    record = controller.start_generation()
    with pytest.raises(RuntimeError):
        pipeline._stream_llm(
            "用户输入", None, _noop_emit, generation_id=record.generation_id
        )

    assert service.conversation_history == []


def test_factory_generator_close_rolls_back_pending_user_entry():
    backend = TrackingBackend(["第一句。", "第二句。"])
    service = OpenAICompatibleLLMService(backend, model="test")

    generator = service.chat("用户消息")
    assert next(generator) == "第一句。"
    assert service.conversation_history == [{"role": "user", "content": "用户消息"}]

    generator.close()

    # Self-committing mode: the consumer aborted before any answer surfaced,
    # so the pending user turn is rolled back and the backend is released.
    assert service.conversation_history == []
    assert backend.closed is True


def test_factory_deferred_mode_defers_history_decision_to_ledger_owner():
    backend = TrackingBackend(["第一句。", "第二句。"])
    service = OpenAICompatibleLLMService(backend, model="test")

    generator = service.chat("用户消息", commit_history=False)
    assert next(generator) == "第一句。"
    assert service.conversation_history == [{"role": "user", "content": "用户消息"}]

    generator.close()

    # Deferred mode: the entry stays pending; the ledger owner decides.
    assert service.conversation_history == [{"role": "user", "content": "用户消息"}]
    assert callable(generator.rollback_user_entry)
    generator.rollback_user_entry()
    assert service.conversation_history == []


def test_factory_backend_error_without_content_rolls_back_user_entry():
    class ExplodingBackend:
        def stream_messages(self, *, messages):
            raise RuntimeError("connection reset")
            yield  # pragma: no cover - makes this a generator

        def complete_messages(self, *, messages, max_tokens):
            return ""

    service = OpenAICompatibleLLMService(ExplodingBackend(), model="test")
    with pytest.raises(RuntimeError):
        list(service.chat("用户消息"))
    assert service.conversation_history == []


class _FakeStream:
    def __init__(self):
        self.closed = False
        self._chunks = iter(
            [
                {"choices": [{"delta": {"content": "第一句。"}}]},
                {"choices": [{"delta": {"content": "第二句。"}}]},
            ]
        )

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.closed = True


class _FakeSDK:
    def __init__(self):
        self.stream = _FakeStream()
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kwargs: self.stream)
        )


def _client_with_fake_sdk():
    client = OpenAICompatibleClient(
        model="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
    )
    client._client = _FakeSDK()
    return client


def test_openai_client_releases_stream_when_consumer_cancels():
    client = _client_with_fake_sdk()
    generator = client.stream_messages(messages=[{"role": "user", "content": "你好"}])

    assert next(generator) == "第一句。"
    generator.close()
    assert client._client.stream.closed is True


def test_openai_client_releases_stream_after_normal_completion():
    client = _client_with_fake_sdk()
    chunks = list(client.stream_messages(messages=[{"role": "user", "content": "你好"}]))

    assert chunks == ["第一句。", "第二句。"]
    assert client._client.stream.closed is True
