"""Provider-neutral context budgeter used above the legacy RAG adapter."""


class ContextBuilder:
    """Build bounded RAG context without deciding how documents are retrieved."""

    def __init__(self, *, active_scale_limit: int = 500, dialogue_limit: int = 1200):
        self.active_scale_limit = active_scale_limit
        self.dialogue_limit = dialogue_limit

    def build(self, documents: list[str], *, active_scale: bool) -> str:
        limit = self.active_scale_limit if active_scale else self.dialogue_limit
        return "\n\n".join(part.strip() for part in documents if part and part.strip())[:limit]
