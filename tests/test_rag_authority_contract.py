"""Decision-gated RAG integration over the real ConversationPipeline."""

from __future__ import annotations

from conversation.contracts import RouterAction
from tests.e2e.fixtures import ScenarioHarness, proposal


def test_false_gate_performs_zero_retrievals():
    harness = ScenarioHarness(responses=["普通回应。"])
    try:
        result, _ = harness.run_turn("心理相关关键词但不需要背景", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.turn_decision.needs_rag is False
        assert harness.rag.calls == []
    finally:
        harness.shutdown()


def test_true_gate_performs_one_authorized_retrieval():
    harness = ScenarioHarness(responses=["参考背景回应。"])
    try:
        result, _ = harness.run_turn("请参考背景", proposal(RouterAction.CHAT, needs_rag=True))
        assert result.turn_decision.needs_rag is True
        assert len(harness.rag.calls) == 1
        assert harness.rag.calls[0][1] is True
    finally:
        harness.shutdown()


def test_rag_failure_does_not_change_turn_decision():
    harness = ScenarioHarness(responses=["仍然回应。"])

    class FailingRAG:
        def __init__(self):
            self.calls = 0

        def get_system_suffix(self, _query, *, enabled=False):
            self.calls += 1
            assert enabled is True
            raise TimeoutError("synthetic RAG timeout")

    rag = FailingRAG()
    harness.rag = rag
    harness.pipeline.rag = rag
    try:
        try:
            harness.run_turn("需要背景但服务超时", proposal(RouterAction.CHAT, needs_rag=True))
        except TimeoutError:
            pass
        else:
            raise AssertionError("production RAG error was silently converted into a new policy")
        assert harness.trace.turn_decisions[-1].needs_rag is True
        assert rag.calls == 1
    finally:
        harness.shutdown()
