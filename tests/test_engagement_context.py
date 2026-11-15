"""Engagement tracking: a stalled topic must hand the writer a direction change."""

from services.pipeline import ConversationPipeline


def _pipeline():
    # The helpers under test are pure; no services or models are needed.
    return ConversationPipeline.__new__(ConversationPipeline)


def test_bare_acknowledgements_are_minimal_replies():
    assert ConversationPipeline._is_minimal_reply("哦哦")
    assert ConversationPipeline._is_minimal_reply("好的吧")
    assert ConversationPipeline._is_minimal_reply("嗯呢？")
    assert ConversationPipeline._is_minimal_reply("   ")


def test_substantive_replies_are_not_minimal():
    assert not ConversationPipeline._is_minimal_reply("今天还可以了")
    assert not ConversationPipeline._is_minimal_reply("就是脑子一直很清醒")
    assert not ConversationPipeline._is_minimal_reply("不踏实")


def test_streak_resets_on_a_substantive_turn():
    pipeline = _pipeline()
    pipeline._minimal_reply_streak = 0
    pipeline._update_engagement("哦哦")
    pipeline._update_engagement("好的吧")
    assert pipeline._minimal_reply_streak == 2
    pipeline._update_engagement("白天也没劲")
    assert pipeline._minimal_reply_streak == 0


def test_no_context_before_the_topic_is_exhausted():
    pipeline = _pipeline()
    pipeline._minimal_reply_streak = 1
    assert pipeline._build_engagement_context() == ""


def test_context_forces_a_direction_change_after_two_bare_replies():
    pipeline = _pipeline()
    pipeline._minimal_reply_streak = 2
    context = pipeline._build_engagement_context()
    assert "话题已经聊完" in context
    assert "不要再追问" in context


def test_longer_stall_offers_two_directions_as_well_as_rest():
    pipeline = _pipeline()
    pipeline._minimal_reply_streak = 4
    context = pipeline._build_engagement_context()
    assert "两个新方向" in context
    assert "放松" in context
