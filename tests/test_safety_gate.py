"""Tests for the independently callable deterministic safety gate."""

from safety.crisis_policy import CrisisPolicy
from safety.safety_gate import SafetyGate
from safety.types import SafetyAction


def test_current_plan_and_method_override_a_denial_clause():
    decision = CrisisPolicy().evaluate(
        "\u6211\u4e0d\u60f3\u81ea\u6740\uff0c\u4f46\u6211\u5df2\u7ecf\u51c6\u5907\u597d\u4eca\u665a\u5272\u8155"
    )

    assert decision.action == SafetyAction.EMERGENCY
    assert decision.plan is True
    assert decision.means is True
    assert decision.risk_level == 9


def test_clear_denial_without_other_signal_is_not_escalated():
    decision = CrisisPolicy().evaluate("\u6211\u6ca1\u6709\u4f24\u5bb3\u81ea\u5df1\u7684\u60f3\u6cd5")

    assert decision.action == SafetyAction.NONE
    assert decision.protective_signal is True
    assert decision.current_suicidal_ideation is False


def test_historical_signal_is_retained_without_being_treated_as_immediate():
    decision = CrisisPolicy().evaluate("\u4ee5\u524d\u6211\u66fe\u7ecf\u60f3\u8fc7\u81ea\u6740\uff0c\u4f46\u73b0\u5728\u6ca1\u6709\u8fd9\u79cd\u60f3\u6cd5")

    assert decision.historical_signal is True
    assert decision.action == SafetyAction.MONITOR


def test_optional_guard_cannot_reduce_the_deterministic_emergency_decision():
    class UnderstatingGuard:
        def assess_input(self, text):
            return CrisisPolicy().evaluate("\u6211\u6ca1\u6709\u4f24\u5bb3\u81ea\u5df1\u7684\u60f3\u6cd5")

    decision = SafetyGate(guard_client=UnderstatingGuard()).assess_input(
        "\u6211\u51c6\u5907\u4eca\u665a\u5272\u8155"
    )

    assert decision.action == SafetyAction.EMERGENCY
