# Report Generation Tool - Wraps report generation pipeline

import re
from datetime import datetime

from services.logger import get_logger
from services.metrics import get_metrics

logger = get_logger(__name__)


class ReportGenerationTool:
    """Generate visitor feedback + researcher report + PDF for session end."""

    name = "generate_reports"
    description = "Generate all session reports (visitor feedback, researcher report, PDF)"

    def __init__(self, report_service, data_manager, pdf_generator=None):
        self.report = report_service
        self.data = data_manager
        self.pdf_gen = pdf_generator

    def execute(self, conversation_history, end_type, user_id="default_user",
                user_info=None, relaxation_info="", session_emotions=None,
                scale_tags=None, emit=None, emotion_tracker=None) -> dict:
        """
        Generate all reports for session end.

        Args:
            conversation_history: List of conversation messages
            end_type: EndType enum value
            user_id: Subject ID
            user_info: User profile dict
            relaxation_info: Relaxation completion info string
            session_emotions: List of emotion dicts
            scale_tags: Dict of scale assessment results {scale_name: {q_num: score}}
            emit: Callback for UI updates (stream_text, etc.)

        Returns:
            dict with keys: feedback, researcher_report, save_result
        """
        session_emotions = session_emotions or []
        scale_tags = scale_tags or {}
        metrics = get_metrics()

        # 1. Visitor feedback (streaming)
        full_feedback = ""
        try:
            with metrics.timer("report.feedback"):
                stream_gen = self.report.generate_visitor_feedback(
                    conversation_history, end_type, relaxation_info,
                    stream=True, session_emotions=session_emotions
                )
                for chunk in stream_gen:
                    full_feedback += chunk
                    if emit:
                        clean = self._clean_chunk(chunk)
                        if clean:
                            emit("stream_text", clean)
        except Exception as e:
            logger.warning(f"Visitor feedback generation failed: {e}")

        # 2. Researcher report
        researcher_report = None
        scored_scales = None
        try:
            with metrics.timer("report.researcher"):
                researcher_report = self.report.generate_researcher_report(
                    conversation_history, user_id, end_type,
                    user_info=user_info, relaxation_info=relaxation_info,
                    session_emotions=session_emotions
                )
            # Add scale assessment results
            if scale_tags:
                researcher_report = researcher_report or {}
                scored_scales = self._score_scales(scale_tags)
                researcher_report["scale_assessments"] = scored_scales
        except Exception as e:
            logger.warning(f"Researcher report generation failed: {e}")

        # 3. Save
        save_result = None
        if self.data and researcher_report:
            try:
                save_result = self.data.save_session_report(
                    researcher_report, full_feedback, end_type.value
                )
            except Exception as e:
                logger.warning(f"Report save failed: {e}")

        # 4. Save treatment progress
        if emotion_tracker and self.data:
            try:
                from data.treatment_progress import TreatmentProgress
                progress = TreatmentProgress(str(self.data.data_root))

                scale_scores = {}
                if scored_scales:
                    for scale_result in scored_scales:
                        name = scale_result.get("scale_name", "unknown")
                        scale_scores[name] = {k: v for k, v in scale_result.items() if k != "scale_name"}

                key_events = []
                if relaxation_info and relaxation_info != "未进行":
                    key_events.append(f"relaxation_{relaxation_info}")
                for scale_name in (scale_tags or {}):
                    key_events.append(f"scale_{scale_name}_completed")

                progress.add_session(
                    subject_id=user_id,
                    session_date=datetime.now().strftime("%Y-%m-%d"),
                    session_folder=str(self.data.current_folder_name or ""),
                    emotion_data=emotion_tracker.get_session_emotion_data(),
                    scale_scores=scale_scores,
                    key_events=key_events,
                    rounds=self.report.get_round_count(),
                    duration_minutes=self.report.get_session_duration_minutes(),
                    end_type=end_type.value,
                )
            except Exception as e:
                logger.warning(f"Treatment progress save failed: {e}")

        return {
            "feedback": full_feedback,
            "researcher_report": researcher_report,
            "save_result": save_result,
        }

    def _clean_chunk(self, text):
        """Clean TTS tags from a feedback chunk."""
        text = re.sub(r'<\|[^>]+\|>', '', text)
        text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
        text = re.sub(r'\[END_[A-Z_]+\]', '', text)
        text = re.sub(r'\[(?:breath|laughter)\]', '', text)
        return text.strip()

    def _score_scales(self, scale_tags: dict) -> list:
        """Score all completed scale assessments. Returns list of result dicts."""
        from services.scales import get_scale_manager
        mgr = get_scale_manager()
        results = []
        for scale_name, answers in scale_tags.items():
            # Map by REAL question number, not positional index. `answers` is
            # {q_num: score}; sort by q_num so non-contiguous / out-of-order
            # keys do not misalign with the scale's item order.
            try:
                sorted_qnums = sorted(answers.keys(), key=lambda k: int(k))
            except (ValueError, TypeError):
                sorted_qnums = sorted(answers.keys())
            score_list = [answers[q] for q in sorted_qnums]
            score_result = mgr.score_scale(scale_name, score_list)
            score_result["scale_name"] = scale_name
            score_result["raw_answers"] = answers
            results.append(score_result)
        return results
