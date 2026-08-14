"""Phase 6 red/green tests for the language-only response boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from config import SYSTEM_PROMPT
from conversation.response_builder import ResponseBuilder


ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"missing function {name!r} in {path}")


def test_72b_system_prompt_is_language_only():
    forbidden = (
        "|||",
        "[END_",
        "[REC_",
        "[SCALE:",
        "每轮回复都必须包含",
        "结束标签",
        "策略选择",
        "心理分析内容",
        "输出一个JSON",
    )
    for marker in forbidden:
        assert marker not in SYSTEM_PROMPT


def test_response_builder_has_no_pipeline_dependency_or_orientation_parser():
    source = (ROOT / "conversation" / "response_builder.py").read_text(encoding="utf-8")

    assert "from services.pipeline" not in source
    assert "def _split_response" not in source
    assert "analysis_markers" not in source


def test_response_builder_normalizes_plain_generated_text_without_business_fields():
    response = ResponseBuilder.build("嗯，我在听。[breath]")

    assert response.generated_text == "嗯，我在听。[breath]"
    assert response.spoken_text == "嗯，我在听。"
    assert response.tts_text == "嗯，我在听。[breath]"
    assert not hasattr(response, "action")
    assert not hasattr(response, "end_type")


def test_stream_llm_does_not_parse_control_protocol():
    source = _function_source(ROOT / "services" / "pipeline.py", "_stream_llm")

    assert "detect_tag(" not in source
    assert "parse_scale_tags(" not in source
    assert "split('|||" not in source
    assert 'split("|||"' not in source
