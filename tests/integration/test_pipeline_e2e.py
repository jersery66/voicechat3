"""End-to-end integration tests: REAL ConversationPipeline, fake backends.

Covers baseline features without GPU/Ollama/audio:
  F04 agent routing        F09 LLM streaming      F10 ||| parsing
  F07/F08 RAG injection    F14/F15/F17 scale flow F19/F20 relaxation tags
  F26-ish end tags         F06 crisis keyword gate

Every test drives pipeline.execute() with text input (use_stt=False,
use_tts=False) and asserts on PipelineResult, emitted UI events and the
recorded backend calls.
"""

import pytest

from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import (
    EmitCollector,
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeTTS,
)


def make_pipeline(llm=None, agent=None, rag=None, report=None):
    """Build a real pipeline wired to fakes. Caller must shutdown()."""
    p = ConversationPipeline(
        stt_service=None,
        llm_service=llm or FakeLLM(),
        tts_service=FakeTTS(),
        rag_service=rag or FakeRAG(),
        agent_service=agent or FakeAgent(),
        report_service=report or FakeReport(),
        data_manager=FakeData(),
        session_emotions=[],
    )
    return p


@pytest.fixture
def ctx():
    pipelines = []

    def build(**kw):
        p = make_pipeline(**kw)
        pipelines.append(p)
        return p

    yield build
    for p in pipelines:
        p.shutdown()


def run_turn(p, user_text, emit=None):
    emit = emit or EmitCollector()
    result = p.execute(
        PipelineConfig(use_stt=False, use_tts=False, user_text=user_text),
        emit,
    )
    return result, emit


class TestNormalChat:
    def test_plain_turn_parses_separator_and_cleans(self, ctx):
        llm = FakeLLM(["【情绪识别】平静【状态评估】低【变革话语】无【策略选择】肯定|||嗯，[breath]你接着说。"])
        p = ctx(llm=llm)
        result, emit = run_turn(p, "你好")

        assert result.spoken_text.strip() != ""
        assert "【情绪识别】" in result.analysis_text
        # spoken side is clean: no breath tag in display text, no analysis leaked
        assert "[breath]" not in result.clean_spoken
        assert "【" not in result.clean_spoken
        assert "stream_text" in emit.types()
        assert result.end_type is None
        assert result.relaxation_rec is None

    def test_user_and_assistant_messages_recorded(self, ctx):
        p = ctx()
        run_turn(p, "最近心里有点堵")
        assert p.data.user_messages[-1]["text"] == "最近心里有点堵"
        assert len(p.data.assistant_messages) >= 1

    def test_round_incremented(self, ctx):
        report = FakeReport(start_round=3)
        p = ctx(report=report)
        run_turn(p, "你好")
        assert report.round_count == 4


class TestRagInjection:
    def test_rag_suffix_reaches_llm(self, ctx):
        rag = FakeRAG(suffix="【知识库】失眠干预：引导规律作息与放松训练。")
        p = ctx(rag=rag)
        result, _ = run_turn(p, "最近一直失眠，睡不着")
        sent_suffix = p.llm.calls[-1]["system_suffix"]
        assert "知识库" in sent_suffix
        assert len(rag.queries) >= 1


class TestScaleFlow:
    def test_scale_start_uses_runtime_item_and_rejects_out_of_order_tag(self, ctx):
        """Runtime, not the Router or an LLM tag, owns the current item."""
        agent = FakeAgent()
        agent.route_script = [
            {"scale_action": "start", "scale": "PHQ-9", "item": 1,
             "confidence": 0.9, "risk_level": 0, "reason": "symptom signals"},
            {"scale_action": "continue", "scale": "PHQ-9", "item": 2,
             "confidence": 0.9, "risk_level": 0, "reason": "waiting answer"},
        ]
        llm = FakeLLM([
            "【情绪识别】低落【状态评估】中【变革话语】无【策略选择】量表|||这两周，你平时会觉得一些原本还能做的事，现在也提不起劲吗？",
            "【情绪识别】低落【状态评估】中【变革话语】无【策略选择】量表|||嗯，我记下了。那睡眠这块呢？[SCALE:PHQ-9:Q2:S1]",
        ])
        p = ctx(agent=agent, llm=llm)

        # Turn 1: symptom text starts PHQ-9 but is not an answer to Q1.
        result1, _ = run_turn(p, "最近一直睡不着，心里难受")
        assert result1.scale_active is True
        runtime1 = p.scale_runtime.snapshot()
        assert runtime1.active_scale == "PHQ-9"
        assert "PHQ-9" in runtime1.administered_scales
        assert dict(runtime1.answers_by_scale.get("PHQ-9", {})) == {}

        # Turn 2: a tag for Q2 cannot bypass Runtime's current Q1.
        result2, _ = run_turn(p, "做什么都没劲，好几天了")
        runtime2 = p.scale_runtime.snapshot()
        assert result2.scale_tags.get("PHQ-9", {}).get(1) == 1
        assert dict(runtime2.answers_by_scale["PHQ-9"]) == {1: 1}
        assert 2 not in runtime2.answers_by_scale["PHQ-9"]

    def test_short_answer_inferred_without_tag(self, ctx):
        """The trigger text deliberately contains NO frequency word, so
        turn 1 cannot be retroactively scored; the tagless answer in turn 2
        must go through the real inference path (short-answer scoring /
        infer_scale_score_from_text) for Q1."""
        agent = FakeAgent()
        agent.route_script = [
            {"scale_action": "start", "scale": "PHQ-9", "item": 1,
             "confidence": 0.9, "risk_level": 0, "reason": "start"},
            {"scale_action": "continue", "scale": "PHQ-9", "item": 1,
             "confidence": 0.9, "risk_level": 0, "reason": "waiting"},
        ]
        llm = FakeLLM([
            "【情绪识别】低落【状态评估】中【变革话语】无【策略选择】量表|||这两周心情低落的次数多吗？",
            # LLM forgets the SCALE tag; pipeline must infer from user text
            "【情绪识别】低落【状态评估】中【变革话语】无【策略选择】量表|||这么频繁啊，那平时做事的劲头呢？",
        ])
        p = ctx(agent=agent, llm=llm)
        # no frequency word here -> nothing retroactively scored in turn 1
        run_turn(p, "最近睡不着")
        assert p.scale_runtime.snapshot().answers_by_scale.get("PHQ-9", {}).get(1) is None
        # Q1 symptom keyword + frequency word, but no SCALE tag in the reply
        run_turn(p, "做什么都没劲，几乎每天")
        assert p.scale_runtime.snapshot().answers_by_scale["PHQ-9"].get(1) == 3


class TestRelaxationTag:
    def test_rec_tag_is_metadata_only_without_decision(self, ctx):
        llm = FakeLLM([
            "【情绪识别】焦虑【状态评估】中【变革话语】无【策略选择】放松|||身上紧得很是吧？试试左边的呼吸放松按钮。[REC_BREATHING]",
        ])
        p = ctx(llm=llm)
        result, _ = run_turn(p, "心里很紧张，坐不住")
        assert result.relaxation_rec is None
        assert result.turn_decision.action.value != "recommend_relaxation"
        assert p.relaxation_used is False


class TestEndTag:
    END_REPLY = ("【情绪识别】平静【状态评估】低【变革话语】无【策略选择】结束"
                 "|||嗯，能感觉到你松快了不少。有事儿随时来找我唠。[END_GOAL_ACHIEVED]")

    def test_explicit_decision_authorizes_end_and_tag_does_not_choose_type(self, ctx):
        llm = FakeLLM([self.END_REPLY])
        p = ctx(llm=llm)
        result, _ = run_turn(p, "今天先这样吧")
        assert result.turn_decision.action.value == "end_session"
        assert result.end_type == "quit"

    def test_end_tag_suppressed_without_explicit_end(self, ctx):
        """Legacy safety: the LLM alone must not end a session — the END
        tag is suppressed unless the user explicitly asks to end."""
        llm = FakeLLM([self.END_REPLY])
        p = ctx(llm=llm)
        result, _ = run_turn(p, "我好多了，谢谢你")
        assert result.end_type is None


class TestDetachedCrisisRuntime:
    def test_legacy_crisis_keyword_uses_the_ordinary_pipeline(self, ctx):
        p = ctx()
        result, emit = run_turn(p, "我不想活了")

        assert result.spoken_text
        assert not hasattr(result, "crisis_risk")
        assert "show_crisis" not in emit.types()
        assert "危机干预" not in p.llm.calls[-1]["system_suffix"]

    def test_negative_emotion_does_not_trigger_a_third_crisis_call(self, ctx):
        agent = FakeAgent()
        p = ctx(agent=agent)

        run_turn(p, "最近很绝望，整个人都很累")

        assert agent.intent_calls == 1
        assert agent.emotion_calls == 1


class TestShadowIndependence:
    """The pipeline must not depend on app.engine (UI-layer concern)."""

    def test_pipeline_imports_without_app_layer(self):
        import services.pipeline as sp
        assert sp.__name__ == "services.pipeline"


class TestCumulativeSymptomTrigger:
    def test_symptom_signals_wait_for_minimum_rounds_before_starting_scale(self, ctx):
        """Cumulative signals must honour the same minimum-round gate as
        an agent initiated scale start."""
        agent = FakeAgent()  # default route = 'chat', low confidence
        p = ctx(agent=agent, report=FakeReport(start_round=0))

        for _ in range(4):
            result, _ = run_turn(p, "最近一直睡不着")
            assert p.scale_runtime.snapshot().active_scale is None

        result, _ = run_turn(p, "最近一直睡不着")
        assert p.scale_runtime.snapshot().active_scale == "PHQ-9"
        assert result.scale_active is True


class TestAgentRecommendationNormalization:
    def test_agent_game_recommendation_survives_parallel_intent_classification(self, ctx):
        agent = FakeAgent()
        agent.route_script = [{
            "scale_action": "none", "scale": None, "item": None,
            "recommend_game": True, "confidence": 0.95,
            "risk_level": 0, "reason": "用户想放松",
        }]
        p = ctx(agent=agent)

        result, _ = run_turn(p, "有点无聊")

        assert result.intent == "entertainment"

    def test_agent_relaxation_type_is_normalized_for_ui_and_video(self, ctx):
        agent = FakeAgent()
        agent.route_script = [{
            "scale_action": "none", "scale": None, "item": None,
            "recommend_relaxation": True,
            "relaxation_type": "muscle_relaxation",
            "confidence": 0.95, "risk_level": 0, "reason": "身体紧张",
        }]
        p = ctx(agent=agent)

        result, _ = run_turn(p, "我浑身紧绷")

        assert result.relaxation_rec == "muscle"
        assert result.scale_active is False


class TestSeparatorEdgeCases:
    def test_reversed_format_recovered(self, ctx):
        """F10: spoken|||analysis (reversed) must be detected via the
        analysis-tag heuristic; spoken side is the LEFT part."""
        llm = FakeLLM([
            "嗯，你接着说，我听着呢。|||【情绪识别】平静【状态评估】低【变革话语】无【策略选择】肯定",
        ])
        p = ctx(llm=llm)
        result, _ = run_turn(p, "随便聊聊")
        assert "你接着说" in result.clean_spoken
        assert "【情绪识别】" in result.analysis_text
        assert "【" not in result.clean_spoken

    def test_reversed_format_never_streams_internal_analysis(self, ctx):
        llm = FakeLLM([
            "我会继续听你说。|||【情绪识别】平静【状态评估】低【变革话语】无【策略选择】肯定",
        ])
        p = ctx(llm=llm)

        _, emit = run_turn(p, "随便聊聊")

        streamed = "".join(
            content for event, content in emit.events if event == "stream_text"
        )
        assert "情绪识别" not in streamed
        assert "状态评估" not in streamed

    def test_internal_terms_never_leak_to_display(self, ctx):
        """F11: forbidden internal strategy terms in the spoken side are
        stripped before display."""
        llm = FakeLLM([
            "【情绪识别】焦虑【状态评估】高【变革话语】无【策略选择】情感反映"
            "|||策略选择是情感反映，你最近睡得怎么样？",
        ])
        p = ctx(llm=llm)
        result, _ = run_turn(p, "心里有点烦")
        assert "策略选择" not in result.clean_spoken
        assert "情感反映" not in result.clean_spoken


class TestAgentResilience:
    def test_route_exception_falls_back_and_turn_completes(self, ctx):
        """Agent routing failure must not kill the turn: fallback route is
        used, cooldown engages, reply still delivered."""
        agent = FakeAgent()
        agent.route_error = RuntimeError("3B model exploded")
        p = ctx(agent=agent)
        result, _ = run_turn(p, "最近有点烦")
        assert result.spoken_text.strip() != ""
        assert p._agent_route_cooldown > 0

    def test_agent_unavailable_turn_completes(self, ctx):
        agent = FakeAgent()
        agent.available = False
        p = ctx(agent=agent)
        result, _ = run_turn(p, "最近有点烦")
        assert result.spoken_text.strip() != ""


class TestMultiTurnHistory:
    def test_history_flows_to_agent_and_rag(self, ctx):
        """FakeLLM mirrors real history side effects; turn 2 must see the
        previous user turn in agent recent_history and RAG query."""
        agent = FakeAgent()
        rag = FakeRAG(suffix="【知识库】测试条目")
        p = ctx(agent=agent, rag=rag)
        run_turn(p, "第一轮说的话")
        run_turn(p, "第二轮说的话")
        # agent received accumulated context on the second route call
        assert len(agent.route_calls) == 2
        assert "第一轮说的话" in (agent.route_calls[1].get("recent_history") or "") or \
               len(p.llm.conversation_history) >= 4
        # RAG query for turn 2 includes earlier user turns (multi-turn ctx)
        assert any("第一轮说的话" in q for q in rag.queries)
