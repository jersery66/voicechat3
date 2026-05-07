import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.engine import GameEngine
from game.clinical_tracker import ClinicalTracker


class GameService:
    """Therapeutic game service. Runs game fullscreen via pygame, blocks until done."""

    def play_game(self, session_folder: str) -> dict:
        csv_path = os.path.join(session_folder, "game_clinical_data.csv")
        tracker = ClinicalTracker(csv_path)

        engine = GameEngine(tracker)
        results = engine.run()

        tracker.save_csv()
        print(f"[INFO] Game clinical data saved: {csv_path}")

        # Add difficulty metrics from the engine
        if hasattr(engine, 'difficulty_sys'):
            results.update(engine.difficulty_sys.get_metrics())

        return results


_game_service = None


def get_game_service() -> GameService:
    global _game_service
    if _game_service is None:
        _game_service = GameService()
    return _game_service
