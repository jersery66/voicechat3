"""Guard the MainWindow's migration to the coordinator boundary."""

from pathlib import Path


def test_main_window_routes_turns_through_conversation_coordinator():
    source = Path("ui/main_window.py").read_text(encoding="utf-8")

    assert "coordinator.execute(config, safe_put)" in source
    assert "self.pipeline.execute(config, safe_put)" not in source
