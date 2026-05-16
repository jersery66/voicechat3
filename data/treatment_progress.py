# Treatment Progress - Cross-session longitudinal tracking

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

from services.logger import get_logger

logger = get_logger(__name__)


class TreatmentProgress:
    """Tracks treatment progress across sessions for a single subject.

    Storage: session_summaries/{subject_id}_progress.json
    """

    def __init__(self, data_root: str):
        self.data_root = Path(data_root)
        self.summaries_dir = self.data_root / "session_summaries"
        self.summaries_dir.mkdir(parents=True, exist_ok=True)

    def _get_progress_path(self, subject_id: str) -> Path:
        return self.summaries_dir / f"{subject_id}_progress.json"

    def _load_progress(self, subject_id: str) -> Dict[str, Any]:
        path = self._get_progress_path(subject_id)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load progress for {subject_id}: {e}")
        return {
            "subject_id": subject_id,
            "sessions": [],
            "emotion_trend": [],
            "scale_trend": {},
            "last_updated": None,
        }

    def _save_progress(self, subject_id: str, data: Dict[str, Any]):
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path = self._get_progress_path(subject_id)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            logger.warning(f"Failed to save progress for {subject_id}: {e}")

    def add_session(self,
                    subject_id: str,
                    session_date: str,
                    session_folder: str,
                    emotion_data: Dict[str, Any],
                    scale_scores: Dict[str, Dict[str, Any]],
                    key_events: List[str],
                    rounds: int,
                    duration_minutes: float,
                    end_type: str) -> None:
        """Record a completed session's data."""
        progress = self._load_progress(subject_id)

        session_entry = {
            "date": session_date,
            "session_folder": session_folder,
            "emotion_summary": {
                "dominant": emotion_data.get("dominant_emotion", "neutral"),
                "avg_intensity": emotion_data.get("avg_intensity", 0.0),
                "trend": emotion_data.get("trend", "stable"),
            },
            "scale_scores": scale_scores,
            "key_events": key_events,
            "rounds": rounds,
            "duration_minutes": round(duration_minutes, 1),
            "end_type": end_type,
        }
        progress["sessions"].append(session_entry)

        progress["emotion_trend"].append({
            "date": session_date,
            "dominant": emotion_data.get("dominant_emotion", "neutral"),
            "intensity": emotion_data.get("avg_intensity", 0.0),
        })

        for scale_name, score_data in scale_scores.items():
            if scale_name not in progress["scale_trend"]:
                progress["scale_trend"][scale_name] = []
            progress["scale_trend"][scale_name].append({
                "date": session_date,
                "total": score_data.get("total", 0),
                "severity": score_data.get("severity", ""),
            })

        self._save_progress(subject_id, progress)
        logger.info(f"Treatment progress updated for {subject_id}: session {session_date}")

    def get_emotion_trend(self, subject_id: str) -> List[Dict]:
        progress = self._load_progress(subject_id)
        return progress.get("emotion_trend", [])

    def get_scale_trend(self, subject_id: str, scale_name: str = None) -> Any:
        progress = self._load_progress(subject_id)
        if scale_name:
            return progress.get("scale_trend", {}).get(scale_name, [])
        return progress.get("scale_trend", {})

    def get_key_events(self, subject_id: str) -> List[Dict]:
        progress = self._load_progress(subject_id)
        events = []
        for session in progress.get("sessions", []):
            for event in session.get("key_events", []):
                events.append({"date": session["date"], "event": event})
        return events

    def get_progress_summary(self, subject_id: str) -> str:
        """Generate a human-readable summary for LLM context injection."""
        progress = self._load_progress(subject_id)
        sessions = progress.get("sessions", [])
        if not sessions:
            return ""

        parts = [f"【治疗进展 - 被试{subject_id}】"]
        parts.append(f"累计会话: {len(sessions)}次")

        latest = sessions[-1]
        emo = latest.get("emotion_summary", {})
        parts.append(
            f"最近情绪趋势: {emo.get('dominant', '未知')} "
            f"(强度{emo.get('avg_intensity', 0):.2f}, "
            f"{emo.get('trend', '稳定')})"
        )

        scales = latest.get("scale_scores", {})
        if scales:
            scale_strs = [
                f"{name}: {data.get('total', '?')}分({data.get('severity', '')})"
                for name, data in scales.items()
            ]
            parts.append("最近量表: " + ", ".join(scale_strs))

        if len(sessions) >= 2:
            first_intensity = sessions[0].get("emotion_summary", {}).get("avg_intensity", 0)
            last_intensity = emo.get("avg_intensity", 0)
            if last_intensity < first_intensity - 0.1:
                parts.append("情绪强度呈下降趋势（好转）")
            elif last_intensity > first_intensity + 0.1:
                parts.append("情绪强度呈上升趋势（需关注）")

        return "\n".join(parts)

    def list_all_subjects(self) -> List[str]:
        """List all subject IDs that have progress data."""
        subjects = []
        for path in self.summaries_dir.glob("*_progress.json"):
            subject_id = path.stem.replace("_progress", "")
            subjects.append(subject_id)
        return sorted(subjects)
