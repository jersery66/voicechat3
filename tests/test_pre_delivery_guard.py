"""Correction A2 pre-delivery safety contracts."""

from __future__ import annotations

from conversation.contracts import TurnAction
from conversation.pre_delivery_guard import GenerationGuardState, GuardContext, PreDeliveryGuard


def _guard(action=TurnAction.CHAT):
    return PreDeliveryGuard(), GuardContext(generation_id=1, turn_action=action), GenerationGuardState()


def test_normal_sentence_is_allowed_unchanged():
    guard, context, state = _guard()
    result = guard.evaluate("听起来你最近确实有点累。", context=context, state=state)
    assert result.status == "ALLOW"
    assert result.text == "听起来你最近确实有点累。"


def test_internal_strategy_and_legacy_control_text_is_blocked_before_delivery():
    guard, context, state = _guard()
    assert guard.evaluate("根据PHQ-9评分，你应该放松。[END_SESSION]", context=context, state=state).status == "BLOCK"
    assert guard.evaluate("[REC_BREATHING]", context=context, state=GenerationGuardState()).status == "BLOCK"


def test_scale_wording_leak_is_blocked_for_scale_language_tasks():
    guard, context, state = _guard(TurnAction.CONTINUE_SCALE)
    result = guard.evaluate("这是PHQ-9的第几题？", context=context, state=state)
    assert result.status == "BLOCK"
    assert result.reason in {"scale_wording_leak", "internal_strategy_leak"}


def test_one_primary_question_budget_is_generation_scoped():
    guard, context, state = _guard()
    assert guard.evaluate("最近睡眠怎么样？", context=context, state=state).status == "ALLOW"
    second = guard.evaluate("你一般几点睡？", context=context, state=state)
    assert second.status == "BLOCK"
    assert second.reason == "primary_question_budget_exceeded"


def test_new_generation_has_independent_question_budget():
    guard, context, state = _guard()
    assert guard.evaluate("最近睡眠怎么样？", context=context, state=state).status == "ALLOW"
    new_context = GuardContext(generation_id=2)
    new_state = GenerationGuardState()
    assert guard.evaluate("你一般几点睡？", context=new_context, state=new_state).status == "ALLOW"


def test_guard_does_not_contain_business_decision_or_recovery_logic():
    source = PreDeliveryGuard.evaluate.__code__.co_names
    assert "TurnPolicy" not in source
    assert "ScaleRuntime" not in source
    assert "SessionEngine" not in source


def test_pipeline_guard_blocks_second_question_before_ui_and_tts():
    from conversation.contracts import RouterAction
    from tests.e2e.fixtures import ScenarioHarness, proposal

    harness = ScenarioHarness(responses=["最近睡眠怎么样？你一般几点睡？"])
    try:
        result, _ = harness.run_turn("我最近有点累", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.turn_decision.action is TurnAction.CHAT
        assert [item.text for item in harness.trace.visible_sentences] == ["最近睡眠怎么样？"]
        assert harness.tts.started.wait(timeout=1.0)
        assert harness.trace.tts_calls == ["最近睡眠怎么样？"]
    finally:
        harness.shutdown()


def test_pipeline_guard_blocks_scale_wording_before_tts_without_changing_decision():
    from conversation.contracts import RouterAction
    from tests.e2e.fixtures import ScenarioHarness, proposal

    harness = ScenarioHarness(start_round=5, responses=["PHQ-9第1题，你得3分。"])
    try:
        result, _ = harness.run_turn("最近一直睡不着", proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=False))
        assert result.turn_decision.action is TurnAction.START_SCALE
        assert len(harness.trace.visible_sentences) == 1
        assert "PHQ" not in harness.trace.visible_sentences[0].text
        assert harness.tts.started.wait(timeout=1.0)
        assert len(harness.trace.tts_calls) == 1
        assert "PHQ" not in harness.trace.tts_calls[0]
    finally:
        harness.shutdown()


def test_first_unsafe_sentence_emits_one_safe_fallback_and_does_not_recurse():
    from conversation.contracts import RouterAction
    from tests.e2e.fixtures import ScenarioHarness, proposal

    harness = ScenarioHarness(responses=["根据PHQ-9评分，你得3分。"])
    try:
        result, _ = harness.run_turn("最近有点难受", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.turn_decision.action is TurnAction.CHAT
        assert len(harness.trace.visible_sentences) == 1
        assert harness.tts.started.wait(timeout=1.0)
        assert len(harness.trace.tts_calls) == 1
        assert "PHQ" not in harness.trace.visible_sentences[0].text
    finally:
        harness.shutdown()


def test_stale_or_cancelled_generation_never_emits_fallback():
    from conversation.delivery import SentenceReady
    from tests.e2e.fixtures import ScenarioHarness

    harness = ScenarioHarness()
    try:
        old = harness.controller.start_generation()
        harness.controller.start_generation()
        harness.pipeline._emit_generation_sentence(
            SentenceReady(old.generation_id, 0, "[END_SESSION]"),
            harness.emit,
        )
        assert harness.trace.visible_sentences == []
        assert harness.trace.tts_calls == []
    finally:
        harness.shutdown()
