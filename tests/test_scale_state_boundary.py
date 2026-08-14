"""Static ownership gates for Phase 3 scale-state migration."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_has_no_second_business_authority_or_io_dependencies():
    source = _source("assessment/scale_runtime.py")
    forbidden = (
        "decide_action(",
        "route(",
        "TurnPolicy",
        "Router",
        "llm",
        "network",
        "services.pipeline",
        "ui.",
        "report_service",
        "stt",
        "tts",
    )
    assert all(marker not in source for marker in forbidden)


def test_runtime_uses_definition_accessors_not_scale_selection_heuristics():
    source = _source("assessment/scale_runtime.py")
    assert "should_administer" not in source
    assert "recommend_scale_candidates" not in source


def test_scale_policy_reads_registered_scale_names_from_canonical_manager():
    source = _source("assessment/scale_policy.py")
    assert "_SCALE_NAMES" not in source
    assert "get_scale_manager" in source


def test_pipeline_has_no_legacy_scale_owner_or_selection_calls():
    source = _source("services/pipeline.py")
    forbidden = (
        "from core.scale_fsm import",
        "delegate_property(",
        "ScaleState()",
        "self._scale_state",
        "_scale_answers =",
        "_active_scale =",
        "should_administer(",
        "recommend_scale_candidates(",
    )
    assert all(marker not in source for marker in forbidden)


def test_phase2_authority_and_phase1_safety_boundaries_remain_untouched():
    contracts = _source("conversation/contracts.py")
    pipeline = _source("services/pipeline.py")
    assert "class RouterProposal" in contracts
    assert "class TurnDecision" in contracts
    assert "requested_item" not in contracts
    assert "scale_score" not in contracts
    assert "safety/resources" not in pipeline
    assert "RouterProposal" in pipeline
    assert "TurnDecision" in pipeline


def test_no_phase4_or_phase5_authority_contracts_are_introduced():
    changed_sources = "\n".join(
        _source(path)
        for path in (
            "assessment/scale_runtime.py",
            "services/pipeline.py",
            "services/scales.py",
        )
    )
    forbidden = (
        "SessionEngine authority",
        "authoritative session migration",
        "ScaleRuntime.decide_action",
        "RouterProposalV2",
        "ScaleRuntimeActionPolicy",
    )
    assert all(marker not in changed_sources for marker in forbidden)
