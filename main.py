# Voice Chat Application - PySide6 Version
# Entry point for the heart doctor AI counseling system

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"


def _autostart() -> None:
    """Detect this machine and pick a runtime config before ``config`` is imported.

    ``config.py`` reads the environment at import time, so this has to run first.
    An explicit ``VOICECHAT_RUNTIME_CONFIG`` is always honoured unchanged; when
    nothing is set, the machine is probed, the model stack is started if needed,
    and ``config/runtime.local.yaml`` is written. Any failure is a warning, never
    a hard stop: a broken probe must not be able to prevent the GUI from opening.

    Skipped under pytest: importing this module during a test run would probe the
    developer's machine and export ``VOICECHAT_RUNTIME_CONFIG`` for every later
    test in the session, which silently re-points config assertions at the local
    file. Tests that exercise autostart call it directly.
    """
    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from deployment.autostart import ensure, start_watchdog

        ensure()
        # WSL2 may reclaim the distro mid-session; keep the services alive for
        # as long as the window is open instead of failing on the next turn.
        start_watchdog()
    except Exception as exc:  # noqa: BLE001 - startup must survive anything here
        print(f"WARNING: autostart skipped ({type(exc).__name__}: {exc})")


_autostart()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.main_window import MainWindow
from services.logger import setup_logging


def _initialize_error_monitor():
    from services.error_monitor import get_error_monitor

    get_error_monitor()


def _apply_user_settings() -> None:
    """Apply config/user_settings.json before Qt exists.

    Interface scale has to be in the environment before ``QApplication`` is
    constructed, and the audio overrides have to be set before the speech
    services resolve their devices — so this runs at the very top of ``main()``.
    Everything is optional: a missing or broken file just means defaults.
    """
    try:
        from services.user_settings import load_settings

        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - settings must never block startup
        print(f"WARNING: 读取用户设置失败（{type(exc).__name__}: {exc}），使用默认值")
        return

    for warning in settings.warnings:
        print(f"WARNING: 用户设置：{warning}")

    scale = settings.scale
    if abs(scale - 1.0) > 0.001 and "QT_SCALE_FACTOR" not in os.environ:
        # Qt scales fonts and layouts together; doing it here keeps every
        # hard-coded pixel size proportional instead of half-scaled.
        os.environ["QT_SCALE_FACTOR"] = f"{scale:.2f}"
        print(f"[VoiceChat] 界面缩放 {scale:.2f}×")

    input_device = settings.input_device
    if input_device and input_device != "auto":
        os.environ["VOICECHAT_INPUT_DEVICE"] = str(input_device)
        print(f"[VoiceChat] 录音设备: {input_device}")
    output_device = settings.output_device
    if output_device and output_device != "default":
        os.environ["VOICECHAT_OUTPUT_DEVICE"] = str(output_device)
        print(f"[VoiceChat] 播放设备: {output_device}")


def _run_preflight() -> int | None:
    """Run the configured runtime's preflight and return a failure status if needed."""
    from deployment.runtime_config import get_runtime_config

    try:
        runtime = get_runtime_config()
    except Exception as exc:
        print(f"FATAL: Runtime configuration could not be loaded: {exc}")
        return 2

    try:
        from scripts.check_config import run_check
        ok = run_check()
    except Exception as exc:
        if runtime.runtime.strict_preflight:
            print(
                "FATAL: Deployment preflight could not complete for runtime config "
                f"{runtime.source_path}: {exc}"
            )
            return 2
        print(f"WARNING: Config check failed: {exc}")
        return None

    if not ok:
        if runtime.runtime.strict_preflight:
            print(
                "FATAL: Deployment preflight failed for runtime config "
                f"{runtime.source_path}. The application will not start."
            )
            return 2
        print("WARNING: Some configuration checks failed. The application may not work correctly.")
    return None


def main() -> int:
    setup_logging()
    _apply_user_settings()
    _initialize_error_monitor()

    preflight_status = _run_preflight()
    if preflight_status is not None:
        return preflight_status

    # Print actual model configuration
    from config import (
        DIALOGUE_PROVIDER, DIALOGUE_BASE_URL, DIALOGUE_MODEL, print_model_status,
    )
    print(f"[VoiceChat] Using LLM model: {DIALOGUE_MODEL}")
    print(f"[VoiceChat] Dialogue provider: {DIALOGUE_PROVIDER} ({DIALOGUE_BASE_URL})")
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
    try:
        from services.user_settings import load_settings

        preferences = load_settings()
        if preferences.get("ui.start_maximized", True):
            window.showMaximized()
        else:
            width = int(preferences.get("ui.start_width", 1280))
            height = int(preferences.get("ui.start_height", 800))
            available = app.primaryScreen().availableGeometry()
            window.resize(min(width, available.width()), min(height, available.height()))
            window.move(
                available.x() + max(0, (available.width() - window.width()) // 2),
                available.y() + max(0, (available.height() - window.height()) // 2),
            )
            window.show()
    except Exception:
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
