"""The report must record what the system recommended, not what was played.

A real session recommended meditation and ended without the participant ever
opening the exercise.  ``relaxation_recommendation`` came out as "无": the field
was only written when something had actually been completed, so the one fact a
researcher needs -- "was an exercise offered, and which one?" -- disappeared
exactly in the case where it matters most.
"""

import json

from core.types import EndType
from services.report_service import ReportService, relaxation_code


class RecordingAgent:
    def __init__(self, reply):
        self.reply = reply

    def is_available(self):
        return True

    def generate_report(self, prompt, timeout=None):
        return self.reply


def _service(reply=None):
    service = ReportService(
        llm_service=None,
        agent_service=RecordingAgent(reply or json.dumps({
            "session_info": {"subject_id": "P03"},
            "summary": "以情绪低落为主。",
        }, ensure_ascii=False)),
    )
    service.round_count = 8
    service.session_start_time = None
    return service


def _report(service, **kwargs):
    return service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "最近睡不好"}],
        subject_id="P03",
        end_type=EndType.GOAL_ACHIEVED,
        **kwargs,
    )


def test_recommendation_is_recorded_when_nothing_was_played():
    report = _report(_service(), relaxation_recommendation="meditation")
    assert report["relaxation_recommendation"] == "MEDITATION"
    assert report["relaxation_recommended_type"] == "MEDITATION"


def test_recommendation_accepts_the_display_name_the_writer_spoke():
    report = _report(_service(), relaxation_recommendation="冥想放松")
    assert report["relaxation_recommendation"] == "MEDITATION"


def test_no_recommendation_leaves_the_field_untouched():
    report = _report(_service())
    assert relaxation_code(report.get("relaxation_recommendation")) is None


def test_a_played_exercise_still_wins_over_a_missing_recommendation():
    """Completion is its own fact; it must not be erased by this change."""
    report = _report(_service(), relaxation_info="呼吸放松训练")
    assert report["relaxation_recommendation"] == "BREATHING"


def test_recommendation_and_completion_stay_separate():
    """Recommended breathing, did nothing: the report says so, unambiguously."""
    report = _report(
        _service(),
        relaxation_info="未进行",
        relaxation_recommendation="BREATHING",
    )
    assert report["relaxation_recommendation"] == "BREATHING"
    assert report.get("relaxation_completed") is not True


# ---------------------------------------------------------------------------
# Risk is a record, not a sentence the model may forget
# ---------------------------------------------------------------------------


def test_disclosed_risk_reaches_the_report_even_when_the_model_omits_it():
    """S106 said "有时候真的想结束这一切"; risk_assessment came back empty.

    The detector had already stopped every invitation for that session, so the
    fact was known -- the report simply had no path for it.
    """
    report = _report(_service(), risk_disclosed=True)
    assert report["risk_disclosed"] is True
    assert "自伤" in report["risk_assessment"] or "风险" in report["risk_assessment"]


def test_a_model_written_risk_assessment_is_kept():
    reply = json.dumps({
        "session_info": {"subject_id": "P03"},
        "summary": "以情绪低落为主。",
        "risk_assessment": "存在消极死亡意念，建议进一步评估。",
    }, ensure_ascii=False)
    report = _report(_service(reply), risk_disclosed=True)
    assert report["risk_assessment"] == "存在消极死亡意念，建议进一步评估。"


def test_no_disclosure_leaves_the_report_alone():
    report = _report(_service())
    assert "risk_disclosed" not in report or report["risk_disclosed"] is not True
