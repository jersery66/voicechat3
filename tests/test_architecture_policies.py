"""Pure policies that form the first independently testable domain layers."""

from assessment.scale_policy import ScalePolicy
from intervention.relaxation_policy import RelaxationPolicy
from knowledge.rag_service import ContextBuilder
from voice.contracts import AudioFrame, Transcription


def test_scale_policy_never_replaces_the_item_while_an_answer_is_pending():
    directive = ScalePolicy().decide(
        route={"scale_action": "start", "scale": "GAD-7", "item": 1},
        active_scale="PHQ-9",
        active_item=3,
        waiting_for_answer=True,
    )

    assert directive.action == "keep_current"
    assert directive.scale_name == "PHQ-9"
    assert directive.item == 3


def test_relaxation_policy_keeps_existing_once_per_session_constraint():
    policy = RelaxationPolicy()

    assert policy.may_recommend(relaxation_used=False, waiting_scale_answer=False) is True
    assert policy.may_recommend(relaxation_used=True, waiting_scale_answer=False) is False
    assert policy.may_recommend(relaxation_used=False, waiting_scale_answer=True) is False


def test_context_builder_applies_the_active_scale_privacy_budget():
    context = ContextBuilder().build(
        ["first context", "second context"], active_scale=True
    )

    assert context == "first context\n\nsecond context"
    assert ContextBuilder(active_scale_limit=5).build(["123456"], active_scale=True) == "12345"


def test_voice_contracts_are_provider_neutral_value_objects():
    frame = AudioFrame(samples=b"audio", sample_rate=16000, channels=1)
    transcription = Transcription(text="hello", is_final=True)

    assert frame.sample_rate == 16000
    assert transcription.is_final is True
