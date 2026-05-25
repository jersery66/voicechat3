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

from services.logger import get_logger

logger = get_logger(__name__)


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
        
    def set_user_id(self, user_id: Optional[str]):
        """Set the current subject ID (被试编号).

        Defensive against ``None`` / empty / whitespace-only values: any of
        these are mapped to ``"default_subject"`` to keep downstream path
        construction safe.
        """
        cleaned = (user_id or "").strip() or "default_subject"
        self.current_subject_id = cleaned

    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        """Safely read a JSON file. Returns ``default`` on missing/corrupt."""
        if not path.exists():
            return {} if default is None else default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read JSON {path}: {e}")
            return {} if default is None else default

    @staticmethod
    def _write_json(path: Path, data: Any) -> bool:
        """Safely write JSON to ``path``. Returns True on success."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.warning(f"Failed to write JSON {path}: {e}")
            return False
        
    # ==================== User Profile Management ====================

    @staticmethod
    def normalize_user_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize UI/report aliases before profile persistence.

        Older UI code used short keys such as ``marital`` and ``drug_type``;
        report/history code expects ``marital_status`` and ``addiction_type``.
        Keep both aliases so existing reports and stored JSON remain compatible.
        """
        normalized = dict(profile or {})

        subject_id = (
            normalized.get("subject_id")
            or normalized.get("user_id")
            or normalized.get("id")
        )
        if subject_id:
            normalized["subject_id"] = str(subject_id).strip()
            normalized["user_id"] = normalized["subject_id"]

        marital = normalized.get("marital_status") or normalized.get("marital")
        if marital:
            normalized["marital_status"] = marital
            normalized["marital"] = marital

        drug_type = normalized.get("addiction_type") or normalized.get("drug_type")
        if drug_type:
            normalized["addiction_type"] = drug_type
            normalized["drug_type"] = drug_type

        return normalized
    
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

        profile = self.normalize_user_profile(profile)
            
        profiles_dir = self.data_root / "user_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        
        profile_path = profiles_dir / f"{self.current_subject_id}.json"
        
        existing_profile = self._read_json(profile_path, default={})
        if not isinstance(existing_profile, dict):
            existing_profile = {}

        existing_profile.update(profile)
        existing_profile["subject_id"] = self.current_subject_id
        existing_profile["user_id"] = self.current_subject_id
        existing_profile["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._write_json(profile_path, existing_profile)

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
        return self._read_json(profile_path, default={})
    
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
                logger.warning(f"Failed to load profile {profile_file}: {e}")
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

        summaries_data = self._read_json(
            summary_path,
            default={"subject_id": self.current_subject_id, "sessions": []},
        )
        # Guard against legacy / corrupted files lacking the expected keys
        if not isinstance(summaries_data, dict):
            summaries_data = {"subject_id": self.current_subject_id, "sessions": []}
        summaries_data.setdefault("subject_id", self.current_subject_id)
        summaries_data.setdefault("sessions", [])

        session_entry = {
            "date": session_date or self.current_date or datetime.now().strftime("%Y-%m-%d"),
            "summary": summary,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        summaries_data["sessions"].append(session_entry)
        summaries_data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._write_json(summary_path, summaries_data)

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

        data = self._read_json(summary_path, default={})
        if not isinstance(data, dict):
            return []
        sessions = data.get("sessions", []) or []
        try:
            return sessions[-limit:][::-1]
        except Exception as e:
            logger.warning(f"Failed to slice summaries: {e}")
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
                addiction_type = profile.get("addiction_type") or profile.get("drug_type")
                if addiction_type:
                    context_parts.append(f"吸毒类型：{addiction_type}")
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

        try:
            from data.treatment_progress import TreatmentProgress
            progress = TreatmentProgress(str(self.data_root))
            progress_summary = progress.get_progress_summary(sid)
            if progress_summary:
                context_parts.append(progress_summary)
                context_parts.append("")
        except Exception as e:
            logger.debug(f"Treatment progress context unavailable: {e}")

        return "\n".join(context_parts) if context_parts else ""
        
    def start_new_session(self) -> str:
        """Start a new chat session and return folder name.

        If a previous "default_subject" empty session exists, it is renamed to
        the current subject id (when distinct) instead of creating a new
        folder, to avoid accumulating empty default folders.
        """
        now = datetime.now()
        previous_folder_name = self.current_folder_name
        previous_message_count = self.message_counter
        # Snapshot the previous session path BEFORE mutating state, so the
        # rename branch below can refer to the old directory unambiguously.
        old_path: Optional[Path] = None
        if previous_folder_name and self.current_date:
            old_path = self.data_root / self.current_date / previous_folder_name

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

        # If we previously had an empty "default_subject" session AND the
        # caller has now provided a real subject id, rename the old folder
        # in-place rather than creating a new empty one.
        if (
            previous_folder_name
            and "default_subject" in previous_folder_name
            and previous_message_count == 0
            and old_path is not None
            and old_path.exists()
            and subject_id != "default_subject"
        ):
            try:
                self.current_folder_name = folder_name
                new_path = self._get_session_path()

                old_path.rename(new_path)
                logger.info(f"Renamed empty default session to: {new_path}")

                # Update metadata in the renamed folder
                metadata = self._load_metadata()
                metadata["subject_id"] = subject_id
                metadata["folder_name"] = folder_name
                self._save_metadata(metadata)

                return self.current_folder_name
            except Exception as e:
                logger.warning(f"Failed to rename default session: {e}")
                # Fallback to creating new folder
                self.current_folder_name = folder_name

        self.current_folder_name = folder_name

        # Create directory structure
        session_path = self._get_session_path()
        session_path.mkdir(parents=True, exist_ok=True)

        # Create metadata file (millisecond-precision start time)
        metadata = {
            "subject_id": subject_id,
            "folder_name": folder_name,
            "date": self.current_date,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
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
        self._write_json(metadata_path, metadata)

    def _load_metadata(self) -> Dict[str, Any]:
        """Load session metadata."""
        metadata_path = self._get_session_path() / "metadata.json"
        return self._read_json(metadata_path, default={})
    
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
            logger.warning(f"Failed to save user text: {e}")

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
            logger.warning(f"Failed to update metadata: {e}")

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
            logger.warning(f"Failed to save assistant text: {e}")

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
            logger.warning(f"Failed to update metadata: {e}")

        return {
            "audio_path": str(audio_path),
            "text_path": str(text_path)
        }
    
    def _save_wav(self, filepath: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE):
        """Save numpy array as WAV file.

        Accepts any array-like and any numeric dtype; coerces to mono int16 at
        ``sample_rate`` Hz, writing a 16-bit PCM WAV. Errors are caught and
        logged — callers must not rely on the file existing on disk.
        """
        try:
            arr = np.asarray(audio)
            if arr.size == 0:
                logger.debug(f"Skip empty audio for {filepath}")
                return
            if arr.dtype in (np.float32, np.float64):
                # Clip to [-1, 1] to avoid integer overflow on conversion
                arr = np.clip(arr, -1.0, 1.0)
                arr = (arr * 32767).astype(np.int16)
            elif arr.dtype != np.int16:
                arr = arr.astype(np.int16)

            with wave.open(str(filepath), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)  # 16-bit
                wav.setframerate(sample_rate)
                wav.writeframes(arr.tobytes())
        except Exception as e:
            logger.warning(f"Failed to save WAV {filepath}: {e}")
    
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
            logger.warning(f"Failed to save researcher report: {e}")

        # Save visitor feedback as text
        feedback_path = session_path / "visitor_feedback.txt"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with open(feedback_path, 'w', encoding='utf-8') as f:
                f.write(f"[{timestamp}]\n{visitor_feedback}")
        except Exception as e:
            logger.warning(f"Failed to save visitor feedback: {e}")

        # Update metadata with report info
        try:
            metadata = self._load_metadata()
            metadata["report_generated"] = True
            metadata["end_type"] = end_type
            metadata["end_time"] = timestamp
            self._save_metadata(metadata)
        except Exception as e:
            logger.warning(f"Failed to update metadata for report: {e}")
        
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

