"""Layout regressions that produced broken-looking windows."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_resize_keeps_the_loading_overlay_window_sized():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = source.split("def resizeEvent", 1)[1].split("def _background_offset_x", 1)[0]
    # The overlay was sized once from the 640x480 default central rect and
    # stayed as a small card in the top-left corner after the window grew.
    assert "overlay.setGeometry" in body
    assert "central.rect()" in body


def test_loading_overlay_paints_its_scrim():
    source = (ROOT / "ui" / "loading_screen.py").read_text(encoding="utf-8")
    # Plain QWidgets ignore QSS backgrounds without styled-background painting.
    assert "WA_StyledBackground" in source


def test_chat_column_scales_up_on_wide_screens():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = source.split("def resizeEvent", 1)[1].split("def _background_offset_x", 1)[0]
    assert "760" in body  # upper bound, not the old phone-like 560
