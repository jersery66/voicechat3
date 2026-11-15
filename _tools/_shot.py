"""Capture the primary screen to a PNG (read-only diagnostic)."""
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

out = sys.argv[1] if len(sys.argv) > 1 else r"D:\voicechat3\logs\gui_shot.png"
app = QApplication([])
screen = QGuiApplication.primaryScreen()
if screen is None:
    print("NO_SCREEN")
    raise SystemExit(2)
pix = screen.grabWindow(0)
pix.save(out, "PNG")
print("saved", out, pix.width(), "x", pix.height())
