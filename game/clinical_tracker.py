import csv
import time
from datetime import datetime


class ClinicalTracker:
    """Records clinical events for CSV export."""

    def __init__(self, output_path: str):
        self.output_path = output_path
        self.session_start = time.time()
        self.events = []
        self._last_flush = time.time()

    def record_event(self, event_type: str, reaction_time_ms: float = None,
                     success: bool = None, detail: str = None):
        self.events.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "elapsed_ms": int((time.time() - self.session_start) * 1000),
            "event_type": event_type,
            "reaction_time_ms": round(reaction_time_ms, 1) if reaction_time_ms else "",
            "success": success if success is not None else "",
            "detail": detail or ""
        })

        now = time.time()
        if now - self._last_flush >= 30:
            self._flush_to_disk()
            self._last_flush = now

    def _flush_to_disk(self):
        if not self.events:
            return
        try:
            headers = ["timestamp", "elapsed_ms", "event_type",
                       "reaction_time_ms", "success", "detail"]
            with open(self.output_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(self.events)
        except Exception as e:
            print(f"[WARNING] Clinical tracker flush failed: {e}")

    def save_csv(self) -> str:
        headers = ["timestamp", "elapsed_ms", "event_type",
                   "reaction_time_ms", "success", "detail"]
        with open(self.output_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(self.events)
        return self.output_path

    def get_summary_metrics(self) -> dict:
        go_events = [e for e in self.events if e["event_type"] == "go_nogo_response"]
        good_pickups = [e for e in go_events if e["success"] is True]
        bad_pickups = [e for e in go_events if e["success"] is False]

        breathing_events = [e for e in self.events if e["event_type"] == "breathing_cycle"]
        breathing_success = [e for e in breathing_events if e["success"] is True]

        camp_events = [e for e in self.events if e["event_type"] == "camp_build"]
        skip_events = [e for e in self.events if e["event_type"] == "camp_build_skipped"]

        correct_rts = [e["reaction_time_ms"] for e in good_pickups
                       if e["reaction_time_ms"] != ""]
        avg_rt = sum(correct_rts) / len(correct_rts) if correct_rts else 0

        # Breathing scores
        breathing_scores = []
        for e in breathing_events:
            detail = e.get("detail") or ""
            if "score=" in detail:
                try:
                    score = int(detail.split("score=")[1])
                    breathing_scores.append(score)
                except:
                    pass
        avg_breathing_score = sum(breathing_scores) / len(breathing_scores) if breathing_scores else 0

        return {
            "game_duration_seconds": int((time.time() - self.session_start)),
            "go_nogo_total_trials": len(go_events),
            "go_nogo_correct_hits": len(good_pickups),
            "go_nogo_false_alarms": len(bad_pickups),
            "go_nogo_accuracy": round(len(good_pickups) / max(len(go_events), 1) * 100, 1),
            "go_nogo_avg_reaction_ms": round(avg_rt, 1),
            "breathing_cycles_attempted": len(breathing_events),
            "breathing_cycles_completed": len(breathing_success),
            "breathing_completion_rate": round(
                len(breathing_success) / max(len(breathing_events), 1) * 100, 1),
            "breathing_avg_score": round(avg_breathing_score, 1),
            "camp_structures_built": len([e for e in camp_events if e.get("success") is True]),
            "camp_builds_skipped": len(skip_events),
            "resources_collected_total": len(good_pickups),
        }
