"""Render the deployed UI to PNGs so the layout can be inspected without a screen."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "FUNASR_MODEL_PATH", r"D:\戒毒所\models\Fun-ASR-Nano-2512")
os.environ.setdefault("VOXCPM_MODEL_PATH", r"D:\戒毒所\models\VoxCPM2")

from PySide6.QtWidgets import QApplication  # noqa: E402

from services.logger import setup_logging  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

setup_logging()
app = QApplication(sys.argv)
window = MainWindow()
window.resize(1366, 768)
window.show()
app.processEvents()
if getattr(window, "loading_screen", None) is not None:
    window.loading_screen.hide()
app.processEvents()

out = ROOT / "docs" / "site-ui"
out.mkdir(parents=True, exist_ok=True)

for name, size in (("1-full-1366x768", (1366, 768)),
                   ("2-narrow-900x600", (900, 600))):
    window.resize(*size)
    app.processEvents()
    app.processEvents()
    pixmap = window.grab()
    path = out / f"{name}.png"
    pixmap.save(str(path))
    print(f"saved {path}  {pixmap.width()}x{pixmap.height()}")

panel = window.control_panel
panel_pixmap = panel.grab()
panel_path = out / "3-control-panel.png"
panel_pixmap.save(str(panel_path))
print(f"saved {panel_path}  {panel_pixmap.width()}x{panel_pixmap.height()}")

chat = window.chat_panel
chat_pixmap = chat.grab()
chat_path = out / "4-chat-panel.png"
chat_pixmap.save(str(chat_path))
print(f"saved {chat_path}  {chat_pixmap.width()}x{chat_pixmap.height()}")
