"""Safety regression tests for explicit denials of self-harm intent."""

from services.agent_service import AgentService


def test_explicit_self_harm_denial_is_not_a_keyword_crisis():
    result = AgentService()._keyword_crisis_risk("我没有伤害自己的想法")

    assert result["risk_level"] == 0
    assert result["immediate_action"] is False


def test_explicit_self_harm_denial_overrides_an_inaccurate_llm_reassessment(monkeypatch):
    service = AgentService()
    monkeypatch.setattr(
        service,
        "_call_json",
        lambda *args, **kwargs: {
            "risk_level": 3,
            "indicators": ["提及自伤"],
            "immediate_action": False,
        },
    )

    result = service.assess_crisis_risk("我没有伤害自己的想法")

    assert result["risk_level"] == 0
    assert result["indicators"] == []


def test_denial_does_not_erase_a_separate_disclosed_suicide_ideation():
    result = AgentService()._keyword_crisis_risk(
        "\u6211\u6709\u81ea\u6740\u7684\u60f3\u6cd5\uff0c\u4f46\u540e\u6765\u53c8\u4e0d\u60f3\u81ea\u6740\u4e86"
    )

    assert result["risk_level"] >= 7
    assert result["immediate_action"] is True


def test_denial_never_hides_a_current_plan_with_a_method():
    """A denial clause must not suppress a separate imminent disclosure."""
    result = AgentService()._keyword_crisis_risk(
        "\u6211\u4e0d\u60f3\u81ea\u6740\uff0c\u4f46\u6211\u5df2\u7ecf\u51c6\u5907\u597d\u4eca\u665a\u5272\u8155"
    )

    assert result["risk_level"] >= 9
    assert result["immediate_action"] is True
