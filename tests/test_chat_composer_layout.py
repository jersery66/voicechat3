"""Composer layout: the bottom bar hugs its content and the field grows.

Both regressions were visible only by looking at the window: the composer
frame absorbed the space the message list left over, which turned it into a
tall white slab with the buttons stranded at the bottom, and the input kept a
fixed 60 px height so a single line sat above four lines of empty white.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QSizePolicy

from ui.chat_panel import ChatInput, ChatPanel

PANEL_SIZE = (470, 833)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _panel(qapp):
    panel = ChatPanel()
    panel.resize(*PANEL_SIZE)
    panel.show()
    qapp.processEvents()
    return panel


def _footer(panel: ChatPanel) -> QFrame:
    for frame in panel.findChildren(QFrame):
        if frame.objectName() == "chatFooter":
            return frame
    raise AssertionError("chatFooter not found")


def test_composer_height_follows_its_content(qapp):
    panel = _panel(qapp)
    footer = _footer(panel)
    assert footer.sizePolicy().verticalPolicy() == QSizePolicy.Maximum
    assert footer.height() == footer.sizeHint().height()


def test_message_area_keeps_the_remaining_height(qapp):
    panel = _panel(qapp)
    footer = _footer(panel)
    # The card is 833 px tall; a composer of roughly 100 px must not eat the
    # list, which used to happen while the frame could grow.
    assert footer.height() < 140
    assert panel.scroll_area.height() > PANEL_SIZE[1] - 200


def test_input_starts_at_a_single_line(qapp):
    panel = _panel(qapp)
    assert panel.text_input.height() == ChatInput.MIN_HEIGHT
    assert panel.btn_send.height() == ChatInput.MIN_HEIGHT


def test_input_grows_with_wrapped_text(qapp):
    panel = _panel(qapp)
    one_line = panel.text_input.height()
    panel.text_input.setPlainText("第一行\n第二行\n第三行")
    qapp.processEvents()
    assert panel.text_input.height() > one_line


def test_input_height_is_capped(qapp):
    panel = _panel(qapp)
    panel.text_input.setPlainText("\n".join(str(i) for i in range(20)))
    qapp.processEvents()
    assert panel.text_input.height() == ChatInput.MAX_HEIGHT


def test_clearing_the_field_restores_one_line(qapp):
    panel = _panel(qapp)
    panel.text_input.setPlainText("\n".join(str(i) for i in range(6)))
    qapp.processEvents()
    assert panel.text_input.height() > ChatInput.MIN_HEIGHT
    panel.text_input.clear()
    qapp.processEvents()
    assert panel.text_input.height() == ChatInput.MIN_HEIGHT
    # The composer has to shrink back as well, not just the field.
    assert _footer(panel).height() == _footer(panel).sizeHint().height()
