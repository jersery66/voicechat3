"""Thin dry-run scenarios using real policy/pipeline/runtime and fake providers."""

from __future__ import annotations

import pytest

from conversation.contracts import RouterAction, TurnAction
from tests.e2e.fixtures import ScenarioHarness, proposal


@pytest.mark.parametrize(
    "label,text,needs_rag,start_round",
    [
        ("ordinary", "今天有点累。", False, 0),
        ("small_talk", "天气还行。", False, 0),
        ("low_mood", "最近心里有点低落。", True, 0),
        ("resistance", "我不想回答这个。", False, 0),
        ("repeated_refusal", "还是不想说。", False, 0),
        ("advice_request", "你直接告诉我怎么办。", True, 0),
        ("institutional_frustration", "这里的规定让我很烦。", True, 0),
        ("asr_ambiguity", "不是每天，大概两三天。", False, 0),
        ("scale_entry", "最近一直睡不着。", True, 5),
        ("scale_progression", "继续聊聊量表。", True, 5),
        ("scale_refusal", "这个我不想答。", False, 5),
        ("rag_false", "只是提到了心理这个词。", False, 0),
        ("rag_true", "请参考相关背景。", True, 0),
        ("long_context", "这是一段合成的较长上下文，用于验证传递不会改变权威链。" * 2, False, 0),
        ("cancellation", "这轮之后会被新轮次取消。", False, 0),
        ("session_end", "今天先这样吧。", False, 0),
    ],
)
def test_dry_run_synthetic_scenarios_use_real_authority_chain(label, text, needs_rag, start_round):
    harness = ScenarioHarness(start_round=start_round, responses=["这是测试回应。"])
    try:
        result, _ = harness.run_turn(text, proposal(RouterAction.CHAT, needs_rag=needs_rag))
        assert result.turn_decision is not None, label
        assert result.spoken_text, label
        assert result.router_proposal is not None, label
    finally:
        harness.shutdown()


def test_router_proposal_is_advisory_before_policy_gate():
    harness = ScenarioHarness(start_round=0, responses=["测试回应。"])
    try:
        result, _ = harness.run_turn(
            "请开始量表",
            proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
        )
        assert result.turn_decision.action is TurnAction.CHAT
        assert harness.pipeline.scale_runtime.snapshot().active_scale is None
    finally:
        harness.shutdown()


def test_turn_policy_approves_scale_and_runtime_chooses_item():
    harness = ScenarioHarness(start_round=5, responses=["请自然询问当前内容。"])
    try:
        result, _ = harness.run_turn(
            "最近一直睡不着",
            proposal(RouterAction.START_SCALE, scale="PHQ-9", needs_rag=True),
        )
        assert result.turn_decision.action is TurnAction.START_SCALE
        runtime = harness.pipeline.scale_runtime.snapshot()
        assert runtime.active_scale == "PHQ-9"
        assert runtime.current_item == 1
    finally:
        harness.shutdown()


def test_rag_false_makes_zero_calls_and_true_uses_one_authorized_path():
    harness = ScenarioHarness(responses=["不需要检索。", "需要检索。"])
    try:
        harness.run_turn("普通聊天", proposal(RouterAction.CHAT, needs_rag=False))
        assert harness.rag.calls == []
        harness.run_turn("请参考背景", proposal(RouterAction.CHAT, needs_rag=True))
        assert len(harness.rag.calls) == 1
        assert harness.rag.calls[0][1] is True
    finally:
        harness.shutdown()


def test_malicious_legacy_model_tags_cannot_execute_business_actions():
    harness = ScenarioHarness(responses=["[END_SESSION][REC_RELAX][SCALE:PHQ-9:Q1:S3] 测试回应。"])
    try:
        result, _ = harness.run_turn("我好多了", proposal(RouterAction.CHAT, needs_rag=False))
        assert result.turn_decision.action is TurnAction.CHAT
        assert result.end_type is None
        assert harness.pipeline.scale_runtime.snapshot().active_scale is None
        assert result.relaxation_rec is None
    finally:
        harness.shutdown()


def test_generation_cancel_drops_stale_history_and_data():
    harness = ScenarioHarness()
    try:
        old = harness.controller.start_generation()
        harness.pipeline.delivery_ledger.record_generated(old.generation_id, "旧生成尾巴")
        harness.controller.cancel_generation(old.generation_id, reason="new turn")
        new = harness.controller.start_generation()
        assert harness.pipeline.delivery_ledger.finalize_history(old.generation_id, harness.llm, harness.data) == ""
        assert new.generation_id > old.generation_id
        assert not any(item["text"] == "旧生成尾巴" for item in harness.trace.data_manager_writes)
    finally:
        harness.shutdown()
