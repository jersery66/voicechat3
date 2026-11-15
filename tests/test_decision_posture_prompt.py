"""The posture must reach the writer as an instruction, not a reference note.

Before this, the writer received the decision layer's intent and emotion plus a
generic "follow what he cares about", while the system prompt already contained
every conversational move as a general rule.  The general rule won, so the
decision layer changed nothing.  These tests pin the three properties that make
the posture binding: it replaces the intent landing, the contradictory
"open with a question" line is dropped, and a precedence line overrides the
general prompt.
"""

from types import SimpleNamespace

from conversation.contracts import TurnAction, TurnDecision
from decision.contracts import RawDecision
from services.pipeline import ConversationPipeline


def build_pipeline():
    return ConversationPipeline(
        None, None, None, None, None, None, None,
        session_emotions=[], emotion_tracker=None,
    )


def context_for(strategy=None, stance=None, action=TurnAction.CHAT, emotion="sad", **raw_kwargs):
    pipeline = build_pipeline()
    try:
        raw = RawDecision(
            provider="fake",
            intervention="continue_interview",
            strategy=strategy,
            stance=stance,
            emotion=emotion,
            intent_jev="symptom",
            confidence=0.62,
            **raw_kwargs,
        )
        pipeline._last_decision_outcome = SimpleNamespace(decision=raw)
        decision = TurnDecision(
            action=action,
            scale_name="PHQ-9" if action is not TurnAction.CHAT else None,
            strategy=strategy,
            stance=stance,
        )
        return pipeline._build_jev_judgement_context(decision)
    finally:
        pipeline.shutdown()


def test_strategy_replaces_the_intent_landing():
    text = context_for("deepen", "probe")

    assert "本轮只做这一件事" in text
    assert "就着他刚提到的那一个细节再问一层" in text
    # The intent landing would have been a second competing plan.
    assert "这一轮在说身体或症状" not in text


def test_no_posture_keeps_the_old_behaviour():
    text = context_for(None, None)

    assert "本轮只做这一件事" not in text
    assert "这一轮在说身体或症状" in text


def test_stance_decides_whether_a_question_is_required():
    asking = context_for("deepen", "probe")
    not_asking = context_for("hold", "reflect")

    assert "直接问没提到的那个时间" in asking
    assert "直接问没提到的那个时间" not in not_asking
    assert "句末不要有问号" in not_asking


def test_a_hold_turn_overrides_the_general_urge_to_push_forward():
    text = context_for("hold", "reflect")

    assert "优先级" in text
    assert "不追问、不加问题" in text


def test_quiet_stance_drops_the_strategy_from_the_prompt():
    """resolve_posture runs at the decision boundary, so the pipeline only sees
    the reconciled pair.  A quiet turn carries no strategy at all."""
    text = context_for(None, "quiet")

    assert "本轮只做这一件事" not in text
    assert "只用很短的一两句话回应" in text


def test_task_turns_get_only_the_tone_rule():
    text = context_for("deepen", "probe", action=TurnAction.CONTINUE_SCALE)

    assert "这一轮怎么开口" not in text
    assert "本轮只做这一件事" not in text
    assert "语气" in text


def test_a_reflect_turn_rules_out_the_particles_too():
    """The writer used to dodge "no question mark" by writing "……就挂断了
    吗。" -- a question with a full stop.  The rule names the particles because
    the punctuation is not the thing being forbidden."""
    text = context_for("stabilize", "reflect")

    assert "吗" in text and "呢" in text
    assert "把问号换成句号不算做到" in text


def test_a_question_turn_forbids_the_summary_plus_evaluation_opener():
    text = context_for("deepen", "probe")

    assert "那种……的感觉" in text
    assert "就整句删掉" in text


def test_a_reflect_turn_never_gets_an_instruction_to_ask():
    """A deepen strategy plus a reflect stance used to reach the writer as two
    cancelling orders: "ask one layer deeper" and "end without a question
    mark".  On Qwen3.8-27B one such turn produced an empty completion -- no
    content and no thinking -- which the pipeline turned into "sorry, could you
    say that again".  The strategy now has a statement-only restatement."""
    text = context_for("deepen", "reflect")

    assert "再问一层" not in text
    assert "原样接住" in text
    assert "句末不要有问号" in text
    # The generic opener must not smuggle the question back in either.
    assert "开口第一句就问那件事的具体细节" not in text
    assert "直接从问句开始" not in text


def test_explore_also_has_a_statement_only_form():
    text = context_for("explore", "reflect")

    assert "就着他刚提起的这个新话题问一件具体的事" not in text
    assert "就着他刚提起的这件事说一句" in text


def test_a_probe_turn_still_gets_the_asking_form():
    text = context_for("deepen", "probe")

    assert "再问一层" in text
    assert "原样接住" not in text
    assert "直接问没提到的那个时间" in text


def test_an_anxious_acknowledgement_is_not_given_a_question_allowance():
    """「一轮最多一个问题」reads as permission on a turn that may not ask."""
    text = context_for("deepen", "reflect", emotion="anxious")

    assert "一轮最多一个问题" not in text
    assert "这一轮不加问题" in text


def test_an_anxious_question_turn_keeps_the_one_question_rule():
    text = context_for("deepen", "probe", emotion="anxious")

    assert "一轮最多一个问题" in text
