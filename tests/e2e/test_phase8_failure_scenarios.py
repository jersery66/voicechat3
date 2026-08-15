"""Phase 8 failure, stale-callback, reset, and authority scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest

from conversation.contracts import (
    RouterAction,
    RouterProposal,
    TurnAction,
    TurnSignals,
)
from conversation.delivery import SentenceReady
from conversation.turn_policy import TurnPolicy
from services.game_service import GameService
from services.report_service import ReportService

from tests.e2e.fixtures import ScenarioHarness, proposal, snapshot


@pytest.fixture
def harness():
    value = ScenarioHarness(start_round=0)
    try:
        yield value
    finally:
        value.shutdown()


def test_i1_invalid_router_and_timeout_use_documented_fallback_once(harness):
    invalid = RouterProposal.from_legacy_route(
        {"action": "not-a-real-action", "item": 99, "scale_score": 4}
    )
    result, _generation = harness.run_turn("继续聊聊", invalid)
    timeout_decision = TurnPolicy().decide(
        user_text="继续",
        proposal=RouterProposal.fallback("timeout_fixture"),
        snapshot=snapshot(time_limit_reached=True),
        signals=TurnSignals(),
    )

    assert invalid.reason == "router_fallback"
    assert result.turn_decision.action is TurnAction.CHAT
    assert len(harness.trace.policy_calls) == 1
    assert timeout_decision.action is TurnAction.CHAT
    assert timeout_decision.reason == "time_limit_pending_choice"


def test_i2_rag_unavailable_after_approved_gate_keeps_turn_alive(harness):
    class UnavailableRAG:
        def __init__(self):
            self.calls = []

        def get_system_suffix(self, query, *, enabled=False):
            self.calls.append((query, enabled))
            return ""

    unavailable = UnavailableRAG()
    harness.rag = unavailable
    harness.pipeline.rag = unavailable
    result, _generation = harness.run_turn(
        "请继续听我说",
        proposal(RouterAction.CHAT, needs_rag=True),
    )

    assert result.turn_decision.needs_rag is True
    assert len(unavailable.calls) == 1
    assert result.spoken_text


def test_i3_provider_exception_before_delivery_uses_current_safe_fallback(harness):
    class FailingLLM:
        conversation_history = []

        def chat(self, text, system_suffix="", *, commit_history=False):
            self.conversation_history.append({"role": "user", "content": text})
            raise RuntimeError("provider unavailable")
            yield "never reached"

    failing = FailingLLM()
    harness.llm = failing
    harness.pipeline.llm = failing
    result, _generation = harness.run_turn(
        "系统暂时有点问题吗",
        proposal(RouterAction.CHAT, needs_rag=False),
    )

    assert "系统出了点小问题" in result.spoken_text
    assert harness.trace.visible_sentences
    assert failing.conversation_history[-1]["role"] == "assistant"


def test_i4_one_sentence_tts_failure_does_not_replay_or_reorder(harness):
    harness.llm.responses = ["第一句。第二句。"]
    harness.tts.fail_on = "第二句"
    result, _generation = harness.run_turn(
        "我想说两句",
        proposal(RouterAction.CHAT, needs_rag=False),
    )

    assert result.turn_decision.action is TurnAction.CHAT
    assert [event.text for event in harness.trace.visible_sentences] == [
        "第一句。",
        "第二句。",
    ]
    assert harness.trace.tts_calls == ["第一句。", "第二句。"]
    assert harness.llm.conversation_history[-1]["content"] == "第一句。第二句。"


def test_i5_media_failure_is_recorded_as_incomplete(monkeypatch, tmp_path):
    class Tracker:
        def __init__(self, csv_path):
            self.csv_path = csv_path

        def get_summary_metrics(self):
            return {"fallback": True}

        def save_csv(self):
            pass

    class Engine:
        difficulty_sys = None

        def __init__(self, tracker):
            self.tracker = tracker

        def run(self):
            raise RuntimeError("media failed")

    monkeypatch.setattr("services.game_service.ClinicalTracker", Tracker)
    monkeypatch.setattr("services.game_service.GameEngine", Engine)
    result = GameService().play_game(str(tmp_path))

    assert result["_completed"] is False
    assert result["fallback"] is True


def test_i6_report_failure_uses_existing_fallback_and_delivery_survives():
    class FailingAgent:
        def generate_report(self, prompt, timeout=None):
            raise RuntimeError("report agent unavailable")

    service = ReportService(llm_service=object(), agent_service=FailingAgent())
    service._fallback_chat = lambda prompt, stream=False: "已有报告降级文本"
    assert service._run_with_fallback("summary", label="phase8") == "已有报告降级文本"


def test_i6_data_persistence_failure_does_not_duplicate_delivered_history(harness):
    record = harness.controller.start_generation()
    ledger = harness.pipeline.delivery_ledger
    ledger.commit_visible(SentenceReady(record.generation_id, 0, "可见句。"))

    def fail_save(*args, **kwargs):
        raise OSError("disk full")

    harness.data.save_assistant_message = fail_save
    assert ledger.finalize_history(record.generation_id, harness.llm, harness.data) == "可见句。"
    assert ledger.finalize_history(record.generation_id, harness.llm, harness.data) == "可见句。"
    assert harness.llm.conversation_history[-1]["content"] == "可见句。"


def test_i6_cancellation_during_failure_handling_drops_late_finalizer(harness):
    old = harness.controller.start_generation()
    harness.pipeline.delivery_ledger.record_generated(old.generation_id, "失败处理中的旧尾巴")
    harness.controller.cancel_generation(old.generation_id, reason="failure interrupted")
    new = harness.controller.start_generation()

    assert harness.pipeline.delivery_ledger.finalize_history(
        old.generation_id, harness.llm, harness.data
    ) == ""
    assert new.generation_id > old.generation_id
    assert not any(
        write["text"] == "失败处理中的旧尾巴" for write in harness.trace.data_manager_writes
    )


def test_phase8_static_authority_chain_has_no_second_rag_or_safety_path():
    root = Path(__file__).resolve().parents[2]
    pipeline = (root / "services" / "pipeline.py").read_text(encoding="utf-8")
    rag = (root / "services" / "rag_service.py").read_text(encoding="utf-8")
    agent = (root / "services" / "agent_service.py").read_text(encoding="utf-8")

    assert "safety/resources" not in pipeline
    assert "safety/resources" not in rag
    assert "classify_rag_intent" not in rag
    assert "classify_rag_intent" not in pipeline
    assert "TurnDecision" in pipeline
    assert "needs_rag" in pipeline
    assert "classify_rag_intent" not in agent
    assert 'CORE_FILES = ["knowledge.json"]' in rag
    for converted in (
        "cpsycounr_converted",
        "psyqa_converted",
        "emollm_single_turn_1",
        "emollm_single_turn_2",
        "emollm_multi_turn",
    ):
        assert converted not in rag


def test_phase8_static_authority_chain_has_one_scale_and_lifecycle_owner():
    root = Path(__file__).resolve().parents[2]
    pipeline = (root / "services" / "pipeline.py").read_text(encoding="utf-8")
    runtime = (root / "assessment" / "scale_runtime.py").read_text(encoding="utf-8")
    engine = (root / "app" / "engine.py").read_text(encoding="utf-8")

    for forbidden in ("self._active_scale =", "self._scale_answers =", "ScaleState()"):
        assert forbidden not in pipeline
    assert "class ScaleRuntime" in runtime
    assert "class SessionEngine" in engine
    assert "TurnPolicy" not in engine
    assert "ScaleRuntime" in pipeline


def test_phase8_static_delivery_and_ui_paths_have_no_unscoped_legacy_owner():
    root = Path(__file__).resolve().parents[2]
    pipeline = (root / "services" / "pipeline.py").read_text(encoding="utf-8")
    window = (root / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "SentenceDeliveryQueue" in pipeline
    assert "self._executor.submit(self._play_tts" not in pipeline
    assert "GenerationController" in window
    assert "SessionEngine" in window
    assert "self._pipeline_generation = 0" not in window
    assert "self._pipeline_cancel_generation = -1" not in window


def test_phase8_legacy_model_tags_have_no_live_action_authority(harness):
    result, _generation = harness.run_turn(
        "我好多了",
        proposal(RouterAction.CHAT, needs_rag=False),
    )
    assert result.turn_decision.action is TurnAction.CHAT
    assert result.end_type is None
    assert result.relaxation_rec is None
    assert all("[END_" not in event.text for event in harness.trace.visible_sentences)
    assert all("[REC_" not in event.text for event in harness.trace.visible_sentences)
    assert all("[SCALE:" not in event.text for event in harness.trace.visible_sentences)


def test_phase8_deployment_provider_contracts_remain_unchanged():
    root = Path(__file__).resolve().parents[2]
    config = (root / "deployment" / "profiles.py").read_text(encoding="utf-8")
    launcher = (root / "scripts" / "start_a100_vllm_stack.ps1").read_text(encoding="utf-8")

    assert "8000" in config
    assert "8001" in config
    assert "8000" in launcher
    assert "8001" in launcher
