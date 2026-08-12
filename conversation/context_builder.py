"""Build the dialogue context that is safe to hand to an inference client."""

from __future__ import annotations

from knowledge.rag_service import ContextBuilder


class ConversationContextBuilder:
    """Keeps retrieval budgeting independent of prompt construction."""

    def __init__(self, *, active_scale_limit: int = 500, dialogue_limit: int = 1200):
        self._rag_context = ContextBuilder(
            active_scale_limit=active_scale_limit,
            dialogue_limit=dialogue_limit,
        )

    def build(self, *, rag_documents: list[str], active_scale: bool) -> str:
        return self._rag_context.build(rag_documents, active_scale=active_scale)
