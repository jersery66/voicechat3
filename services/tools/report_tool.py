# Report Generation Tool - Wraps report generation pipeline

import re


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
                emit=None) -> dict:
        """
        Generate all reports for session end.

        Args:
            conversation_history: List of conversation messages
            end_type: EndType enum value
            user_id: Subject ID
            user_info: User profile dict
            relaxation_info: Relaxation completion info string
            session_emotions: List of emotion dicts
            emit: Callback for UI updates (stream_text, etc.)

        Returns:
            dict with keys: feedback, researcher_report, save_result
        """
        session_emotions = session_emotions or []

        # 1. Visitor feedback (streaming)
        full_feedback = ""
        try:
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
            print(f"[WARNING] Visitor feedback generation failed: {e}")

        # 2. Researcher report
        researcher_report = None
        try:
            researcher_report = self.report.generate_researcher_report(
                conversation_history, user_id, end_type,
                user_info=user_info, relaxation_info=relaxation_info,
                session_emotions=session_emotions
            )
        except Exception as e:
            print(f"[WARNING] Researcher report generation failed: {e}")

        # 3. Save
        save_result = None
        if self.data and researcher_report:
            try:
                save_result = self.data.save_session_report(
                    researcher_report, full_feedback, end_type.value
                )
            except Exception as e:
                print(f"[WARNING] Report save failed: {e}")

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
