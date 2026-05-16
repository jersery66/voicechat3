import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.logger import get_logger
from game.engine import GameEngine
from game.clinical_tracker import ClinicalTracker

logger = get_logger(__name__)


class GameService:
    """Therapeutic game service. Runs game fullscreen via pygame, blocks until done."""

    def play_game(self, session_folder: Optional[str] = None) -> dict:
        """Run the game and return summary metrics.

        Args:
            session_folder: Optional directory in which to persist clinical
                data CSV. When omitted, falls back to the active
                ``DataManager`` session directory or the project's
                ``test_output`` folder.
        """
        if not session_folder:
            try:
                from data.data_manager import get_data_manager
                dm = get_data_manager()
                session_folder = dm.session_dir
            except Exception as e:
                logger.debug(f"Could not resolve session_dir: {e}")
        if not session_folder:
            project_root = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            session_folder = os.path.join(project_root, "test_output")
        os.makedirs(session_folder, exist_ok=True)

        csv_path = os.path.join(session_folder, "game_clinical_data.csv")
        tracker = ClinicalTracker(csv_path)

        engine = GameEngine(tracker)
        try:
            results = engine.run()
        except Exception as e:
            logger.error(f"Game engine crashed: {e}", exc_info=True)
            results = tracker.get_summary_metrics() if hasattr(tracker, 'get_summary_metrics') else {}

        try:
            tracker.save_csv()
            logger.info(f"Game clinical data saved: {csv_path}")
        except Exception as e:
            logger.warning(f"Failed to save tracker CSV: {e}")

        # Add difficulty metrics from the engine
        if hasattr(engine, 'difficulty_sys') and engine.difficulty_sys is not None:
            try:
                results.update(engine.difficulty_sys.get_metrics())
            except Exception as e:
                logger.debug(f"Could not append difficulty metrics: {e}")

        return results

    def launch(self, session_folder: Optional[str] = None) -> dict:
        """Alias for :meth:`play_game` kept for UI/back-compat callers."""
        return self.play_game(session_folder)


_game_service = None


def get_game_service() -> GameService:
    global _game_service
    if _game_service is None:
        _game_service = GameService()
    return _game_service
