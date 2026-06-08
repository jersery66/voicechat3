"""Tests for services.pipeline — tag detection, cleaning, and regex helpers."""

import pytest
from services.pipeline import (
    clean_for_display,
    clean_for_tts,
    detect_tag,
    parse_scale_tags,
    END_PATTERNS,
    REC_TAGS,
)


class TestCleanForDisplay:
    """clean_for_display strips ALL control tags including [breath]/[laughter]."""

    def test_strips_rec_tags(self):
        assert clean_for_display("你好[REC_BREATHING]世界") == "你好世界"

    def test_strips_end_tags(self):
        assert clean_for_display("结束[END_GOAL_ACHIEVED]") == "结束"

    def test_strips_scale_tags(self):
        # SCALE tags are stripped; "评分" is also removed (forbidden internal term)
        assert clean_for_display("评分[SCALE:PHQ-9:Q1:S3]完成") == "完成"
        assert clean_for_display("今天不错[SCALE:GAD-7:Q2:S1]") == "今天不错"

    def test_strips_chinese_brackets(self):
        # 【】 (U+3010/U+3011) are stripped by _RE_BRACKETS_CN
        result = clean_for_display("【情绪识别】焦虑")
        assert result == "焦虑"

    def test_strips_breath_laughter(self):
        assert clean_for_display("哈哈[breath]嘿嘿[laughter]") == "哈哈嘿嘿"

    def test_strips_pipe_tags(self):
        assert clean_for_display("你好<|endoftext|>世界") == "你好世界"

    def test_combined_tags(self):
        text = "你好[breath]【分析】内容[REC_BREATHING][END_GOAL_ACHIEVED]"
        assert clean_for_display(text) == "你好内容"

    def test_empty_string(self):
        assert clean_for_display("") == ""

    def test_no_tags(self):
        assert clean_for_display("普通文本") == "普通文本"


class TestCleanForTts:
    """clean_for_tts keeps [breath]/[laughter] but strips control tags."""

    def test_keeps_breath(self):
        assert clean_for_tts("你好[breath]世界") == "你好[breath]世界"

    def test_keeps_laughter(self):
        assert clean_for_tts("哈哈[laughter]嘿嘿") == "哈哈[laughter]嘿嘿"

    def test_strips_rec_tags(self):
        assert clean_for_tts("你好[REC_BREATHING]世界") == "你好世界"

    def test_strips_end_tags(self):
        assert clean_for_tts("结束[END_GOAL_ACHIEVED]") == "结束"

    def test_strips_scale_tags(self):
        assert clean_for_tts("评分[SCALE:PHQ-9:Q1:S3]完成") == "评分完成"

    def test_strips_chinese_brackets(self):
        assert clean_for_tts("【分析】内容") == "内容"


class TestDetectTag:
    """detect_tag finds the first matching tag pattern."""

    def test_detect_end_goal(self):
        assert detect_tag("目标达成[END_GOAL_ACHIEVED]", END_PATTERNS) == "goal_achieved"

    def test_detect_end_safety(self):
        assert detect_tag("[END_SAFETY]危机", END_PATTERNS) == "safety"

    def test_detect_end_time(self):
        assert detect_tag("[END_TIME_LIMIT]", END_PATTERNS) == "time_limit"

    def test_detect_rec_breathing(self):
        assert detect_tag("试试呼吸[REC_BREATHING]", REC_TAGS) == "breathing"

    def test_detect_rec_muscle(self):
        assert detect_tag("[REC_MUSCLE]肌肉", REC_TAGS) == "muscle"

    def test_detect_rec_meditation(self):
        assert detect_tag("[REC_MEDITATION]", REC_TAGS) == "meditation"

    def test_detect_rec_game(self):
        assert detect_tag("[REC_GAME]", REC_TAGS) == "game"

    def test_no_match_returns_none(self):
        assert detect_tag("普通文本", END_PATTERNS) is None
        assert detect_tag("普通文本", REC_TAGS) is None

    def test_detect_first_match(self):
        # If multiple tags present, returns first match in dict iteration order
        text = "[END_SAFETY][END_GOAL_ACHIEVED]"
        result = detect_tag(text, END_PATTERNS)
        assert result in ("safety", "goal_achieved")  # depends on dict order
        # Verify it found *something*
        assert result is not None


class TestParseScaleTags:
    """parse_scale_tags extracts scale answers from text."""

    def test_single_scale(self):
        text = "[SCALE:PHQ-9:Q1:S3][SCALE:PHQ-9:Q2:S4]"
        result = parse_scale_tags(text)
        assert result == {"PHQ-9": {1: 3, 2: 4}}

    def test_multiple_scales(self):
        text = "[SCALE:PHQ-9:Q1:S2][SCALE:GAD-7:Q1:S5]"
        result = parse_scale_tags(text)
        assert result == {"PHQ-9": {1: 2}, "GAD-7": {1: 5}}

    def test_no_scales(self):
        assert parse_scale_tags("普通文本") == {}

    def test_empty_string(self):
        assert parse_scale_tags("") == {}
