"""Guard the MainWindow's migration to the coordinator boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_window_routes_turns_through_conversation_coordinator():
    source = (ROOT / "ui/main_window.py").read_text(encoding="utf-8")

    assert "coordinator.execute(config, safe_put)" in source
    assert "coordinator.assess_transcript" not in source
    assert "build_safety_gate" not in source
    assert "show_crisis" not in source
    assert "self.pipeline.execute(config, safe_put)" not in source


def test_main_window_loads_the_profile_selected_llm_factory():
    source = (ROOT / "ui/main_window.py").read_text(encoding="utf-8")

    assert "from services.llm_factory import build_llm_service" in source
    assert "self.llm_service = build_llm_service()" in source


def test_main_reports_the_selected_dialogue_endpoint_not_an_ollama_only_host():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "DIALOGUE_BACKEND" in source
    assert "DIALOGUE_BASE_URL" in source


def test_main_window_has_no_crisis_ui_protocol():
    source = (ROOT / "ui/main_window.py").read_text(encoding="utf-8")
    for symbol in (
        "CRISIS_HOTLINES",
        "CrisisDialog",
        "show_crisis",
        "_show_crisis_dialog",
        "build_safety_gate",
        "assess_transcript",
        "EndType.SAFETY",
    ):
        assert symbol not in source
