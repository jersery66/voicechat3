# Data Manager - Hierarchical Storage System

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import wave
import numpy as np

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_ROOT, SAMPLE_RATE


class DataManager:
    """
    Manages hierarchical data storage for voice chat sessions.
    
    Structure (simplified):
        voice_chat_data/
        ├── 2025-12-22/              # Date folder
        │   └── 被试001/              # Subject ID folder (被试编号)
        │       ├── metadata.json
        │       ├── 001_user.wav
        │       ├── 001_user.txt
        │       ├── 001_assistant.wav
        │       └── 001_assistant.txt
        │   └── 被试001_153045/       # Duplicate with timestamp
    """
    
    def __init__(self, data_root: str = DATA_ROOT):
        self.data_root = Path(data_root)
        self.current_subject_id: Optional[str] = None  # 被试编号
        self.current_folder_name: Optional[str] = None  # 实际文件夹名（可能带时间戳）
        self.current_date: Optional[str] = None
        self.message_counter: int = 0
        
    def set_user_id(self, user_id: str):
        """Set the current subject ID (被试编号)."""
        self.current_subject_id = user_id.strip() or "default_subject"
        
    def start_new_session(self) -> str:
        """Start a new chat session and return folder name."""
        now = datetime.now()
        self.current_date = now.strftime("%Y-%m-%d")
        self.message_counter = 0
        
        # 获取被试编号，确定文件夹名
        subject_id = self.current_subject_id or "default_subject"
        
        # 检查是否已存在同名文件夹，如有则添加时间戳
        date_path = self.data_root / self.current_date
        base_folder = subject_id
        folder_name = base_folder
        
        if date_path.exists():
            # 检查同名文件夹
            if (date_path / folder_name).exists():
                # 添加时间戳区分：被试编号_HHMMSS
                folder_name = f"{base_folder}_{now.strftime('%H%M%S')}"
        
        self.current_folder_name = folder_name
        
        # Create directory structure
        session_path = self._get_session_path()
        session_path.mkdir(parents=True, exist_ok=True)
        
        # Create metadata file
        metadata = {
            "subject_id": subject_id,  # 被试编号
            "folder_name": folder_name,
            "date": self.current_date,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],  # Millisecond precision
            "messages": []
        }
        self._save_metadata(metadata)
        
        return self.current_folder_name
    
    def _get_session_path(self) -> Path:
        """Get the current session directory path."""
        return self.data_root / self.current_date / self.current_folder_name
    
    def _save_metadata(self, metadata: Dict[str, Any]):
        """Save session metadata."""
        metadata_path = self._get_session_path() / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
            
    def _load_metadata(self) -> Dict[str, Any]:
        """Load session metadata."""
        metadata_path = self._get_session_path() / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def save_user_message(self, audio: np.ndarray, text: str) -> Dict[str, str]:
        """
        Save user audio and transcription.
        
        Returns:
            Dict with paths to saved files
        """
        if self.current_folder_name is None:
            self.start_new_session()
            
        self.message_counter += 1
        prefix = f"{self.message_counter:03d}_user"
        
        session_path = self._get_session_path()
        audio_path = session_path / f"{prefix}.wav"
        text_path = session_path / f"{prefix}.txt"
        
        # Save audio
        self._save_wav(audio_path, audio)
        
        # Get timestamp with millisecond precision
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Save text with timestamp
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(f"[{timestamp}]\n{text}")
            
        # Update metadata
        metadata = self._load_metadata()
        metadata["messages"].append({
            "type": "user",
            "index": self.message_counter,
            "audio_file": str(audio_path.name),
            "text_file": str(text_path.name),
            "text": text,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Millisecond precision
        })
        self._save_metadata(metadata)
        
        return {
            "audio_path": str(audio_path),
            "text_path": str(text_path)
        }
    
    def save_assistant_message(self, audio: np.ndarray, text: str, sample_rate: int = 24000) -> Dict[str, str]:
        """
        Save assistant audio and response text.
        
        Returns:
            Dict with paths to saved files
        """
        if self.current_folder_name is None:
            self.start_new_session()
            
        prefix = f"{self.message_counter:03d}_assistant"
        
        session_path = self._get_session_path()
        audio_path = session_path / f"{prefix}.wav"
        text_path = session_path / f"{prefix}.txt"
        
        # Save audio (TTS uses 24000 sample rate)
        self._save_wav(audio_path, audio, sample_rate=sample_rate)
        
        # Get timestamp with millisecond precision
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Save text with timestamp
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(f"[{timestamp}]\n{text}")
            
        # Update metadata
        metadata = self._load_metadata()
        metadata["messages"].append({
            "type": "assistant",
            "index": self.message_counter,
            "audio_file": str(audio_path.name),
            "text_file": str(text_path.name),
            "text": text,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Millisecond precision
        })
        self._save_metadata(metadata)
        
        return {
            "audio_path": str(audio_path),
            "text_path": str(text_path)
        }
    
    def _save_wav(self, filepath: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE):
        """Save numpy array as WAV file."""
        # Normalize to int16
        if audio.dtype == np.float32 or audio.dtype == np.float64:
            audio = (audio * 32767).astype(np.int16)
        elif audio.dtype != np.int16:
            audio = audio.astype(np.int16)
            
        with wave.open(str(filepath), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(sample_rate)
            wav.writeframes(audio.tobytes())
    
    def get_session_history(self) -> list:
        """Get all messages in current session."""
        if self.current_folder_name is None:
            return []
        metadata = self._load_metadata()
        return metadata.get("messages", [])
    
    def list_subjects(self, date: Optional[str] = None) -> list:
        """List all subject folders, optionally filtered by date."""
        subjects = []
        
        if date:
            date_path = self.data_root / date
            if date_path.exists():
                for subject_dir in date_path.iterdir():
                    if subject_dir.is_dir():
                        subjects.append({
                            "date": date,
                            "folder_name": subject_dir.name
                        })
        else:
            for date_dir in self.data_root.iterdir():
                if date_dir.is_dir():
                    for subject_dir in date_dir.iterdir():
                        if subject_dir.is_dir():
                            subjects.append({
                                "date": date_dir.name,
                                "folder_name": subject_dir.name
                            })
        return subjects
    
    def clear_current_session(self):
        """Clear current session data."""
        self.current_folder_name = None
        self.current_subject_id = None
        self.message_counter = 0
    
    # ==================== Report Storage ====================
    
    def save_session_report(self, 
                           researcher_report: dict, 
                           visitor_feedback: str,
                           end_type: str) -> Dict[str, str]:
        """
        Save session report (researcher version + visitor feedback).
        
        Args:
            researcher_report: Structured report dict for researchers
            visitor_feedback: Oral-style feedback text for visitors
            end_type: How the session ended (GOAL_ACHIEVED, TIME_LIMIT, etc.)
            
        Returns:
            Dict with paths to saved files
        """
        if self.current_folder_name is None:
            return {"error": "No active session"}
            
        session_path = self._get_session_path()
        
        # Save researcher report as JSON
        report_path = session_path / "researcher_report.json"
        researcher_report["end_type"] = end_type
        researcher_report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(researcher_report, f, ensure_ascii=False, indent=2)
        
        # Save visitor feedback as text
        feedback_path = session_path / "visitor_feedback.txt"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(feedback_path, 'w', encoding='utf-8') as f:
            f.write(f"[{timestamp}]\n{visitor_feedback}")
        
        # Update metadata with report info
        metadata = self._load_metadata()
        metadata["report_generated"] = True
        metadata["end_type"] = end_type
        metadata["end_time"] = timestamp
        self._save_metadata(metadata)
        
        return {
            "report_path": str(report_path),
            "feedback_path": str(feedback_path)
        }
    
    def get_session_duration_minutes(self) -> float:
        """Get session duration in minutes based on metadata."""
        metadata = self._load_metadata()
        start_time_str = metadata.get("start_time")
        if not start_time_str:
            return 0.0
        try:
            # Parse with milliseconds
            start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f")
            delta = datetime.now() - start_time
            return delta.total_seconds() / 60.0
        except ValueError:
            try:
                # Fallback without milliseconds
                start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                delta = datetime.now() - start_time
                return delta.total_seconds() / 60.0
            except ValueError:
                return 0.0
    
    def get_message_count(self) -> int:
        """Get total message count in current session."""
        return self.message_counter


# Singleton instance
_data_manager = None

def get_data_manager() -> DataManager:
    global _data_manager
    if _data_manager is None:
        _data_manager = DataManager()
    return _data_manager

