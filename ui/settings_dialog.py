"""Settings dialog: audio devices, interface scale, and the raw JSON.

Everything here is meant to be fixable on site by a technician. The device
dropdowns avoid the usual trap of listing every sound endpoint by index — they
show the device name with its index and mark loopback endpoints, because
"立体声混音" recording the speakers is the single most common reason a
microphone appears to work but transcribes nothing.

The advanced tab edits the same JSON file by hand, so an option that the dialog
does not expose can still be changed, and a value set by hand is visible here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from services.user_settings import UserSettings

_AUTO = "自动选择（推荐）"


def _audio_devices():
    """[(index, name, is_input, is_output, is_loopback)] — empty when unavailable."""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 - the dialog must open even without audio
        return []
    try:
        listing = sd.query_devices()
    except Exception:  # noqa: BLE001
        return []
    loopback_markers = ("stereo mix", "立体声混音", "loopback", "cable output", "wave out mix")
    devices = []
    for index, device in enumerate(listing):
        name = str(device.get("name", f"设备 {index}"))
        devices.append((
            index,
            name,
            int(device.get("max_input_channels", 0)) > 0,
            int(device.get("max_output_channels", 0)) > 0,
            any(marker in name.lower() for marker in loopback_markers),
        ))
    return devices


class SettingsDialog(QDialog):
    """Modal settings editor. ``saved`` tells the caller a restart is needed."""

    def __init__(self, settings: UserSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.saved = False
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(680, 560)
        self._setup_ui()
        self._load_into_widgets()

    # ------------------------------------------------------------------ ui
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel("本机设置")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        layout.addWidget(title)

        hint = QLabel("保存后立即写入 config\\user_settings.json；标注“需重启”的项在下次启动生效。")
        hint.setStyleSheet("color: #7C8A82; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        tabs = QTabWidget()
        tabs.addTab(self._build_audio_tab(), "音频")
        tabs.addTab(self._build_display_tab(), "显示")
        tabs.addTab(self._build_advanced_tab(), "高级（手动编辑）")
        layout.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        self.btn_reset = QPushButton("恢复默认")
        self.btn_reset.clicked.connect(self._on_reset)
        buttons.addWidget(self.btn_reset)
        buttons.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("保存")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        buttons.addWidget(self.btn_save)
        layout.addLayout(buttons)

    def _build_audio_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(8)

        group_in = QGroupBox("麦克风（录音输入）")
        inner = QVBoxLayout(group_in)
        self.input_combo = QComboBox()
        self.input_combo.setMinimumWidth(420)
        inner.addWidget(self.input_combo)
        tip = QLabel("“立体声混音/loopback”类设备录的是扬声器声音，不是说话声，通常不要选。")
        tip.setStyleSheet("color: #A94432; font-size: 11px;")
        tip.setWordWrap(True)
        inner.addWidget(tip)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新设备")
        self.btn_refresh.clicked.connect(self._reload_devices)
        row.addWidget(self.btn_refresh)
        self.btn_test = QPushButton("测试录音 2 秒")
        self.btn_test.clicked.connect(self._on_test_mic)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        inner.addLayout(row)

        self.test_label = QLabel("")
        self.test_label.setStyleSheet("color: #2F6B4F; font-size: 11px;")
        self.test_label.setWordWrap(True)
        inner.addWidget(self.test_label)
        box.addWidget(group_in)

        group_out = QGroupBox("扬声器（语音播放）")
        out_box = QVBoxLayout(group_out)
        self.output_combo = QComboBox()
        self.output_combo.setMinimumWidth(420)
        out_box.addWidget(self.output_combo)
        out_note = QLabel("“default”使用 Windows 当前默认输出设备；换了耳机也可以留 default。")
        out_note.setStyleSheet("color: #7C8A82; font-size: 11px;")
        out_note.setWordWrap(True)
        out_box.addWidget(out_note)
        box.addWidget(group_out)

        box.addStretch(1)
        return page

    def _build_display_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(10)

        group = QGroupBox("界面缩放（需重启）")
        inner = QVBoxLayout(group)
        row = QHBoxLayout()
        row.addWidget(QLabel("缩放比例"))
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.75, 1.6)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)
        self.scale_spin.setSuffix(" ×")
        row.addWidget(self.scale_spin)
        row.addStretch(1)
        inner.addLayout(row)
        note = QLabel(
            "页面显示不全、文字过大或过小时调整这里：\n"
            "· 1366×768 或系统缩放 150% 的机器，建议 0.85\n"
            "· 1920×1080 及以上保持 1.00\n"
            "· 高分屏字太小可调到 1.10–1.20"
        )
        note.setStyleSheet("color: #4A5A52; font-size: 11px;")
        note.setWordWrap(True)
        inner.addWidget(note)
        box.addWidget(group)

        group2 = QGroupBox("启动方式")
        inner2 = QVBoxLayout(group2)
        self.maximized_check = QCheckBox("启动时最大化")
        inner2.addWidget(self.maximized_check)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("窗口尺寸"))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(800, 4096)
        self.width_spin.setDecimals(0)
        self.width_spin.setSuffix(" px")
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(560, 2160)
        self.height_spin.setDecimals(0)
        self.height_spin.setSuffix(" px")
        size_row.addWidget(self.height_spin)
        size_row.addStretch(1)
        inner2.addLayout(size_row)
        self.maximized_check.toggled.connect(
            lambda checked: (self.width_spin.setEnabled(not checked),
                             self.height_spin.setEnabled(not checked))
        )
        box.addWidget(group2)

        box.addStretch(1)
        return page

    def _build_advanced_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(8)

        note = QLabel(
            f"配置文件：{self.settings.path}\n"
            "可直接编辑下面的 JSON 再点“应用文本”；也可以点“打开所在文件夹”用记事本改。"
            "写错会提示语法错误，不会覆盖原文件。"
        )
        note.setStyleSheet("color: #4A5A52; font-size: 11px;")
        note.setWordWrap(True)
        box.addWidget(note)

        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setFont(QFont("Consolas", 9))
        self.raw_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        wrapper = QScrollArea()
        wrapper.setWidgetResizable(True)
        wrapper.setWidget(self.raw_edit)
        box.addWidget(wrapper, 1)

        row = QHBoxLayout()
        self.btn_apply_text = QPushButton("应用文本")
        self.btn_apply_text.clicked.connect(self._on_apply_text)
        row.addWidget(self.btn_apply_text)
        self.btn_reload_text = QPushButton("重新读取文件")
        self.btn_reload_text.clicked.connect(self._on_reload_text)
        row.addWidget(self.btn_reload_text)
        self.btn_open_folder = QPushButton("打开所在文件夹")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        row.addWidget(self.btn_open_folder)
        row.addStretch(1)
        box.addLayout(row)
        return page

    # -------------------------------------------------------------- loading
    def _load_into_widgets(self) -> None:
        self._reload_devices()
        self.scale_spin.setValue(self.settings.scale)
        self.maximized_check.setChecked(bool(self.settings.get("ui.start_maximized", True)))
        self.width_spin.setValue(float(self.settings.get("ui.start_width", 1280)))
        self.height_spin.setValue(float(self.settings.get("ui.start_height", 800)))
        self.width_spin.setEnabled(not self.maximized_check.isChecked())
        self.height_spin.setEnabled(not self.maximized_check.isChecked())
        self.raw_edit.setPlainText(self.settings.raw_text())

    def _reload_devices(self) -> None:
        devices = _audio_devices()
        self._devices = devices
        self.input_combo.clear()
        self.input_combo.addItem(_AUTO, "auto")
        self.output_combo.clear()
        self.output_combo.addItem("系统默认（default）", "default")

        for index, name, is_input, is_output, is_loopback in devices:
            if is_input:
                label = f"[{index}] {name}" + ("（loopback，录的是扬声器）" if is_loopback else "")
                self.input_combo.addItem(label, index)
            if is_output:
                self.output_combo.addItem(f"[{index}] {name}", index)

        if not devices:
            self.input_combo.addItem("未检测到音频设备（sounddevice 不可用？）", "auto")
            self.test_label.setText("无法枚举音频设备，请检查声卡驱动。")

        self._select_current(self.input_combo, self.settings.input_device, default_value="auto")
        self._select_current(self.output_combo, self.settings.output_device, default_value="default")

    @staticmethod
    def _select_current(combo: QComboBox, value: str, *, default_value: str) -> None:
        text = str(value).strip()
        for i in range(combo.count()):
            data = combo.itemData(i)
            if str(data) == text:
                combo.setCurrentIndex(i)
                return
            if text.isdigit() and str(data) == text:
                combo.setCurrentIndex(i)
                return
        if text and not text.isdigit():
            for i in range(combo.count()):
                if text.lower() in combo.itemText(i).lower():
                    combo.setCurrentIndex(i)
                    return
        for i in range(combo.count()):
            if str(combo.itemData(i)) == default_value:
                combo.setCurrentIndex(i)
                return

    # -------------------------------------------------------------- actions
    def _on_test_mic(self) -> None:
        """Record ~2 s from the selected device and report the level.

        A silent buffer almost always means the wrong endpoint was chosen, so the
        result says which device was opened rather than just "failed".
        """
        selection = self.input_combo.currentData()
        index = None if selection in (None, "auto") else int(selection)
        self.btn_test.setEnabled(False)
        self.test_label.setText("正在录音 2 秒…")
        self.repaint()
        try:
            import numpy as np
            import sounddevice as sd

            rate = 16000
            seconds = 2
            frames = int(rate * seconds)
            kwargs = {"samplerate": rate, "channels": 1, "dtype": "float32"}
            if index is not None:
                kwargs["device"] = index
            audio = sd.rec(frames, **kwargs)
            sd.wait()
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
            opened = sd.query_devices(index if index is not None else sd.default.device[0])
            name = opened.get("name") if isinstance(opened, dict) else index
            if peak < 1e-4:
                self.test_label.setText(
                    f"没有收到声音（峰值 {peak:.4f}）。当前设备：{name}。"
                    "请确认选的是麦克风而不是立体声混音，并检查系统隐私设置里的麦克风权限。"
                )
                self.test_label.setStyleSheet("color: #A94432; font-size: 11px;")
            else:
                self.test_label.setText(
                    f"收到声音：峰值 {peak:.3f}，RMS {rms:.3f}。设备：{name}"
                )
                self.test_label.setStyleSheet("color: #2F6B4F; font-size: 11px;")
        except Exception as exc:  # noqa: BLE001 - any failure is worth showing
            self.test_label.setText(f"录音失败：{type(exc).__name__}: {exc}")
            self.test_label.setStyleSheet("color: #A94432; font-size: 11px;")
        finally:
            self.btn_test.setEnabled(True)

    def _on_apply_text(self) -> None:
        ok, message = self.settings.replace_raw(self.raw_edit.toPlainText())
        if not ok:
            QMessageBox.warning(self, "配置有误", message)
            return
        self.raw_edit.setPlainText(self.settings.raw_text())
        self._load_into_widgets()
        QMessageBox.information(self, "已应用", message + "；记得点“保存”写入文件。")

    def _on_reload_text(self) -> None:
        self.settings.load()
        self._load_into_widgets()

    def _on_open_folder(self) -> None:
        folder = self.settings.path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not self.settings.path.exists():
                self.settings.save()
            os.startfile(str(folder))  # noqa: S606 - opening a folder for the operator
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "无法打开", str(exc))

    def _on_reset(self) -> None:
        if QMessageBox.question(self, "恢复默认", "把所有设置恢复为默认值？") != QMessageBox.Yes:
            return
        self.settings.reset()
        self._load_into_widgets()

    def _on_save(self) -> None:
        input_value = self.input_combo.currentData()
        output_value = self.output_combo.currentData()
        self.settings.update({
            "audio.input_device": "auto" if input_value in (None, "auto") else int(input_value),
            "audio.output_device": "default" if output_value in (None, "default") else int(output_value),
            "ui.scale": round(self.scale_spin.value(), 2),
            "ui.start_maximized": self.maximized_check.isChecked(),
            "ui.start_width": int(self.width_spin.value()),
            "ui.start_height": int(self.height_spin.value()),
        })
        if not self.settings.save():
            QMessageBox.warning(self, "保存失败", "\n".join(self.settings.warnings) or "无法写入配置文件")
            return
        self.saved = True
        self.accept()


def open_settings_dialog(parent, settings: UserSettings) -> bool:
    """Show the dialog; returns True when something was saved."""
    dialog = SettingsDialog(settings, parent)
    dialog.exec()
    return dialog.saved
