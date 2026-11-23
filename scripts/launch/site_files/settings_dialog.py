"""设置对话框：麦克风选择、试录 2 秒看电平、界面缩放。

This file ships only in the site build. The deployed snapshot has no settings UI,
so a wrong microphone could previously be fixed only by hand-editing a batch file
on a machine with no network and no developer.

Saving writes the same `site-config.cmd` that the launcher already reads, and also
applies the microphone to the running process immediately — the ASR service
resolves the device at every recording start, so audio needs no restart. Only the
interface scale waits for the next launch.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout,
)

ROOT = Path(__file__).resolve().parents[1]
SITE_CONFIG = ROOT / "site-config.cmd"
MARKER = "REM ---- settings below this line take effect ----"
AUTO_LABEL = "自动选择（推荐）"

_LOOPBACK_MARKERS = ("stereo mix", "立体声混音", "loopback", "cable output",
                     "wave out mix", "virtual", "线路输入")


def _list_input_devices() -> list[tuple[int, str, bool]]:
    """[(index, name, is_loopback)] — empty when sounddevice is unavailable."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception:  # noqa: BLE001 - the dialog must still open
        return []
    found: list[tuple[int, str, bool]] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        name = str(device.get("name", f"设备 {index}"))
        low = name.lower()
        found.append((index, name, any(marker in low for marker in _LOOPBACK_MARKERS)))
    return found


def _read_saved_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if SITE_CONFIG.is_file():
        try:
            text = SITE_CONFIG.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            match = re.match(r'\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$',
                             line, re.IGNORECASE)
            if match:
                values[match.group(1).upper()] = match.group(2).strip()
    # The environment is what the running session actually uses, so it wins.
    for name in ("VOICECHAT_INPUT_DEVICE", "QT_SCALE_FACTOR"):
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def _write_saved_values(values: dict[str, str]) -> None:
    """Rewrite the editable block, keeping whatever header the file already has."""
    body = [f'set "{name}={value}"' for name, value in values.items() if value]
    text = ""
    if SITE_CONFIG.is_file():
        try:
            text = SITE_CONFIG.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    if MARKER in text:
        head = text.split(MARKER, 1)[0] + MARKER
    else:
        head = (
            "@echo off\r\n"
            "REM ============================================================\r\n"
            "REM  Site settings. Written by the in-app settings dialog.\r\n"
            "REM  ASCII only: cmd.exe reads this file in the console code page.\r\n"
            "REM ============================================================\r\n"
            f"{MARKER}"
        )
    SITE_CONFIG.write_text(head + "\r\n" + "\r\n".join(body) + "\r\n",
                           encoding="utf-8", newline="")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setStyleSheet("""
            QDialog { background-color: #F7FAF8; }
            QLabel { color: #2C3E50; }
            QComboBox, QDoubleSpinBox {
                border: 1px solid rgba(30,42,50,0.18); border-radius: 6px;
                padding: 4px 6px; background: white; font-size: 12px;
            }
            QPushButton {
                border-radius: 8px; padding: 7px 14px; font-size: 12px;
                border: 1px solid rgba(30,42,50,0.15); background: rgba(30,42,50,0.05);
            }
            QPushButton:hover { background: rgba(30,42,50,0.12); }
        """)
        self._saved = _read_saved_values()
        self._test_buffer = None
        self._test_error = ""
        self._build()

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel("本机设置")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        layout.addWidget(title)
        hint = QLabel("麦克风改完立刻生效；界面缩放需要下次启动（桌面图标）才生效。")
        hint.setStyleSheet("color: #7C8A82; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(self._section("录音设备（麦克风）"))
        self.device_combo = QComboBox()
        layout.addWidget(self.device_combo)
        tip = QLabel("标注 loopback / 立体声混音 / CABLE 的设备录的是扬声器声音，"
                     "不是说话声——选错会出现“能录但没有字”。")
        tip.setStyleSheet("color: #A94432; font-size: 11px;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("测试录音 2 秒")
        self.btn_test.clicked.connect(self._on_test)
        test_row.addWidget(self.btn_test)
        test_row.addStretch(1)
        layout.addLayout(test_row)

        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.test_label.setStyleSheet("color: #4A5A52; font-size: 11px;")
        layout.addWidget(self.test_label)

        layout.addWidget(self._section("界面缩放（下次启动生效）"))
        scale_row = QHBoxLayout()
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.75, 1.60)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)
        self.scale_spin.setSuffix(" ×")
        scale_row.addWidget(self.scale_spin)
        note = QLabel("1366×768 或系统缩放 150% 的机器用 0.85；1080p 以上保持 1.00")
        note.setStyleSheet("color: #4A5A52; font-size: 11px;")
        scale_row.addWidget(note)
        scale_row.addStretch(1)
        layout.addLayout(scale_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("保存")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        buttons.addWidget(self.btn_save)
        layout.addLayout(buttons)

        self._reload_devices()

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #1E2A32; font-size: 12px; font-weight: 600;")
        return label

    def _reload_devices(self) -> None:
        devices = _list_input_devices()
        self.device_combo.clear()
        self.device_combo.addItem(AUTO_LABEL, "auto")
        current = str(self._saved.get("VOICECHAT_INPUT_DEVICE", "auto")).strip()
        selected = 0
        for index, name, loopback in devices:
            label = f"[{index}] {name}" + ("   ← loopback，不要选" if loopback else "")
            self.device_combo.addItem(label, str(index))
            if current and current == str(index):
                selected = self.device_combo.count() - 1
        if not devices:
            self.device_combo.addItem("未检测到录音设备（检查声卡驱动）", "auto")
        self.device_combo.setCurrentIndex(selected)
        try:
            self.scale_spin.setValue(float(self._saved.get("QT_SCALE_FACTOR", "1.0")))
        except ValueError:
            self.scale_spin.setValue(1.0)

    # ------------------------------------------------------------- actions
    def _on_test(self) -> None:
        """Record two seconds from the selected device and report the level."""
        choice = self.device_combo.currentData()
        device = None if choice in (None, "auto") else int(choice)
        self.btn_test.setEnabled(False)
        self.test_label.setStyleSheet("color: #4A5A52; font-size: 11px;")
        self.test_label.setText("正在录音 2 秒，请对着麦克风说话…")
        self.repaint()

        try:
            import numpy as np
            import sounddevice as sd

            frames = 16000 * 2
            kwargs = {"samplerate": 16000, "channels": 1, "dtype": "float32",
                      "blocking": False}
            if device is not None:
                kwargs["device"] = device
            self._sd = sd
            self._np = np
            self._test_buffer = sd.rec(frames, **kwargs)
        except Exception as exc:  # noqa: BLE001 - any failure is worth showing
            self._finish_test(f"{type(exc).__name__}: {exc}")
            return
        QTimer.singleShot(2100, lambda: self._finish_test(""))

    def _finish_test(self, error: str) -> None:
        self.btn_test.setEnabled(True)
        if error:
            self._show_test_result(f"录音失败：{error}", ok=False)
            return
        try:
            self._sd.wait()
            audio = self._np.asarray(self._test_buffer)
            peak = float(self._np.max(self._np.abs(audio))) if audio.size else 0.0
            rms = float(self._np.sqrt(self._np.mean(audio ** 2))) if audio.size else 0.0
        except Exception as exc:  # noqa: BLE001
            self._show_test_result(f"录音失败：{type(exc).__name__}: {exc}", ok=False)
            return
        if peak < 1e-4:
            self._show_test_result(
                "没有收到声音（峰值 0.0000）。换一个设备再试；仍然无声请检查"
                " Windows 设置 → 隐私和安全性 → 麦克风，是否允许桌面应用访问。",
                ok=False)
        else:
            self._show_test_result(f"收到声音：峰值 {peak:.3f}，RMS {rms:.3f}", ok=True)

    def _show_test_result(self, text: str, *, ok: bool) -> None:
        self.test_label.setStyleSheet(
            f"color: {'#2F6B4F' if ok else '#A94432'}; font-size: 11px;")
        self.test_label.setText(text)

    def _on_save(self) -> None:
        choice = self.device_combo.currentData()
        device = "" if choice in (None, "auto") else str(choice)
        scale_value = self.scale_spin.value()
        scale = "" if abs(scale_value - 1.0) < 0.001 else f"{scale_value:.2f}"

        values = dict(self._saved)
        if device:
            values["VOICECHAT_INPUT_DEVICE"] = device
        else:
            values.pop("VOICECHAT_INPUT_DEVICE", None)
        if scale:
            values["QT_SCALE_FACTOR"] = scale
        else:
            values.pop("QT_SCALE_FACTOR", None)

        try:
            _write_saved_values(values)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入 site-config.cmd：{exc}")
            return

        # Apply the microphone to this session immediately: the ASR service
        # resolves the device every time a recording starts.
        if device:
            os.environ["VOICECHAT_INPUT_DEVICE"] = device
        else:
            os.environ.pop("VOICECHAT_INPUT_DEVICE", None)
        if scale:
            os.environ["QT_SCALE_FACTOR"] = scale
        else:
            os.environ.pop("QT_SCALE_FACTOR", None)

        tail = "\n界面缩放请关闭程序后，用桌面上的“小微老师”重新打开。\n" if scale else ""
        QMessageBox.information(
            self, "已保存",
            "麦克风设置已保存，下一次录音即生效。" + tail + f"\n写入文件：{SITE_CONFIG}")
        self.accept()


def open_settings_dialog(parent=None) -> bool:
    dialog = SettingsDialog(parent)
    return dialog.exec() == QDialog.Accepted
