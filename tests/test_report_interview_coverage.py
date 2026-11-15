"""Interview coverage is a system record, not something the report invents.

The decision layer picks a dimension per turn and the pipeline keeps the count.
If the report let the language model restate coverage from its own summary, a
session that never asked about appetite could be written up as having covered
it -- and a coverage figure that cannot be checked is worse than none, because
it looks like evidence.
"""

import json

from core.types import EndType
from services.report_service import ReportService


class RecordingAgent:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def is_available(self):
        return True

    def generate_report(self, prompt, timeout=None):
        self.prompts.append(prompt)
        return self.reply


def _service(agent):
    service = ReportService(llm_service=None, agent_service=agent)
    service.round_count = 10
    service.session_start_time = None
    return service


COVERAGE = {
    "counts": {"sleep": 3, "mood": 1},
    "covered": [
        {"focus": "sleep", "name": "睡眠", "turns": 3},
        {"focus": "mood", "name": "情绪", "turns": 1},
    ],
    "uncovered": ["兴趣与愉悦", "精力与疲劳", "食欲与体重"],
    "total_turns": 4,
}


def _reply(**extra):
    payload = {
        "session_info": {"subject_id": "P02"},
        "summary": "以睡眠与情绪为主。",
        "risk_assessment": "未发现明确风险。",
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def test_coverage_reaches_the_prompt_with_an_explicit_boundary():
    agent = RecordingAgent(_reply())
    service = _service(agent)

    service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "最近睡不好"}],
        subject_id="P02",
        end_type=EndType.TIME_LIMIT,
        interview_coverage=COVERAGE,
    )

    prompt = agent.prompts[0]
    assert "【访谈维度覆盖（系统记录，不得改写，不得补充）】" in prompt
    assert "睡眠（3轮）" in prompt
    assert "未覆盖：兴趣与愉悦" in prompt
    assert "不得写成已经谈过" in prompt


def test_the_report_carries_the_system_record_even_if_the_model_omits_it():
    agent = RecordingAgent(_reply())
    service = _service(agent)

    report = service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "最近睡不好"}],
        subject_id="P02",
        end_type=EndType.TIME_LIMIT,
        interview_coverage=COVERAGE,
    )

    assert report["interview_coverage"] == COVERAGE


def test_a_model_supplied_coverage_is_overwritten_by_the_record():
    """Anything the model writes under this key is a guess next to a record."""
    agent = RecordingAgent(_reply(interview_coverage={"counts": {"appetite": 9}}))
    service = _service(agent)

    report = service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "最近睡不好"}],
        subject_id="P02",
        end_type=EndType.TIME_LIMIT,
        interview_coverage=COVERAGE,
    )

    assert report["interview_coverage"]["counts"] == {"sleep": 3, "mood": 1}


def test_without_coverage_the_prompt_and_report_stay_unchanged():
    agent = RecordingAgent(_reply())
    service = _service(agent)

    report = service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "最近睡不好"}],
        subject_id="P02",
        end_type=EndType.TIME_LIMIT,
    )

    assert "访谈维度覆盖" not in agent.prompts[0]
    assert "interview_coverage" not in report
