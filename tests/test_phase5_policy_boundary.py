"""Static Phase 5 authority boundaries."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    node = next(
        item for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_router_prompt_requires_explicit_game_request():
    source = _function_source(ROOT / "services" / "agent_service.py", "route_conversation_actions")
    assert "无聊时：action=\"recommend_game\"" not in source
    assert "明确提出想玩游戏" in source


def test_pipeline_does_not_emit_timeout_ask_outside_session_engine():
    source = _function_source(ROOT / "services" / "pipeline.py", "execute")
    assert 'emit("time_limit_ask"' not in source


def test_pipeline_does_not_read_report_timeout_marker_as_a_second_policy():
    source = (ROOT / "services" / "pipeline.py").read_text(encoding="utf-8")
    assert "report.is_over_limit()" not in source


def test_ui_does_not_write_engine_timeout_markers_into_report_service():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "report_service.time_limit_prompt_shown =" not in source
    assert "report_service.continued_after_time_limit =" not in source
    assert "report_service.time_warning_shown =" not in source


def test_explicit_end_ui_path_does_not_enter_readiness_policy():
    source = _function_source(ROOT / "ui" / "main_window.py", "_post_pipeline_routing")
    assert "end_session_request" in source
    queue_source = _function_source(ROOT / "ui" / "main_window.py", "process_queue")
    assert "_request_end_with_readiness_check(et" not in queue_source


def test_post_relaxation_end_choice_is_direct():
    source = _function_source(ROOT / "ui" / "main_window.py", "_on_end_chosen")
    assert "allow_force_relaxation=False" in source


def test_post_relaxation_dialog_resumes_runtime_instead_of_timeout_ack():
    event_source = _function_source(ROOT / "ui" / "main_window.py", "_handle_engine_event")
    ask_source = _function_source(ROOT / "ui" / "main_window.py", "_ask_continue_or_end")
    assert "_ask_continue_or_end(timeout=False)" in event_source
    assert "timeout=timeout" in ask_source
    assert "_on_continue_chosen" in ask_source


def test_pipeline_worker_does_not_resume_scale_before_turn_policy():
    source = _function_source(ROOT / "ui" / "main_window.py", "_run_pipeline")
    assert "runtime.resume()" not in source


def test_session_engine_does_not_decide_relaxation_eligibility():
    source = _function_source(ROOT / "app" / "engine.py", "_handle_end_session")
    assert "MIN_ROUNDS_FOR_RELAXATION" not in source
    assert "explicit_relaxation_requested" not in source
    assert "RouterProposal" not in source
    assert "game_requires_explicit_request" not in source
    assert "RelaxationRecommendedEvent" not in source
    assert "forced=True" not in source


def test_legacy_session_fsm_does_not_choose_forced_relaxation():
    source = _function_source(ROOT / "core" / "session_fsm.py", "evaluate_session_end")
    assert "force_relaxation" not in source
    assert "has_forced_relaxation_rec" not in source


def test_main_window_resume_path_delegates_to_scale_runtime():
    source = _function_source(ROOT / "ui" / "main_window.py", "_on_continue_chosen")
    assert "resume_scale_after_relaxation" in source


def test_end_relaxation_tag_is_not_selected_by_secondary_model():
    source = _function_source(ROOT / "ui" / "main_window.py", "_get_end_relaxation_tag")
    assert "relaxation_tool" not in source
    assert ".execute(" not in source


def test_pipeline_keeps_only_typed_proactive_candidate_signal():
    source = (ROOT / "services" / "pipeline.py").read_text(encoding="utf-8")
    forbidden = {
        "self._pending_relaxation_after_scale",
        "self._relaxation_candidate",
        "self._game_candidate",
        "self._game_recommended_this_session",
        "self._post_scale_relaxation_done",
    }
    assert not any(marker in source for marker in forbidden)
