# Session Review - Researcher dialog for reviewing past counseling sessions

import json
import os
import re
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QTabWidget,
    QComboBox, QListWidget, QListWidgetItem, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QScrollArea, QGroupBox, QFormLayout, QSizePolicy
)
from PySide6.QtCore import Qt

from services.logger import get_logger

logger = get_logger(__name__)

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    import sounddevice as sd
    import soundfile as sf
    SOUND_AVAILABLE = True
except ImportError:
    SOUND_AVAILABLE = False


EMOTION_COLORS = {
    "neutral": "#4CAF50", "hopeful": "#8BC34A", "grateful": "#CDDC39",
    "anxious": "#FF9800", "stressed": "#FF5722", "angry": "#F44336",
    "depressed": "#9C27B0", "fearful": "#E91E63", "lonely": "#795548",
    "confused": "#607D8B", "sad": "#3F51B5",
}


class SessionReviewDialog(QDialog):
    """Dialog for reviewing past counseling sessions."""

    def __init__(self, data_root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史会话回顾")
        self.setMinimumSize(1100, 750)
        self.data_root = Path(data_root)
        self._current_metadata = None
        self._current_report = None
        self._current_session_path = None
        self._audio_lock = threading.Lock()
        self._setup_ui()
        self._load_subject_list()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Left panel: session selector
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("被试编号:"))
        self.subject_combo = QComboBox()
        self.subject_combo.currentTextChanged.connect(self._on_subject_changed)
        left_layout.addWidget(self.subject_combo)

        left_layout.addWidget(QLabel("会话列表:"))
        self.session_list = QListWidget()
        self.session_list.currentRowChanged.connect(self._on_session_selected)
        left_layout.addWidget(self.session_list)

        left_panel.setFixedWidth(220)
        layout.addWidget(left_panel)

        # Right panel: tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._build_conversation_tab(), "对话回顾")
        self.tab_widget.addTab(self._build_emotion_tab(), "情绪曲线")
        self.tab_widget.addTab(self._build_scale_tab(), "量表结果")
        self.tab_widget.addTab(self._build_info_tab(), "会话信息")
        layout.addWidget(self.tab_widget, 1)

    def _build_conversation_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(QWidget())
        scroll.widget().setLayout(QVBoxLayout())
        scroll.widget().layout().setAlignment(Qt.AlignTop)
        scroll.widget().layout().setSpacing(8)
        self._conversation_container = scroll.widget()
        return scroll

    def _build_emotion_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        if MATPLOTLIB_AVAILABLE:
            self._emotion_figure = Figure(figsize=(8, 5))
            self._emotion_canvas = FigureCanvasQTAgg(self._emotion_figure)
            layout.addWidget(self._emotion_canvas)
        else:
            layout.addWidget(QLabel("matplotlib 未安装，无法显示情绪曲线"))
        return container

    def _build_scale_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        self._scale_table = QTableWidget()
        self._scale_table.setColumnCount(4)
        self._scale_table.setHorizontalHeaderLabels(["量表", "总分", "严重程度", "题目数"])
        self._scale_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._scale_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self._scale_table)
        return container

    def _build_info_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._info_layout = QFormLayout(container)
        self._info_layout.setSpacing(10)
        self._info_labels = {}
        for key in ["被试编号", "日期", "开始时间", "结束时间", "时长(分钟)",
                     "对话轮数", "结束类型", "放松训练", "风险评估"]:
            label = QLabel("--")
            label.setWordWrap(True)
            self._info_labels[key] = label
            self._info_layout.addRow(f"{key}:", label)
        scroll.setWidget(container)
        return scroll

    def _load_subject_list(self):
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        self.subject_combo.addItem("全部")

        subjects = set()
        for date_dir in self.data_root.iterdir():
            if date_dir.is_dir() and re.match(r'\d{4}-\d{2}-\d{2}', date_dir.name):
                for subject_dir in date_dir.iterdir():
                    if subject_dir.is_dir():
                        subjects.add(subject_dir.name)

        for s in sorted(subjects):
            self.subject_combo.addItem(s)
        self.subject_combo.blockSignals(False)
        self._on_subject_changed("全部")

    def _on_subject_changed(self, subject_filter: str):
        self.session_list.clear()
        sessions = []
        for date_dir in sorted(self.data_root.iterdir(), reverse=True):
            if not date_dir.is_dir() or not re.match(r'\d{4}-\d{2}-\d{2}', date_dir.name):
                continue
            for subject_dir in sorted(date_dir.iterdir()):
                if not subject_dir.is_dir():
                    continue
                if subject_filter != "全部" and subject_dir.name != subject_filter:
                    continue
                meta_path = subject_dir / "metadata.json"
                if meta_path.exists():
                    sessions.append((date_dir.name, subject_dir.name, subject_dir))

        for date, folder, path in sessions:
            item = QListWidgetItem(f"{date} / {folder}")
            item.setData(Qt.UserRole, str(path))
            self.session_list.addItem(item)

    def _on_session_selected(self, row: int):
        if row < 0:
            return
        item = self.session_list.item(row)
        session_path = Path(item.data(Qt.UserRole))
        self._current_session_path = session_path

        # Load metadata
        meta_path = session_path / "metadata.json"
        self._current_metadata = {}
        if meta_path.exists():
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    self._current_metadata = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")

        # Load researcher report
        report_path = session_path / "researcher_report.json"
        self._current_report = {}
        if report_path.exists():
            try:
                with open(report_path, 'r', encoding='utf-8') as f:
                    self._current_report = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load report: {e}")

        self._update_conversation_tab()
        self._update_emotion_tab()
        self._update_scale_tab()
        self._update_info_tab()

    def _update_conversation_tab(self):
        layout = self._conversation_container.layout()
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        messages = self._current_metadata.get("messages", [])
        if not messages:
            layout.addWidget(QLabel("暂无对话记录"))
            return

        for msg in messages:
            msg_type = msg.get("type", "unknown")
            text = msg.get("text", "")
            timestamp = msg.get("timestamp", "")
            audio_file = msg.get("audio_file", "")

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)

            # Type label
            type_label = QLabel("来访者" if msg_type == "user" else "咨询师")
            type_label.setFixedWidth(50)
            type_label.setAlignment(Qt.AlignTop)
            type_label.setStyleSheet(f"color: {'#4CAF50' if msg_type == 'user' else '#2196F3'}; font-weight: bold;")

            # Text
            text_label = QLabel(text)
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

            # Play button
            play_btn = QPushButton("播放")
            play_btn.setFixedSize(50, 28)
            play_btn.setEnabled(SOUND_AVAILABLE and bool(audio_file))
            if audio_file:
                wav_path = str(self._current_session_path / audio_file)
                play_btn.clicked.connect(lambda checked, p=wav_path: self._play_audio(p))

            # Timestamp
            time_label = QLabel(timestamp.split(" ")[-1][:8] if " " in timestamp else timestamp)
            time_label.setFixedWidth(70)
            time_label.setStyleSheet("color: gray; font-size: 11px;")

            row_layout.addWidget(type_label)
            row_layout.addWidget(text_label, 1)
            row_layout.addWidget(play_btn)
            row_layout.addWidget(time_label)

            if msg_type == "user":
                row.setStyleSheet("background: rgba(76, 175, 80, 0.08); border-radius: 8px;")
            else:
                row.setStyleSheet("background: rgba(33, 150, 243, 0.08); border-radius: 8px;")

            layout.addWidget(row)

        layout.addStretch()

    def _update_emotion_tab(self):
        if not MATPLOTLIB_AVAILABLE:
            return
        self._emotion_figure.clear()
        ax = self._emotion_figure.add_subplot(111)

        emo_data = self._current_report.get("emotion_tracker_data", {})
        emotions = emo_data.get("emotions", [])

        if not emotions:
            # Try fallback from session_emotions in metadata
            ax.text(0.5, 0.5, "暂无情绪数据", ha='center', va='center', fontsize=14)
            self._emotion_canvas.draw()
            return

        turns = [e["turn"] for e in emotions]
        intensities = [e["intensity"] for e in emotions]
        emotion_names = [e["emotion"] for e in emotions]
        colors = [EMOTION_COLORS.get(name, "#9E9E9E") for name in emotion_names]

        ax.plot(turns, intensities, color="#1976D2", linewidth=1.5, alpha=0.7, zorder=1)
        ax.scatter(turns, intensities, c=colors, s=80, zorder=2, edgecolors='white', linewidths=1)

        for i, (t, inten, name) in enumerate(zip(turns, intensities, emotion_names)):
            ax.annotate(name, (t, inten), textcoords="offset points",
                        xytext=(0, 10), ha='center', fontsize=8, color=colors[i])

        ax.set_xlabel("对话轮次")
        ax.set_ylabel("情绪强度")
        ax.set_title("情绪变化曲线")
        ax.set_ylim(0, 1.05)
        ax.grid(True, alpha=0.3)
        self._emotion_figure.tight_layout()
        self._emotion_canvas.draw()

    def _update_scale_tab(self):
        scales = self._current_report.get("scale_assessments", [])
        self._scale_table.setRowCount(len(scales))
        for i, scale in enumerate(scales):
            self._scale_table.setItem(i, 0, QTableWidgetItem(scale.get("scale_name", "")))
            self._scale_table.setItem(i, 1, QTableWidgetItem(str(scale.get("total", ""))))
            self._scale_table.setItem(i, 2, QTableWidgetItem(scale.get("severity", "")))
            self._scale_table.setItem(i, 3, QTableWidgetItem(str(scale.get("items", ""))))

        if not scales:
            self._scale_table.setRowCount(1)
            self._scale_table.setItem(0, 0, QTableWidgetItem("本次会话未进行量表评估"))
            for c in range(1, 4):
                self._scale_table.setItem(0, c, QTableWidgetItem(""))

    def _update_info_tab(self):
        meta = self._current_metadata
        report = self._current_report

        self._info_labels["被试编号"].setText(meta.get("subject_id", "--"))
        self._info_labels["日期"].setText(meta.get("date", "--"))
        self._info_labels["开始时间"].setText(meta.get("start_time", "--"))

        end_time = report.get("session_info", {}).get("end_time", meta.get("end_time", "--"))
        self._info_labels["结束时间"].setText(str(end_time))

        duration = report.get("session_info", {}).get("duration_minutes",
                   report.get("session_duration_minutes", "--"))
        self._info_labels["时长(分钟)"].setText(str(duration))

        rounds = report.get("session_info", {}).get("total_rounds",
                 report.get("conversation_rounds", "--"))
        self._info_labels["对话轮数"].setText(str(rounds))

        end_type = report.get("session_info", {}).get("end_type",
                   report.get("end_type", "--"))
        self._info_labels["结束类型"].setText(str(end_type))

        relax = report.get("relaxation_type", report.get("relaxation_completed", "未进行"))
        self._info_labels["放松训练"].setText(str(relax))

        risk = report.get("risk_assessment", {})
        if isinstance(risk, dict):
            risk_text = f"{risk.get('level', '--')} / {risk.get('notes', '')}"
        else:
            risk_text = str(risk) if risk else "--"
        self._info_labels["风险评估"].setText(risk_text)

    def _play_audio(self, wav_path: str):
        if not SOUND_AVAILABLE:
            return
        if not self._audio_lock.acquire(blocking=False):
            return  # Already playing
        if not os.path.exists(wav_path):
            self._audio_lock.release()
            return

        def _play():
            try:
                data, sr = sf.read(wav_path)
                sd.play(data, sr)
                sd.wait()
            except Exception as e:
                logger.warning(f"Audio playback failed: {e}")
            finally:
                self._audio_lock.release()

        threading.Thread(target=_play, daemon=True).start()
