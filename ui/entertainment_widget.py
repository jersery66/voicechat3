# Entertainment Widget - Music, Movies, Games

import os
import sys
import json
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QListWidget, QListWidgetItem, QStackedWidget,
    QFileDialog, QMessageBox, QSlider
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MediaLibrary:
    """Manages media library (music, movies, games)."""
    
    def __init__(self, library_path: str = None):
        self.library_path = Path(library_path) if library_path else self._get_default_library_path()
        self._ensure_library_dirs()
        self._load_library()
        
    def _get_default_library_path(self) -> Path:
        app_dir = Path(__file__).parent.parent
        return app_dir / "media_library"
    
    def _ensure_library_dirs(self):
        for subdir in ["music", "movies", "games"]:
            (self.library_path / subdir).mkdir(parents=True, exist_ok=True)
            
        config_file = self.library_path / "library_config.json"
        if not config_file.exists():
            default_config = {"music": [], "movies": [], "games": []}
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
    
    def _load_library(self):
        config_file = self.library_path / "library_config.json"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else:
            self.config = {"music": [], "movies": [], "games": []}
            
    def _save_library(self):
        config_file = self.library_path / "library_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
            
    def add_media(self, media_type: str, name: str, path: str, description: str = ""):
        if media_type not in self.config:
            self.config[media_type] = []
        
        media_entry = {
            "name": name,
            "path": path,
            "description": description,
            "added_at": str(Path(path).stat().st_mtime) if Path(path).exists() else ""
        }
        
        for item in self.config[media_type]:
            if item.get("path") == path:
                return False
                
        self.config[media_type].append(media_entry)
        self._save_library()
        return True
        
    def remove_media(self, media_type: str, path: str):
        if media_type not in self.config:
            return False
            
        self.config[media_type] = [
            item for item in self.config[media_type] 
            if item.get("path") != path
        ]
        self._save_library()
        return True
        
    def get_media_list(self, media_type: str) -> list:
        return self.config.get(media_type, [])


class MusicPlayerWidget(QWidget):
    """Music player widget with playlist."""
    
    def __init__(self, media_library: MediaLibrary, parent=None):
        super().__init__(parent)
        self.media_library = media_library
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.current_index = -1
        
        self._setup_ui()
        self._connect_signals()
        self._load_playlist()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        title = QLabel("🎵 音乐播放器")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        playlist_frame = QFrame()
        playlist_frame.setStyleSheet("background-color: rgba(45, 45, 65, 0.5); border-radius: 8px;")
        playlist_layout = QVBoxLayout(playlist_frame)
        
        self.playlist = QListWidget()
        self.playlist.setAlternatingRowColors(True)
        self.playlist.setMinimumHeight(200)
        playlist_layout.addWidget(self.playlist)
        
        playlist_buttons = QHBoxLayout()
        
        self.add_btn = QPushButton("➕ 添加")
        self.add_btn.clicked.connect(self._add_music)
        playlist_buttons.addWidget(self.add_btn)
        
        self.remove_btn = QPushButton("➖ 移除")
        self.remove_btn.clicked.connect(self._remove_music)
        playlist_buttons.addWidget(self.remove_btn)
        
        playlist_layout.addLayout(playlist_buttons)
        layout.addWidget(playlist_frame)
        
        controls_frame = QFrame()
        controls_frame.setStyleSheet("background-color: rgba(45, 45, 65, 0.3); border-radius: 8px; padding: 10px;")
        controls_layout = QVBoxLayout(controls_frame)
        
        self.now_playing = QLabel("未播放")
        self.now_playing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.now_playing.setStyleSheet("color: #9CA3AF;")
        controls_layout.addWidget(self.now_playing)
        
        progress_layout = QHBoxLayout()
        self.position_label = QLabel("0:00")
        self.duration_label = QLabel("0:00")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 0)
        
        progress_layout.addWidget(self.position_label)
        progress_layout.addWidget(self.progress_slider)
        progress_layout.addWidget(self.duration_label)
        controls_layout.addLayout(progress_layout)
        
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(20)
        
        self.prev_btn = QPushButton("⏮️")
        self.prev_btn.setFixedSize(50, 50)
        self.prev_btn.clicked.connect(self._prev_track)
        buttons_layout.addWidget(self.prev_btn)
        
        self.play_btn = QPushButton("▶️")
        self.play_btn.setFixedSize(60, 60)
        self.play_btn.clicked.connect(self._toggle_play)
        buttons_layout.addWidget(self.play_btn)
        
        self.next_btn = QPushButton("⏭️")
        self.next_btn.setFixedSize(50, 50)
        self.next_btn.clicked.connect(self._next_track)
        buttons_layout.addWidget(self.next_btn)
        
        controls_layout.addLayout(buttons_layout)
        
        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel("🔊"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(self._set_volume)
        volume_layout.addWidget(self.volume_slider)
        controls_layout.addLayout(volume_layout)
        
        layout.addWidget(controls_frame)
        
    def _connect_signals(self):
        self.playlist.itemDoubleClicked.connect(self._play_selected)
        self.player.positionChanged.connect(self._update_position)
        self.player.durationChanged.connect(self._update_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.progress_slider.sliderMoved.connect(self._seek)
        
    def _load_playlist(self):
        self.playlist.clear()
        music_list = self.media_library.get_media_list("music")
        for item in music_list:
            list_item = QListWidgetItem(f"🎵 {item['name']}")
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self.playlist.addItem(list_item)
            
    def _add_music(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音乐文件", "",
            "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac"
        )
        for file_path in files:
            name = Path(file_path).stem
            if self.media_library.add_media("music", name, file_path):
                self._load_playlist()
                
    def _remove_music(self):
        current = self.playlist.currentItem()
        if current:
            item = current.data(Qt.ItemDataRole.UserRole)
            self.media_library.remove_media("music", item["path"])
            self._load_playlist()
            
    def _play_selected(self, item):
        media_data = item.data(Qt.ItemDataRole.UserRole)
        self._play_track(media_data["path"], media_data["name"])
        
    def _play_track(self, path: str, name: str):
        if not Path(path).exists():
            QMessageBox.warning(self, "错误", f"文件不存在: {path}")
            return
            
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.now_playing.setText(f"正在播放: {name}")
        self.play_btn.setText("⏸️")
        
    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶️")
        else:
            self.player.play()
            self.play_btn.setText("⏸️")
            
    def _prev_track(self):
        if self.playlist.count() == 0:
            return
        self.current_index = (self.current_index - 1) % self.playlist.count()
        item = self.playlist.item(self.current_index)
        if item:
            media_data = item.data(Qt.ItemDataRole.UserRole)
            self._play_track(media_data["path"], media_data["name"])
            
    def _next_track(self):
        if self.playlist.count() == 0:
            return
        self.current_index = (self.current_index + 1) % self.playlist.count()
        item = self.playlist.item(self.current_index)
        if item:
            media_data = item.data(Qt.ItemDataRole.UserRole)
            self._play_track(media_data["path"], media_data["name"])
            
    def _update_position(self, position):
        self.progress_slider.setValue(position)
        seconds = position // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        self.position_label.setText(f"{minutes}:{seconds:02d}")
        
    def _update_duration(self, duration):
        self.progress_slider.setRange(0, duration)
        seconds = duration // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        self.duration_label.setText(f"{minutes}:{seconds:02d}")
        
    def _seek(self, position):
        self.player.setPosition(position)
        
    def _set_volume(self, value):
        self.audio_output.setVolume(value / 100.0)
        
    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._next_track()
            
    def stop(self):
        self.player.stop()


class MovieLibraryWidget(QWidget):
    """Movie library widget."""
    
    def __init__(self, media_library: MediaLibrary, parent=None):
        super().__init__(parent)
        self.media_library = media_library
        self._setup_ui()
        self._load_library()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        title = QLabel("🎬 电影库")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        library_frame = QFrame()
        library_frame.setStyleSheet("background-color: rgba(45, 45, 65, 0.5); border-radius: 8px;")
        library_layout = QVBoxLayout(library_frame)
        
        self.movie_list = QListWidget()
        self.movie_list.setAlternatingRowColors(True)
        self.movie_list.setMinimumHeight(300)
        self.movie_list.itemDoubleClicked.connect(self._play_movie)
        library_layout.addWidget(self.movie_list)
        
        buttons = QHBoxLayout()
        
        self.add_btn = QPushButton("➕ 添加电影")
        self.add_btn.clicked.connect(self._add_movie)
        buttons.addWidget(self.add_btn)
        
        self.remove_btn = QPushButton("➖ 移除")
        self.remove_btn.clicked.connect(self._remove_movie)
        buttons.addWidget(self.remove_btn)
        
        self.play_btn = QPushButton("▶️ 播放")
        self.play_btn.clicked.connect(self._play_selected)
        buttons.addWidget(self.play_btn)
        
        library_layout.addLayout(buttons)
        layout.addWidget(library_frame)
        
        hint = QLabel("双击电影或选择后点击播放按钮")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #9CA3AF;")
        layout.addWidget(hint)
        
    def _load_library(self):
        self.movie_list.clear()
        movies = self.media_library.get_media_list("movies")
        for movie in movies:
            item = QListWidgetItem(f"🎬 {movie['name']}")
            item.setData(Qt.ItemDataRole.UserRole, movie)
            self.movie_list.addItem(item)
            
    def _add_movie(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择电影文件", "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv)"
        )
        for file_path in files:
            name = Path(file_path).stem
            if self.media_library.add_media("movies", name, file_path):
                self._load_library()
                
    def _remove_movie(self):
        current = self.movie_list.currentItem()
        if current:
            item = current.data(Qt.ItemDataRole.UserRole)
            self.media_library.remove_media("movies", item["path"])
            self._load_library()
            
    def _play_selected(self):
        current = self.movie_list.currentItem()
        if current:
            self._play_movie(current)
            
    def _play_movie(self, item):
        movie_data = item.data(Qt.ItemDataRole.UserRole)
        path = movie_data["path"]
        
        if not Path(path).exists():
            QMessageBox.warning(self, "错误", f"文件不存在: {path}")
            return
            
        if sys.platform == 'win32':
            os.startfile(path)
        else:
            subprocess.Popen(['xdg-open', path])


class GameLibraryWidget(QWidget):
    """Game library widget."""
    
    def __init__(self, media_library: MediaLibrary, parent=None):
        super().__init__(parent)
        self.media_library = media_library
        self._setup_ui()
        self._load_library()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        title = QLabel("🎮 游戏库")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        library_frame = QFrame()
        library_frame.setStyleSheet("background-color: rgba(45, 45, 65, 0.5); border-radius: 8px;")
        library_layout = QVBoxLayout(library_frame)
        
        self.game_list = QListWidget()
        self.game_list.setAlternatingRowColors(True)
        self.game_list.setMinimumHeight(300)
        self.game_list.itemDoubleClicked.connect(self._launch_game)
        library_layout.addWidget(self.game_list)
        
        buttons = QHBoxLayout()
        
        self.add_btn = QPushButton("➕ 添加游戏")
        self.add_btn.clicked.connect(self._add_game)
        buttons.addWidget(self.add_btn)
        
        self.remove_btn = QPushButton("➖ 移除")
        self.remove_btn.clicked.connect(self._remove_game)
        buttons.addWidget(self.remove_btn)
        
        self.launch_btn = QPushButton("🚀 启动")
        self.launch_btn.clicked.connect(self._launch_selected)
        buttons.addWidget(self.launch_btn)
        
        library_layout.addLayout(buttons)
        layout.addWidget(library_frame)
        
        hint = QLabel("双击游戏或选择后点击启动按钮")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #9CA3AF;")
        layout.addWidget(hint)
        
    def _load_library(self):
        self.game_list.clear()
        games = self.media_library.get_media_list("games")
        for game in games:
            item = QListWidgetItem(f"🎮 {game['name']}")
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.game_list.addItem(item)
            
    def _add_game(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏程序", "",
            "可执行文件 (*.exe);;所有文件 (*.*)"
        )
        if file_path:
            name = Path(file_path).stem
            if self.media_library.add_media("games", name, file_path):
                self._load_library()
                
    def _remove_game(self):
        current = self.game_list.currentItem()
        if current:
            item = current.data(Qt.ItemDataRole.UserRole)
            self.media_library.remove_media("games", item["path"])
            self._load_library()
            
    def _launch_selected(self):
        current = self.game_list.currentItem()
        if current:
            self._launch_game(current)
            
    def _launch_game(self, item):
        game_data = item.data(Qt.ItemDataRole.UserRole)
        path = game_data["path"]
        
        if not Path(path).exists():
            QMessageBox.warning(self, "错误", f"文件不存在: {path}")
            return
            
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法启动游戏: {str(e)}")


class EntertainmentWidget(QWidget):
    """Main entertainment widget with tabs for music, movies, games."""
    
    view_changed = pyqtSignal(int)
    
    def __init__(self, media_library: MediaLibrary, parent=None):
        super().__init__(parent)
        self.media_library = media_library
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.stack = QStackedWidget()
        
        self.music_widget = MusicPlayerWidget(self.media_library)
        self.movies_widget = MovieLibraryWidget(self.media_library)
        self.games_widget = GameLibraryWidget(self.media_library)
        
        self.stack.addWidget(self.music_widget)
        self.stack.addWidget(self.movies_widget)
        self.stack.addWidget(self.games_widget)
        
        tabs_frame = QFrame()
        tabs_frame.setStyleSheet("background-color: rgba(30, 30, 45, 0.8);")
        tabs_layout = QHBoxLayout(tabs_frame)
        tabs_layout.setContentsMargins(16, 8, 16, 8)
        tabs_layout.setSpacing(16)
        
        self.music_btn = QPushButton("🎵 音乐")
        self.music_btn.setCheckable(True)
        self.music_btn.setChecked(True)
        self.music_btn.clicked.connect(lambda: self._switch_tab(0))
        tabs_layout.addWidget(self.music_btn)
        
        self.movies_btn = QPushButton("🎬 电影")
        self.movies_btn.setCheckable(True)
        self.movies_btn.clicked.connect(lambda: self._switch_tab(1))
        tabs_layout.addWidget(self.movies_btn)
        
        self.games_btn = QPushButton("🎮 游戏")
        self.games_btn.setCheckable(True)
        self.games_btn.clicked.connect(lambda: self._switch_tab(2))
        tabs_layout.addWidget(self.games_btn)
        
        tabs_layout.addStretch()
        
        layout.addWidget(self.stack, 1)
        layout.addWidget(tabs_frame)
        
    def _switch_tab(self, index: int):
        self.stack.setCurrentIndex(index)
        self.music_btn.setChecked(index == 0)
        self.movies_btn.setChecked(index == 1)
        self.games_btn.setChecked(index == 2)
        
    def stop_music(self):
        self.music_widget.stop()
