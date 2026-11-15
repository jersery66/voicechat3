"""A report whose envelope is prose gets one JSON-only repair round.

Before this, a valid analysis returned without braces degraded to a single
``raw_analysis`` blob, and every downstream field (summary, risk, suggestions)
quietly disappeared from the PDF.
"""

import json

from services.report_service import ReportService
from core.types import EndType


class RepairingAgent:
    """Returns prose first, JSON when the repair instruction is present."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def is_available(self):
        return True

    def generate_report(self, prompt, timeout=None):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _service(agent):
    service = ReportService(llm_service=None, agent_service=agent)
    service.round_count = 8
    service.session_start_time = None
    return service


def test_prose_first_answer_is_repaired_into_structure():
    agent = RepairingAgent([
        "这位来访者近期入睡困难，情绪偏低，但能主动求助。",
        json.dumps({
            "session_info": {"subject_id": "P01"},
            "summary": "入睡困难，情绪偏低，能主动求助。",
            "risk_assessment": "未发现明确风险。",
        }, ensure_ascii=False),
    ])
    service = _service(agent)

    report = service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "最近总睡不着"}],
        subject_id="P01",
        end_type=EndType.TIME_LIMIT,
    )

    assert "raw_analysis" not in report
    assert report["summary"].startswith("入睡困难")
    assert report["session_info"]["subject_id"] == "P01"
    assert len(agent.prompts) == 2
    assert "输出格式纠正" in agent.prompts[1]


def test_unrepaired_answer_keeps_raw_analysis_and_stops_after_one_retry():
    agent = RepairingAgent([
        "第一遍只有散文。",
        "第二遍还是散文，没有花括号。",
    ])
    service = _service(agent)

    report = service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "嗯"}],
        subject_id="P02",
        end_type=EndType.QUIT,
    )

    # The first pass is the fuller analysis, so a failed repair must not
    # replace it with a shorter second attempt.
    assert report["raw_analysis"] == "第一遍只有散文。"
    assert len(agent.prompts) == 2


def test_fenced_json_is_accepted_without_a_retry():
    agent = RepairingAgent([
        "```json\n" + json.dumps(
            {"session_info": {"subject_id": "P03"}, "summary": "稳定。"},
            ensure_ascii=False,
        ) + "\n```",
    ])
    service = _service(agent)

    report = service.generate_researcher_report(
        conversation_history=[{"role": "user", "content": "还行"}],
        subject_id="P03",
        end_type=EndType.TIME_LIMIT,
    )

    assert report["summary"] == "稳定。"
    assert len(agent.prompts) == 1
