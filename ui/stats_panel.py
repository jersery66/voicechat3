# Stats Panel - Researcher-facing treatment effectiveness statistics dashboard

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
    QPushButton, QFileDialog, QMessageBox, QTextEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from services.stats_service import StatsService
from services.logger import get_logger

logger = get_logger(__name__)

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class StatsPanelDialog(QDialog):
    """Researcher-facing statistics dashboard."""

    def __init__(self, data_root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("会话与评估趋势")
        self.setMinimumSize(1100, 750)
        self.data_root = data_root
        self.stats_service = StatsService(data_root)
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Header
        header = QLabel("会话与评估趋势")
        header.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._build_overview_tab(), "被试概览")
        self.tab_widget.addTab(self._build_charts_tab(), "统计图表")
        self.tab_widget.addTab(self._build_runtime_tab(), "运行监控")
        self.tab_widget.addTab(self._build_export_tab(), "导出报告")
        layout.addWidget(self.tab_widget, 1)

    def _build_overview_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        # Group stats summary
        self._group_stats_label = QLabel()
        self._group_stats_label.setWordWrap(True)
        self._group_stats_label.setStyleSheet(
            "background: rgba(33,150,243,0.08); padding: 12px; border-radius: 8px; font-size: 13px;"
        )
        layout.addWidget(self._group_stats_label)

        # Subject table
        self._subject_table = QTableWidget()
        self._subject_table.setColumnCount(6)
        self._subject_table.setHorizontalHeaderLabels([
            "被试编号", "会话数", "最近模型观察", "最新量表", "总时长(分)", "最近会话"
        ])
        self._subject_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._subject_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._subject_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._subject_table.setAlternatingRowColors(True)
        layout.addWidget(self._subject_table)

        refresh_btn = QPushButton("刷新数据")
        refresh_btn.clicked.connect(self._load_data)
        layout.addWidget(refresh_btn)

        return container

    def _build_charts_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        if MATPLOTLIB_AVAILABLE:
            self._chart_figure = Figure(figsize=(10, 8))
            self._chart_canvas = FigureCanvasQTAgg(self._chart_figure)
            layout.addWidget(self._chart_canvas)
        else:
            layout.addWidget(QLabel("matplotlib 未安装，无法显示图表"))
        return container

    def _build_runtime_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        refresh_btn = QPushButton("刷新运行监控")
        refresh_btn.clicked.connect(self._load_runtime_data)
        layout.addWidget(refresh_btn)

        self._runtime_metrics = QTextEdit()
        self._runtime_metrics.setReadOnly(True)
        self._runtime_metrics.setMinimumHeight(220)
        layout.addWidget(self._runtime_metrics)

        self._runtime_errors = QTextEdit()
        self._runtime_errors.setReadOnly(True)
        self._runtime_errors.setMinimumHeight(220)
        layout.addWidget(self._runtime_errors)

        return container

    def _build_export_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignCenter)

        desc = QLabel("导出包含所有被试统计数据的 PDF 报告\n包含总体统计和被试明细表")
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("font-size: 14px; color: #555;")
        layout.addWidget(desc)

        export_btn = QPushButton("导出 PDF 报告")
        export_btn.setFixedSize(200, 45)
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                border: none; border-radius: 8px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #45a049; }
        """)
        export_btn.clicked.connect(self._export_pdf)
        layout.addWidget(export_btn)

        self._export_status = QLabel("")
        self._export_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._export_status)

        return container

    def _load_data(self):
        # Group stats — cache for reuse in charts
        self._cached_group = self.stats_service.get_group_stats()
        self._cached_subjects = self.stats_service.get_all_subject_stats()
        self._cached_scales = self.stats_service.get_scale_score_progressions()
        self._cached_emo_trends = self.stats_service.get_emotion_trend_aggregation()

        group = self._cached_group
        self._group_stats_label.setText(
            f"被试总数: {group['total_subjects']}  |  "
            f"总会话数: {group['total_sessions']}  |  "
            f"平均时长: {group['avg_duration']}分钟  |  "
            f"危机事件: {group['crisis_count']}次  |  "
            f"量表变化: 描述性统计"
        )

        # Subject table
        subjects = self._cached_subjects
        self._subject_table.setRowCount(len(subjects))
        for i, s in enumerate(subjects):
            self._subject_table.setItem(i, 0, QTableWidgetItem(s["subject_id"]))
            self._subject_table.setItem(i, 1, QTableWidgetItem(str(s["session_count"])))
            observation = f"{s['latest_emotion']} / 强度 {s['latest_intensity']:.2f}"
            self._subject_table.setItem(i, 2, QTableWidgetItem(observation))

            # Latest scale scores summary
            scales = s.get("latest_scale_scores", {})
            if scales:
                scale_strs = [f"{k}:{v.get('total','?')}" for k, v in scales.items()]
                self._subject_table.setItem(i, 3, QTableWidgetItem(", ".join(scale_strs)))
            else:
                self._subject_table.setItem(i, 3, QTableWidgetItem("--"))

            self._subject_table.setItem(i, 4, QTableWidgetItem(f"{s['total_duration']:.0f}"))
            self._subject_table.setItem(i, 5, QTableWidgetItem(s["last_session_date"]))

        # Charts
        if MATPLOTLIB_AVAILABLE:
            self._update_charts()
        self._load_runtime_data()

    def _load_runtime_data(self):
        if not hasattr(self, "_runtime_metrics"):
            return
        from services.metrics import get_metrics
        from services.error_monitor import get_error_monitor

        snapshot = get_metrics().snapshot()
        if snapshot:
            lines = ["性能指标（毫秒）:"]
            for name, stat in sorted(snapshot.items()):
                lines.append(
                    f"{name}: count={int(stat['count'])}, "
                    f"avg={stat['avg']:.1f}, p50={stat['p50']:.1f}, "
                    f"p95={stat['p95']:.1f}, max={stat['max']:.1f}"
                )
            self._runtime_metrics.setPlainText("\n".join(lines))
        else:
            self._runtime_metrics.setPlainText("暂无性能指标。")

        errors = get_error_monitor().get_recent(30)
        if errors:
            lines = ["最近 WARNING/ERROR:"]
            for item in errors:
                lines.append(
                    f"[{item.get('ts')}] {item.get('level')} "
                    f"{item.get('logger')}: {item.get('msg')}"
                )
            self._runtime_errors.setPlainText("\n".join(lines))
        else:
            self._runtime_errors.setPlainText("暂无 WARNING/ERROR 记录。")

    def _update_charts(self):
        self._chart_figure.clear()
        group = self._cached_group

        # Chart 1: End type distribution (pie)
        ax1 = self._chart_figure.add_subplot(221)
        end_dist = group.get("end_type_distribution", {})
        if end_dist:
            labels = list(end_dist.keys())
            sizes = list(end_dist.values())
            colors = ['#4CAF50', '#FF9800', '#F44336', '#2196F3', '#9C27B0']
            ax1.pie(sizes, labels=labels, autopct='%1.0f%%', colors=colors[:len(labels)])
            ax1.set_title("会话结束类型分布")

        # Chart 2: Average scale scores (bar)
        ax2 = self._chart_figure.add_subplot(222)
        scale_progressions = self._cached_scales
        if scale_progressions:
            scale_names = []
            avg_scores = []
            max_scores = {"PHQ-9": 27, "GAD-7": 21, "PCL-5": 32}
            for name, entries in scale_progressions.items():
                if entries:
                    scale_names.append(name)
                    avg = sum(e["total"] for e in entries) / len(entries)
                    avg_scores.append(avg)

            if scale_names:
                bars = ax2.bar(scale_names, avg_scores, color=['#2196F3', '#FF9800', '#9C27B0'])
                for bar, name in zip(bars, scale_names):
                    max_s = max_scores.get(name, 27)
                    ax2.axhline(y=max_s * 0.5, color='orange', linestyle='--', alpha=0.5, linewidth=0.8)
                    ax2.axhline(y=max_s * 0.75, color='red', linestyle='--', alpha=0.5, linewidth=0.8)
                ax2.set_ylabel("平均分")
                ax2.set_title("量表平均得分")

        # Chart 3: Emotion intensity trends (line)
        ax3 = self._chart_figure.add_subplot(212)
        emo_trends = self._cached_emo_trends
        colors_map = [
            '#1976D2', '#388E3C', '#F57C00', '#7B1FA2',
            '#C62828', '#00838F', '#4E342E', '#283593',
        ]
        for idx, (sid, entries) in enumerate(emo_trends.items()):
            if not entries:
                continue
            sessions = list(range(1, len(entries) + 1))
            intensities = [e.get("intensity", 0) for e in entries]
            color = colors_map[idx % len(colors_map)]
            ax3.plot(sessions, intensities, marker='o', label=sid, color=color, markersize=5)

        ax3.set_xlabel("会话序号")
        ax3.set_ylabel("情绪强度")
        ax3.set_title("各被试模型情绪观察（描述性）")
        ax3.legend(fontsize=8, loc='upper right')
        ax3.grid(True, alpha=0.3)

        self._chart_figure.tight_layout()
        self._chart_canvas.draw()

    def _export_pdf(self):
        default_name = f"treatment_stats_{Path(self.data_root).name}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出统计报告", default_name, "PDF Files (*.pdf)"
        )
        if not path:
            return

        try:
            result = self.stats_service.export_group_report_pdf(path)
            if result:
                self._export_status.setText(f"导出成功: {result}")
                self._export_status.setStyleSheet("color: green; font-size: 13px;")
            else:
                self._export_status.setText("导出失败: reportlab 未安装")
                self._export_status.setStyleSheet("color: red; font-size: 13px;")
        except Exception as e:
            self._export_status.setText(f"导出失败: {e}")
            self._export_status.setStyleSheet("color: red; font-size: 13px;")
