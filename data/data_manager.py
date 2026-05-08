# Data Manager - Hierarchical Storage System

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
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
        ├── user_profiles/                    # 来访者基本信息存储
        │   └── 被试001.json                   # 每个来访者一个文件
        ├── session_summaries/                # 会话摘要存储
        │   └── 被试001_summary.json          # 每个来访者的历史摘要
        ├── 2025-12-22/                       # Date folder
        │   └── 被试001/                      # Subject ID folder (被试编号)
        │       ├── metadata.json
        │       ├── 001_user.wav
        │       ├── 001_user.txt
        │       ├── 001_assistant.wav
        │       └── 001_assistant.txt
        │   └── 被试001_153045/               # Duplicate with timestamp
    """
    
    def __init__(self, data_root: str = DATA_ROOT):
        self.data_root = Path(data_root)
        self.current_subject_id: Optional[str] = None  # 被试编号
        self.current_folder_name: Optional[str] = None  # 实际文件夹名（可能带时间戳）
        self.current_date: Optional[str] = None
        self.message_counter: int = 0
        self._ensure_profile_dirs()
        
    def _ensure_profile_dirs(self):
        """Ensure profile and summary directories exist."""
        profiles_dir = self.data_root / "user_profiles"
        summaries_dir = self.data_root / "session_summaries"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        summaries_dir.mkdir(parents=True, exist_ok=True)
        
    def set_user_id(self, user_id: str):
        """Set the current subject ID (被试编号)."""
        self.current_subject_id = user_id.strip() or "default_subject"
        
    # ==================== User Profile Management ====================
    
    def save_user_profile(self, profile: Dict[str, Any]) -> str:
        """
        Save user profile information.
        
        Args:
            profile: Dict containing user info like:
                - name: 姓名
                - age: 年龄
                - gender: 性别
                - occupation: 职业
                - addiction_type: 吸毒类型
                - addiction_duration: 吸毒年限
                - treatment_count: 戒毒次数
                - notes: 备注
                
        Returns:
            Path to saved profile file
        """
        if not self.current_subject_id:
            return ""
            
        profiles_dir = self.data_root / "user_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        
        profile_path = profiles_dir / f"{self.current_subject_id}.json"
        
        existing_profile = {}
        if profile_path.exists():
            with open(profile_path, 'r', encoding='utf-8') as f:
                existing_profile = json.load(f)
        
        existing_profile.update(profile)
        existing_profile["subject_id"] = self.current_subject_id
        existing_profile["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(existing_profile, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARNING] Failed to save user profile: {e}")

        return str(profile_path)
    
    def load_user_profile(self, subject_id: str = None) -> Dict[str, Any]:
        """
        Load user profile information.
        
        Args:
            subject_id: Subject ID to load, or current subject if None
            
        Returns:
            Profile dict or empty dict if not found
        """
        sid = subject_id or self.current_subject_id
        if not sid:
            return {}
            
        profile_path = self.data_root / "user_profiles" / f"{sid}.json"
        if profile_path.exists():
            with open(profile_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def get_all_user_profiles(self) -> List[Dict[str, Any]]:
        """Get all user profiles."""
        profiles_dir = self.data_root / "user_profiles"
        if not profiles_dir.exists():
            return []
            
        profiles = []
        for profile_file in profiles_dir.glob("*.json"):
            try:
                with open(profile_file, 'r', encoding='utf-8') as f:
                    profiles.append(json.load(f))
            except Exception as e:
                print(f"[WARNING] Failed to load profile {profile_file}: {e}")
        return profiles
    
    # ==================== Session Summary Management ====================
    
    def save_session_summary(self, summary: str, session_date: str = None) -> str:
        """
        Save session summary for a subject.
        
        Args:
            summary: Summary text of the session
            session_date: Date of the session (defaults to current date)
            
        Returns:
            Path to saved summary file
        """
        if not self.current_subject_id:
            return ""
            
        summaries_dir = self.data_root / "session_summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        
        summary_path = summaries_dir / f"{self.current_subject_id}_summary.json"
        
        summaries_data = {"subject_id": self.current_subject_id, "sessions": []}
        if summary_path.exists():
            with open(summary_path, 'r', encoding='utf-8') as f:
                summaries_data = json.load(f)
        
        session_entry = {
            "date": session_date or self.current_date or datetime.now().strftime("%Y-%m-%d"),
            "summary": summary,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        summaries_data["sessions"].append(session_entry)
        summaries_data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summaries_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARNING] Failed to save session summary: {e}")

        return str(summary_path)
    
    def load_session_summaries(self, subject_id: str = None, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Load recent session summaries for a subject.
        
        Args:
            subject_id: Subject ID to load, or current subject if None
            limit: Maximum number of summaries to return
            
        Returns:
            List of session summary dicts (most recent first)
        """
        sid = subject_id or self.current_subject_id
        if not sid:
            return []
            
        summary_path = self.data_root / "session_summaries" / f"{sid}_summary.json"
        if not summary_path.exists():
            return []
            
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            sessions = data.get("sessions", [])
            return sessions[-limit:][::-1]
        except Exception as e:
            print(f"[WARNING] Failed to load summaries: {e}")
            return []
    
    def get_formatted_history_context(self, subject_id: str = None, include_profile: bool = True, include_summaries: int = 3) -> str:
        """
        Get formatted context string with user profile and recent session summaries.
        Used to inject into LLM system prompt.
        
        Args:
            subject_id: Subject ID, or current subject if None
            include_profile: Whether to include user profile info
            include_summaries: Number of recent summaries to include (0 to disable)
            
        Returns:
            Formatted context string
        """
        sid = subject_id or self.current_subject_id
        if not sid:
            return ""
            
        context_parts = []
        
        if include_profile:
            profile = self.load_user_profile(sid)
            if profile:
                context_parts.append("【来访者基本信息】")
                if profile.get("name"):
                    context_parts.append(f"姓名：{profile['name']}")
                if profile.get("age"):
                    context_parts.append(f"年龄：{profile['age']}")
                if profile.get("gender"):
                    context_parts.append(f"性别：{profile['gender']}")
                if profile.get("occupation"):
                    context_parts.append(f"职业：{profile['occupation']}")
                if profile.get("addiction_type"):
                    context_parts.append(f"吸毒类型：{profile['addiction_type']}")
                if profile.get("addiction_duration"):
                    context_parts.append(f"吸毒年限：{profile['addiction_duration']}")
                if profile.get("treatment_count"):
                    context_parts.append(f"戒毒次数：{profile['treatment_count']}")
                if profile.get("notes"):
                    context_parts.append(f"备注：{profile['notes']}")
                context_parts.append("")
        
        if include_summaries > 0:
            summaries = self.load_session_summaries(sid, limit=include_summaries)
            if summaries:
                context_parts.append("【历史会话摘要】")
                for i, s in enumerate(summaries, 1):
                    context_parts.append(f"--- {s.get('date', '未知日期')} ---")
                    context_parts.append(s.get("summary", "无摘要"))
                    context_parts.append("")
        
        return "\n".join(context_parts) if context_parts else ""
        
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
            # Check for existing folder with same name
            if (date_path / folder_name).exists():
                folder_name = f"{base_folder}_{now.strftime('%H%M%S')}"
        
        # Check if we should rename an existing empty "default_subject" session
        # This prevents accumulating empty default folders
        if self.current_folder_name and "default_subject" in self.current_folder_name and self.message_counter == 0:
            old_path = self._get_session_path()
            if old_path.exists() and subject_id != "default_subject":
                try:
                    new_folder_name = folder_name
                    # Ensure new name is unique if we are renaming to it (though we just calculated it)
                    # But wait, folder_name was calculated based on existence.
                    # If we rename 'default' to 'subject_001', we need to check if 'subject_001' exists?
                    # Yes, logic above handled collision for 'subject_001' vs existing 'subject_001'.
                    
                    self.current_folder_name = new_folder_name
                    new_path = self._get_session_path()
                    
                    # Rename directory
                    old_path.rename(new_path)
                    print(f"[INFO] Renamed empty default session to: {new_path}")
                    
                    # Update metadata in the renamed folder
                    metadata = self._load_metadata()
                    metadata["subject_id"] = subject_id
                    metadata["folder_name"] = new_folder_name
                    self._save_metadata(metadata)
                    
                    return self.current_folder_name
                except Exception as e:
                    print(f"[WARNING] Failed to rename default session: {e}")
                    # Fallback to creating new folder
                    self.current_folder_name = folder_name
        
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
    
    @property
    def session_dir(self) -> Optional[str]:
        """Get the current session directory path as string."""
        if self.current_folder_name is None:
            return None
        return str(self._get_session_path())

    def _get_session_path(self) -> Path:
        """Get the current session directory path."""
        return self.data_root / self.current_date / self.current_folder_name
    
    def _save_metadata(self, metadata: Dict[str, Any]):
        """Save session metadata."""
        metadata_path = self._get_session_path() / "metadata.json"
        try:
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARNING] Failed to save metadata: {e}")
            
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

        # Save audio (may be None in text-only mode)
        if audio is not None:
            self._save_wav(audio_path, audio)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        try:
            with open(text_path, 'w', encoding='utf-8') as f:
                f.write(f"[{timestamp}]\n{text}")
        except Exception as e:
            print(f"[WARNING] Failed to save user text: {e}")

        try:
            metadata = self._load_metadata()
            metadata["messages"].append({
                "type": "user",
                "index": self.message_counter,
                "audio_file": str(audio_path.name) if audio is not None else None,
                "text_file": str(text_path.name),
                "text": text,
                "timestamp": timestamp
            })
            self._save_metadata(metadata)
        except Exception as e:
            print(f"[WARNING] Failed to update metadata: {e}")

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
        if audio is not None:
            self._save_wav(audio_path, audio, sample_rate=sample_rate)

        # Get timestamp with millisecond precision
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        try:
            with open(text_path, 'w', encoding='utf-8') as f:
                f.write(f"[{timestamp}]\n{text}")
        except Exception as e:
            print(f"[WARNING] Failed to save assistant text: {e}")

        try:
            metadata = self._load_metadata()
            metadata["messages"].append({
                "type": "assistant",
                "index": self.message_counter,
                "audio_file": str(audio_path.name) if audio is not None else None,
                "text_file": str(text_path.name),
                "text": text,
                "timestamp": timestamp
            })
            self._save_metadata(metadata)
        except Exception as e:
            print(f"[WARNING] Failed to update metadata: {e}")

        return {
            "audio_path": str(audio_path),
            "text_path": str(text_path)
        }
    
    def _save_wav(self, filepath: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE):
        """Save numpy array as WAV file."""
        try:
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
        except Exception as e:
            print(f"[WARNING] Failed to save WAV {filepath}: {e}")
    
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
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(researcher_report, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARNING] Failed to save researcher report: {e}")

        # Save visitor feedback as text
        feedback_path = session_path / "visitor_feedback.txt"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with open(feedback_path, 'w', encoding='utf-8') as f:
                f.write(f"[{timestamp}]\n{visitor_feedback}")
        except Exception as e:
            print(f"[WARNING] Failed to save visitor feedback: {e}")

        # Update metadata with report info
        try:
            metadata = self._load_metadata()
            metadata["report_generated"] = True
            metadata["end_type"] = end_type
            metadata["end_time"] = timestamp
            self._save_metadata(metadata)
        except Exception as e:
            print(f"[WARNING] Failed to update metadata for report: {e}")
        
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

