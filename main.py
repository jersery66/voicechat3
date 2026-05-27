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


def main():
    setup_logging()
    from services.error_monitor import get_error_monitor
    get_error_monitor()

    # Pre-launch config check (non-blocking — warn but don't block startup)
    try:
        from scripts.check_config import run_check
        if not run_check():
            print("WARNING: Some configuration checks failed. The application may not work correctly.")
    except Exception as e:
        print(f"Config check skipped: {e}")

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
        sys.exit(app.exec())
    except SystemExit:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
