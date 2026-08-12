"""Game completion must distinguish an engine crash from a completed game."""

from services.game_service import GameService


def test_game_service_marks_crashed_game_as_incomplete(tmp_path, monkeypatch):
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
            pass

        def run(self):
            raise RuntimeError("game crashed")

    monkeypatch.setattr("services.game_service.ClinicalTracker", Tracker)
    monkeypatch.setattr("services.game_service.GameEngine", Engine)

    result = GameService().play_game(str(tmp_path))

    assert result["_completed"] is False
    assert result["fallback"] is True
