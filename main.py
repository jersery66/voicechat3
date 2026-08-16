# Voice Chat Application - PySide6 Version
# Entry point for the heart doctor AI counseling system

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.main_window import MainWindow
from services.logger import setup_logging


def _initialize_error_monitor():
    from services.error_monitor import get_error_monitor

    get_error_monitor()


def _run_preflight() -> int | None:
    """Run the selected profile's preflight and return a failure status if needed."""
    from deployment.profiles import get_deployment_profile

    try:
        profile = get_deployment_profile()
    except Exception as exc:
        print(f"FATAL: Deployment profile could not be loaded: {exc}")
        return 2

    try:
        from scripts.check_config import run_check
        ok = run_check()
    except Exception as exc:
        if profile.strict_preflight:
            print(
                "FATAL: Deployment preflight could not complete for profile "
                f"'{profile.name}': {exc}"
            )
            return 2
        print(f"WARNING: Config check failed: {exc}")
        return None

    if not ok:
        if profile.strict_preflight:
            print(
                "FATAL: Deployment preflight failed for profile "
                f"'{profile.name}'. The application will not start."
            )
            return 2
        print("WARNING: Some configuration checks failed. The application may not work correctly.")
    return None


def main() -> int:
    setup_logging()
    _initialize_error_monitor()

    preflight_status = _run_preflight()
    if preflight_status is not None:
        return preflight_status

    # Print actual model configuration
    from config import (
        DIALOGUE_BACKEND, DIALOGUE_BASE_URL, OLLAMA_MODEL, print_model_status,
    )
    print(f"[VoiceChat] Using LLM model: {OLLAMA_MODEL}")
    print(f"[VoiceChat] Dialogue backend: {DIALOGUE_BACKEND} ({DIALOGUE_BASE_URL})")
    print_model_status()

    # High DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    # Set default font
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    # Create and show main window
    window = MainWindow()
    window.showMaximized()

    try:
        return app.exec()
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
