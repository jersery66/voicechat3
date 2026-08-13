"""Tests for core.scale_fsm — scale state container and pipeline delegation."""

import pytest

from core.scale_fsm import SCALE_NAMES, ScaleState, delegate_property, fresh_symptom_scores
from services.pipeline import ConversationPipeline


@pytest.fixture
def state():
    return ScaleState()


@pytest.fixture
def pipeline():
    """Pipeline with stub services — only state ownership is exercised."""
    p = ConversationPipeline(
        None, None, None, None, None, None, None,
        session_emotions=[], emotion_tracker=None,
    )
    yield p
    p.shutdown()


class TestScaleStateDefaults:
    """Defaults must match the legacy ConversationPipeline initial values."""

    def test_core_fsm_defaults(self, state):
        assert state.administered == set()
        assert state.answers == {}
        assert state.active_scale is None
        assert state.active_item == 1
        assert state.waiting_answer is False
        assert state.queue == []
        assert state.pause_turns == 0
        assert state.soft_paused is False
        assert state.resume_item == 1

    def test_flow_defaults(self, state):
        assert state.scale_active is False
        assert state.scale_name is None
        assert state.scale_current_item == 0
        assert state.scale_completed is False
        assert state.scale_refused_rounds == 0
        assert state.scale_defer_until_round == 0
        assert state.last_scale_ask_round == -999
        assert state.consecutive_scale_asks == 0

    def test_signal_defaults(self, state):
        assert state.symptom_scores == {name: 0 for name in SCALE_NAMES}
        assert state.symptom_turns == 0
        assert state.last_scale_trigger_round == -999
        assert state.scale_trigger_cooldown == 3

    def test_resume_defaults(self, state):
        assert state.pending_scale_resume is False
        assert state.last_bot_asked_scale is None
        assert state.last_bot_asked_item == 0


class TestScaleStateReset:
    def test_reset_restores_defaults(self, state):
        state.administered.add("PHQ-9")
        state.answers["PHQ-9"] = {1: 2}
        state.active_scale = "PHQ-9"
        state.active_item = 5
        state.waiting_answer = True
        state.queue.append("GAD-7")
        state.pause_turns = 2
        state.soft_paused = True
        state.resume_item = 4
        state.symptom_scores["PHQ-9"] = 5
        state.pending_scale_resume = True

        state.reset()

        assert state.administered == set()
        assert state.answers == {}
        assert state.active_scale is None
        assert state.active_item == 1
        assert state.waiting_answer is False
        assert state.queue == []
        assert state.pause_turns == 0
        assert state.soft_paused is False
        assert state.resume_item == 1
        assert state.symptom_scores == {name: 0 for name in SCALE_NAMES}
        assert state.pending_scale_resume is False

    def test_fresh_symptom_scores_independent(self):
        a = fresh_symptom_scores()
        b = fresh_symptom_scores()
        a["PHQ-9"] = 9
        assert b["PHQ-9"] == 0

    def test_reset_preserves_trigger_cooldown(self):
        """Legacy reset_session() never touched scale_trigger_cooldown;
        reset() must not either (init-only constant)."""
        state = ScaleState()
        state.scale_trigger_cooldown = 5
        state.reset()
        assert state.scale_trigger_cooldown == 5


class TestDelegateProperty:
    class Holder:
        def __init__(self):
            self._scale_state = ScaleState()

        active = delegate_property("active_scale")
        answers = delegate_property("answers")

    def test_scalar_read_write(self):
        h = self.Holder()
        assert h.active is None
        h.active = "GAD-7"
        assert h.active == "GAD-7"
        assert h._scale_state.active_scale == "GAD-7"

    def test_container_mutations_hit_state(self):
        h = self.Holder()
        h.answers["PHQ-9"] = {1: 3}
        assert h._scale_state.answers["PHQ-9"] == {1: 3}
        h.answers.clear()
        assert h._scale_state.answers == {}


class TestPipelineDelegation:
    """ConversationPipeline legacy names must read/write the container."""

    def test_legacy_underscore_names(self, pipeline):
        assert pipeline._active_scale is None
        pipeline._active_scale = "PHQ-9"
        pipeline._active_scale_q = 3
        pipeline._active_scale_waiting_answer = True
        assert pipeline._scale_state.active_scale == "PHQ-9"
        assert pipeline._scale_state.active_item == 3
        assert pipeline._scale_state.waiting_answer is True

    def test_legacy_container_names(self, pipeline):
        pipeline._administered_scales.add("GAD-7")
        pipeline._scale_answers["GAD-7"] = {1: 1}
        pipeline._scale_queue.append("PCL-5")
        assert pipeline._scale_state.administered == {"GAD-7"}
        assert pipeline._scale_state.answers == {"GAD-7": {1: 1}}
        assert pipeline._scale_state.queue == ["PCL-5"]

    def test_legacy_flow_names(self, pipeline):
        pipeline.scale_active = True
        pipeline.symptom_scores = {"PHQ-9": 4, "GAD-7": 0, "PCL-5": 0}
        pipeline.pending_scale_resume = True
        assert pipeline._scale_state.scale_active is True
        assert pipeline._scale_state.symptom_scores["PHQ-9"] == 4
        assert pipeline._scale_state.pending_scale_resume is True

    def test_reset_session_resets_scale_and_relaxation(self, pipeline):
        pipeline._active_scale = "PHQ-9"
        pipeline._administered_scales.add("PHQ-9")
        pipeline.symptom_scores["PHQ-9"] = 3
        pipeline.relaxation_used = True
        pipeline.exit_requested = True
        pipeline._agent_route_cooldown = 2
        pipeline._post_scale_relaxation_done = True
        pipeline._relaxation_recommended_this_session.add("breathing")
        pipeline._game_recommended_this_session = True
        pipeline._pending_relaxation_after_scale = "muscle"
        pipeline._relaxation_candidate = "meditation"
        pipeline._game_candidate = True

        pipeline.reset_session()

        assert pipeline._active_scale is None
        assert pipeline._administered_scales == set()
        assert pipeline.symptom_scores == {name: 0 for name in SCALE_NAMES}
        assert pipeline.relaxation_used is False
        assert pipeline.exit_requested is False
        assert pipeline._agent_route_cooldown == 0
        assert pipeline._post_scale_relaxation_done is False
        assert pipeline._relaxation_recommended_this_session == set()
        assert pipeline._game_recommended_this_session is False
        assert pipeline._pending_relaxation_after_scale is None
        assert pipeline._relaxation_candidate is None
        assert pipeline._game_candidate is False

    def test_get_active_scale_state_reads_container(self, pipeline):
        assert pipeline.get_active_scale_state() is None
        pipeline._active_scale = "GAD-7"
        pipeline._active_scale_q = 2
        snapshot = pipeline.get_active_scale_state()
        assert snapshot == {"scale_name": "GAD-7", "item": 2, "incomplete": True}
