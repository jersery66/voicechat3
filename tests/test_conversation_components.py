"""Conversation helpers can be exercised without loading models or Qt."""

from conversation.context_builder import ConversationContextBuilder
from conversation.response_builder import ResponseBuilder


def test_context_builder_keeps_active_scale_context_small():
    builder = ConversationContextBuilder(active_scale_limit=12, dialogue_limit=30)

    context = builder.build(rag_documents=["one", "two"], active_scale=True)

    assert context == "one\n\ntwo"


def test_response_builder_never_exposes_internal_control_tags():
    response = ResponseBuilder().build("analysis|||\u4f60\u597d[SCALE:PHQ-9:Q1:S2][REC_BREATHING]")

    assert response.spoken_text == "\u4f60\u597d"
    assert response.analysis_text == "analysis"
