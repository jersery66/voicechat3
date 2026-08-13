"""Tests for services.report_service — report parsing and session tracking."""

import json
import pytest
from datetime import datetime
from services.report_service import ReportService, EndType


@pytest.fixture
def report_service():
    """Create a ReportService instance."""
    return ReportService()


class TestParseReportJson:
    """_parse_report_json should extract JSON from LLM output."""

    def test_parses_valid_json(self, report_service):
        text = '{"session_info": {"mood": "calm"}, "summary": "测试摘要"}'
        result = report_service._parse_report_json(text, "被试001", EndType.GOAL_ACHIEVED)
        assert result["session_info"]["subject_id"] == "被试001"
        assert result["session_info"]["end_type"] == "GOAL_ACHIEVED"
        assert result["summary"] == "测试摘要"

    def test_parses_json_in_markdown_block(self, report_service):
        text = '```json\n{"summary": "内容"}\n```'
        result = report_service._parse_report_json(text, "被试002", EndType.TIME_LIMIT)
        assert result["session_info"]["subject_id"] == "被试002"

    def test_wraps_invalid_json_in_structure(self, report_service):
        text = "这不是JSON，是纯文本分析"
        result = report_service._parse_report_json(text, "被试003", EndType.QUIT)
        assert result["session_info"]["subject_id"] == "被试003"
        assert result["raw_analysis"] == text

    def test_empty_text(self, report_service):
        result = report_service._parse_report_json("", "被试004", EndType.QUIT)
        assert result["session_info"]["subject_id"] == "被试004"


class TestShouldWarnTimeLimit:
    """should_warn_time_limit should warn once when time threshold is reached."""

    def test_no_warning_initially(self, report_service):
        report_service.start_session()
        should_warn, msg = report_service.should_warn_time_limit()
        assert should_warn is False
        assert msg == ""

    def test_no_double_warning(self, report_service):
        report_service.start_session()
        report_service.time_warning_shown = True
        should_warn, msg = report_service.should_warn_time_limit()
        assert should_warn is False


class TestSessionTracking:
    """Session lifecycle tracking."""

    def test_start_session_resets_state(self, report_service):
        report_service.round_count = 99
        report_service.time_warning_shown = True
        report_service.completed_relaxation = "呼吸"
        report_service.start_session()
        assert report_service.round_count == 0
        assert report_service.time_warning_shown is False
        assert report_service.completed_relaxation is None

    def test_increment_round(self, report_service):
        report_service.start_session()
        report_service.increment_round()
        report_service.increment_round()
        assert report_service.get_round_count() == 2

    def test_record_relaxation(self, report_service):
        report_service.start_session()
        report_service.record_relaxation("渐进式肌肉放松")
        assert report_service.completed_relaxation == "渐进式肌肉放松"

    def test_session_duration(self, report_service):
        report_service.start_session()
        duration = report_service.get_session_duration_minutes()
        assert duration >= 0.0
        assert duration < 1.0  # Should be near zero


class TestFormatEmotionSummary:
    """_format_emotion_summary should format emotion data for prompts."""

    def test_empty_emotions(self, report_service):
        assert report_service._format_emotion_summary([]) == ""

    def test_single_emotion(self, report_service):
        result = report_service._format_emotion_summary([{"emotion": "anxious", "intensity": 0.8}])
        assert "焦虑" in result

    def test_multiple_emotions(self, report_service):
        emotions = [
            {"emotion": "anxious", "intensity": 0.8},
            {"emotion": "anxious", "intensity": 0.6},
            {"emotion": "neutral", "intensity": 0.3},
        ]
        result = report_service._format_emotion_summary(emotions)
        assert "焦虑" in result
        assert "平静" in result
