"""Tests for core.tags — canonical import path for tag detection & cleaning.

These mirror a subset of tests/test_pipeline.py but import from the new
core package, locking in the refactored module boundary. The pipeline
re-export compatibility is still covered by test_pipeline.py.
"""

import pytest

from core.tags import (
    END_PATTERNS,
    REC_TAGS,
    clean_for_display,
    clean_for_tts,
    detect_tag,
    parse_scale_tags,
    _contains_internal_leak,
)


class TestCoreTagsDetection:
    def test_end_tag(self):
        assert detect_tag("聊完了[END_GOAL_ACHIEVED]", END_PATTERNS) == "goal_achieved"

    def test_rec_tag(self):
        assert detect_tag("试试[REC_MEDITATION]", REC_TAGS) == "meditation"

    def test_none(self):
        assert detect_tag("今天天气不错", END_PATTERNS) is None

    def test_custom_patterns_dict(self):
        custom = {r"\[CUSTOM\]": "custom"}
        assert detect_tag("x[CUSTOM]y", custom) == "custom"


class TestCoreTagsParsing:
    def test_scale_tags(self):
        assert parse_scale_tags("[SCALE:PHQ-9:Q2:S1]") == {"PHQ-9": {2: 1}}

    def test_case_insensitive_match_normalizes_to_canonical_name(self):
        assert parse_scale_tags("[scale:gad-7:q3:s2]") == {"GAD-7": {3: 2}}


class TestCoreTagsCleaning:
    def test_display_strips_breath(self):
        assert clean_for_display("你好[breath]世界") == "你好世界"

    def test_tts_keeps_breath(self):
        assert clean_for_tts("你好[breath]世界") == "你好[breath]世界"

    def test_mixed_separator_keeps_spoken(self):
        assert clean_for_display("分析内容|||口播内容") == "口播内容"

    def test_internal_leak_removed(self):
        assert clean_for_display("这是情感反映技术") == "这是技术"

    def test_internal_leak_detector(self):
        assert _contains_internal_leak("策略选择：共情") is True
        assert _contains_internal_leak("普通对话") is False


class TestOrphanThinkMarker:
    """The serving stack returns ``</think>`` without its opening partner.

    Observed on a real session: the closing marker stayed in the reply, the TTS
    voice read it out, and the sentence was spoken twice.  The paired pattern
    ``<think>...</think>`` cannot match a lone closer, so the cleaning step has
    to treat it as a boundary on its own.
    """

    SPOKEN = "可以试试肌肉放松，就在左边“轻松一下”里，点开就能开始。"

    def test_orphan_closer_does_not_repeat_the_sentence(self):
        raw = self.SPOKEN + "</think>" + self.SPOKEN
        assert clean_for_display(raw) == self.SPOKEN

    def test_orphan_closer_never_reaches_tts(self):
        raw = self.SPOKEN + "</think>" + self.SPOKEN
        out = clean_for_tts(raw)
        assert "think" not in out.lower()
        assert out == self.SPOKEN

    def test_reasoning_before_closer_is_dropped(self):
        raw = "让我想想该怎么说。</think>" + self.SPOKEN
        assert clean_for_display(raw) == self.SPOKEN

    def test_trailing_closer_keeps_the_reply(self):
        assert clean_for_display(self.SPOKEN + "</think>") == self.SPOKEN

    def test_paired_block_still_removed(self):
        raw = "<think>内部推理</think>" + self.SPOKEN
        assert clean_for_display(raw) == self.SPOKEN

    def test_marker_only_reply_becomes_empty(self):
        assert clean_for_display("</think>") == ""

    def test_plain_reply_untouched(self):
        assert clean_for_display(self.SPOKEN) == self.SPOKEN
