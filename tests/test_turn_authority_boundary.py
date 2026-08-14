"""Static guardrails for the Phase 2 single-decision boundary."""

import ast
from pathlib import Path

from conversation.contracts import RouterProposal, TurnDecision
from services.agent_service import AgentService


ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    node = next(
        item for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_router_proposal_contract_is_not_executable():
    forbidden = {
        "scale_item", "requested_item", "scale_score", "accepted_score",
        "next_item", "risk_level", "urgency", "crisis", "session_state",
    }
    assert forbidden.isdisjoint(RouterProposal.model_fields)
    assert callable(getattr(AgentService, "route_proposal", None))


def test_router_proposal_adapter_drops_legacy_item_and_score_fields():
    proposal = RouterProposal.from_legacy_route({
        "action": "start_scale",
        "scale": "PHQ-9",
        "item": 9,
        "scale_score": 3,
        "risk_level": 0,
        "confidence": 0.9,
    })
    assert proposal.scale_name == "PHQ-9"
    assert "item" not in proposal.model_dump()
    assert "scale_score" not in proposal.model_dump()


def test_pipeline_and_ui_do_not_route_from_raw_legacy_fields():
    pipeline = (ROOT / "services" / "pipeline.py").read_text(encoding="utf-8")
    assert "agent_route.get(" not in pipeline
    assert "if scale_action" not in pipeline
    assert "if agent_route" not in pipeline

    ui_source = _function_source(ROOT / "ui" / "main_window.py", "_post_pipeline_routing")
    assert "turn_decision" in ui_source
    for forbidden in (
        "result.end_type",
        "result.intent",
        "result.relaxation_rec",
        "result.all_scales_completed",
        "_should_soft_recommend_relaxation",
    ):
        assert forbidden not in ui_source


def test_scale_policy_is_not_a_second_item_authority():
    source = (ROOT / "assessment" / "scale_policy.py").read_text(encoding="utf-8")
    assert "requested_item" not in source


def test_pipeline_result_contracts_are_immutable_value_objects():
    assert TurnDecision.model_config.get("frozen") is True
    assert RouterProposal.model_config.get("frozen") is True


def test_legacy_policy_decision_is_not_a_production_authority():
    production_files = (
        ROOT / "conversation" / "coordinator.py",
        ROOT / "services" / "pipeline.py",
        ROOT / "services" / "agent_service.py",
        ROOT / "ui" / "main_window.py",
    )
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        assert "PolicyDecision" not in source


def test_turn_policy_has_no_provider_or_ui_dependency():
    source = (ROOT / "conversation" / "turn_policy.py").read_text(encoding="utf-8")
    assert "services." not in source
    assert "ui." not in source
    assert "open(" not in source
    assert "requests" not in source
