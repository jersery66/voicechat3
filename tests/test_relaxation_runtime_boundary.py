"""Phase 1 must remain isolated from current state owners."""

from __future__ import annotations

from pathlib import Path

import relaxation.runtime as runtime_module


def test_relaxation_runtime_has_no_reverse_business_owner_dependency():
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("ScaleRuntime", "SessionEngine", "TurnPolicy", "DialogueLLM", "ConversationPipeline"):
        assert forbidden not in source
