from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGroupBox, QGridLayout, QScrollArea, QWidget,
    QSizePolicy, QComboBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class MediaPanelDialog(QDialog):
    """Dialog for selecting media (music/videos) based on emotional categories."""

    def __init__(self, library_config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("影视音乐")
        self.setModal(True)
        self.setMinimumSize(500, 400)
        self.setStyleSheet("""
            QDialog { background-color: #f5f0e8; }
            QLabel { color: #2c3e50; }
            QPushButton {
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: bold;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #c4a96a;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                color: #5d4037;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)
        self._library_config = library_config or {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("选择放松内容")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #5d4037; padding: 8px;")
        layout.addWidget(title)

        desc = QLabel("根据你的情绪状态，选择适合的音乐或视频来放松身心")
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color: #78909c; font-size: 11px; padding: 4px;")
        layout.addWidget(desc)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        grid = QVBoxLayout(container)
        grid.setSpacing(8)

        scenes = self._library_config.get("scenes", {})
        emotion_groups = {
            "情绪舒缓": [
                ("anxiety_relief", "焦虑缓解", "#5C6BC0"),
                ("depression_support", "情绪提振", "#4CAF50"),
                ("anger_calm", "愤怒平复", "#FF7043"),
                ("sleep_aid", "助眠放松", "#7E57C2"),
            ],
            "正念冥想": [
                ("meditation", "冥想正念", "#26A69A"),
                ("nature_sounds", "自然白噪音", "#00BCD4"),
                ("breathing_exercise", "呼吸训练", "#66BB6A"),
                ("muscle_relaxation", "肌肉放松", "#8D6E63"),
            ],
            "日常放松": [
                ("entertainment", "日常娱乐", "#FF8A65"),
                ("motivation", "振奋激励", "#FFC107"),
            ],
        }

        for group_name, items in emotion_groups.items():
            group_box = QGroupBox(group_name)
            group_box.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
            group_layout = QGridLayout(group_box)
            group_layout.setSpacing(6)

            for col, (scene_key, scene_name, color) in enumerate(items):
                scene_data = scenes.get(scene_key, {})
                music_count = len(scene_data.get("music", []))
                video_count = len(scene_data.get("videos", []))

                btn = QPushButton(scene_name)
                btn.setObjectName(f"scene_{scene_key}")
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {color}; color: white;
                        border: none; border-radius: 8px;
                        padding: 10px; font-size: 13px; font-weight: bold;
                    }}
                    QPushButton:hover {{ background-color: {color}; opacity: 0.9; }}
                """)
                btn.setMinimumHeight(44)

                count_text = ""
                if music_count > 0 or video_count > 0:
                    count_text = f"\n({music_count}曲/{video_count}视频)"
                elif scene_key in ("nature_sounds", "sleep_aid", "meditation"):
                    count_text = "\n(在线资源)"

                btn.setText(f"{scene_name}{count_text}")
                btn.clicked.connect(lambda checked, k=scene_key: self.scene_selected.emit(k))
                group_layout.addWidget(btn, 0, col)

            grid.addWidget(group_box)

        # Free resources section
        free_box = QGroupBox("免费下载资源")
        free_box.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        free_layout = QVBoxLayout(free_box)
        free_layout.setSpacing(4)

        free_sites = [
            ("Pixabay - 免版税音乐/视频", "https://pixabay.com/music/", "#3F51B5"),
            ("Free Music Archive - 独立音乐", "https://freemusicarchive.org/", "#5C6BC0"),
            ("Jamendo - CC授权音乐", "https://www.jamendo.com/", "#26A69A"),
        ]
        for name, url, color in free_sites:
            site_btn = QPushButton(name)
            site_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color}; color: white;
                    border: none; border-radius: 6px;
                    padding: 8px; font-size: 11px;
                }}
                QPushButton:hover {{ opacity: 0.9; }}
            """)
            site_btn.clicked.connect(lambda checked, u=url: self.open_url.emit(u))
            free_layout.addWidget(site_btn)

        grid.addWidget(free_box)
        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Close button
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #78909C; color: white;
                border: none; border-radius: 8px;
                padding: 8px 24px; font-size: 12px;
            }
            QPushButton:hover { background-color: #607D8B; }
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    scene_selected = Signal(str)
    open_url = Signal(str)