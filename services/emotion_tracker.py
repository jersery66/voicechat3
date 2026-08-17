# Emotion Tracker - Real-time emotion trend analysis and intervention triggers

import threading
from typing import Dict, List, Optional
from collections import deque, Counter


class EmotionTracker:
    """Tracks emotion trends across conversation turns and triggers interventions.

    Detects worsening patterns (3+ consecutive rounds of escalating negative emotion)
    and provides awareness-level system suffix for the LLM.
    """

    NEGATIVE_EMOTIONS = {"angry", "anxious", "depressed", "fearful", "stressed", "confused", "lonely"}
    WINDOW_SIZE = 5          # Recent turns to analyze for trend
    INTERVENE_CONSECUTIVE = 3  # Consecutive negative rounds to trigger intervention
    INTERVENE_INTENSITY = 0.6  # Intensity threshold for intervention

    def __init__(self):
        self._history: deque = deque(maxlen=self.WINDOW_SIZE)
        self._session_emotions: List[Dict] = []  # Full session history for persistence
        self._negative_streak: int = 0
        self._peak_negative_intensity: float = 0.0
        self._session_negative_streak_peak: int = 0
        self._session_peak_negative_intensity: float = 0.0
        # Re-entrant lock: the pipeline writes and the UI reads from other
        # threads; compound reads (get_trend) may nest inside other methods.
        self._lock = threading.RLock()

    def add_emotion(self, emotion_dict: Dict) -> None:
        """Record one turn's emotion result. Called after each user utterance."""
        with self._lock:
            emotion = emotion_dict.get("emotion", "neutral")
            intensity = emotion_dict.get("intensity", 0.0)

            entry = {"emotion": emotion, "intensity": intensity}
            self._history.append(entry)
            self._session_emotions.append(entry)

            if emotion in self.NEGATIVE_EMOTIONS:
                self._negative_streak += 1
                self._peak_negative_intensity = max(self._peak_negative_intensity, intensity)
                self._session_negative_streak_peak = max(
                    self._session_negative_streak_peak,
                    self._negative_streak,
                )
                self._session_peak_negative_intensity = max(
                    self._session_peak_negative_intensity,
                    intensity,
                )
            else:
                self._negative_streak = 0
                self._peak_negative_intensity = 0.0

    def get_trend(self, last_n: int = None) -> str:
        """Return emotion trend: 'improving', 'stable', 'worsening', or 'volatile'.

        Trend is determined by intensity direction over the window.
        """
        with self._lock:
            if last_n is None:
                last_n = self.WINDOW_SIZE

            recent = list(self._history)[-last_n:]
            if len(recent) < 2:
                return "stable"

            intensities = [r["intensity"] for r in recent if r["emotion"] in self.NEGATIVE_EMOTIONS]
            if len(intensities) < 2:
                return "stable"

            # Mean difference between first and second half
            mid = len(intensities) // 2
            first_half = sum(intensities[:mid]) / max(mid, 1)
            second_half = sum(intensities[mid:]) / max(len(intensities) - mid, 1)
            diff = second_half - first_half

            # Standard deviation to detect high variance without clear direction
            mean = sum(intensities) / len(intensities)
            variance = sum((x - mean) ** 2 for x in intensities) / len(intensities)
            std = variance ** 0.5

            if diff > 0.2:
                return "worsening"
            elif diff < -0.2:
                return "improving"
            elif std > 0.2:
                return "volatile"
            else:
                return "stable"

    def should_intervene(self) -> bool:
        """True if the pattern suggests the LLM should adjust its approach.

        Triggers when: 3+ consecutive negative emotions with intensity >= 0.6.
        """
        with self._lock:
            if self._negative_streak < self.INTERVENE_CONSECUTIVE:
                return False
            if self._peak_negative_intensity < self.INTERVENE_INTENSITY:
                return False
            return True

    def get_emotion_data(self) -> dict:
        """Return structured emotion data for programmatic use."""
        with self._lock:
            if not self._history:
                return {"dominant": "neutral", "intensity": 0.0, "avg_intensity": 0.0, "trend": "stable"}
            current = self._history[-1]
            intensities = [r["intensity"] for r in self._history]
            return {
                "dominant": current["emotion"],
                "intensity": current["intensity"],
                "avg_intensity": sum(intensities) / len(intensities),
                "trend": self.get_trend(),
                "negative_streak": self._negative_streak,
                "peak_intensity": self._peak_negative_intensity,
            }

    def get_session_emotion_data(self) -> dict:
        """Return complete session emotion data for persistence."""
        with self._lock:
            all_emotions = self._session_emotions
            if not all_emotions:
                return {
                    "emotions": [], "dominant_emotion": "neutral",
                    "avg_intensity": 0.0, "trend": "stable",
                    "negative_streak_peak": 0, "peak_intensity": 0.0,
                }
            emotions_list = [
                {"emotion": e["emotion"], "intensity": e["intensity"], "turn": i + 1}
                for i, e in enumerate(all_emotions)
            ]
            dominant_emotion = Counter(e["emotion"] for e in all_emotions).most_common(1)[0][0]
            intensities = [e["intensity"] for e in all_emotions]
            return {
                "emotions": emotions_list,
                "dominant_emotion": dominant_emotion,
                "avg_intensity": round(sum(intensities) / len(intensities), 3),
                "trend": self.get_trend(),
                "negative_streak_peak": self._session_negative_streak_peak,
                "peak_intensity": self._session_peak_negative_intensity,
            }

    def get_emotion_summary(self) -> str:
        """Human-readable summary of current emotional state."""
        with self._lock:
            if not self._history:
                return "暂无情绪数据"

            current = self._history[-1]
            trend = self.get_trend()

            trend_text = {
                "improving": "情绪正在好转",
                "stable": "情绪较为稳定",
                "worsening": "情绪正在恶化",
                "volatile": "情绪波动较大",
            }.get(trend, "情绪状态不明")

            return (
                f"当前情绪: {current['emotion']} (强度: {current['intensity']:.2f}), "
                f"{trend_text}"
            )

    def get_intervention_hint(self) -> Optional[str]:
        """Return style-only guidance for the system prompt, or ``None``.

        Concrete interventions are business actions owned by TurnPolicy.  The
        method name remains for compatibility, but its output must never
        authorize breathing, relaxation, meditation, or another activity.
        """
        with self._lock:
            if not self.should_intervene():
                return None

            current = self._history[-1]
            trend = self.get_trend()

            hints = {
                "anxious": "来访者焦虑持续上升，请放慢节奏，多使用反映性倾听，避免追问。",
                "depressed": "来访者情绪低落加重，请增加肯定和支持性语言，帮助其找到微小的积极变化。",
                "angry": "来访者愤怒在积累，请注意降低对抗感，使用双面反映技术，不要直接挑战其观点。",
                "fearful": "来访者恐惧感增强，请提供安全感，使用正常化技术，避免深入探讨创伤细节。",
                "stressed": "来访者压力水平上升，请放慢节奏、减少信息负担，保持支持性回应。",
            }

            base = hints.get(current["emotion"],
                             f"来访者负面情绪正在加剧({trend})，请调整策略，更加温和、支持性。")

            return f"【情绪预警】{base}"

    def reset(self) -> dict:
        """Reset tracker for a new session. Returns last session data."""
        with self._lock:
            data = self.get_session_emotion_data()
            self._history.clear()
            self._session_emotions.clear()
            self._negative_streak = 0
            self._peak_negative_intensity = 0.0
            self._session_negative_streak_peak = 0
            self._session_peak_negative_intensity = 0.0
            return data
