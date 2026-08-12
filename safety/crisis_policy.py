"""Conservative deterministic safety policy used before dialogue generation."""

from __future__ import annotations

from safety.types import EvidenceSpan, SafetyAction, SafetyDecision


_DENIAL_PATTERNS = (
    "没有伤害自己", "没有自伤", "没有自残", "没有自杀",
    "不想伤害自己", "不想自伤", "不想自残", "不想自杀",
    "没想过伤害自己", "没想过自伤", "没想过自残", "没想过自杀",
    "现在没有这种想法", "目前没有这种想法",
)
_SELF_HARM_TERMS = (
    "自杀", "自伤", "自残", "伤害自己", "想死", "轻生", "不想活",
    "活不下去", "结束生命", "死了算了", "不如死了", "死了一了百了",
)
_MEANS_TERMS = ("跳楼", "割腕", "上吊", "喝农药", "安眠药", "服药", "撞车")
_PLAN_TERMS = ("准备好", "计划", "打算", "决定", "安排好了")
_IMMEDIACY_TERMS = ("现在", "马上", "今晚", "今天", "立刻", "等会儿")
_HISTORICAL_TERMS = ("以前", "曾经", "之前", "过去", "那时候")
_VIOLENCE_TERMS = ("杀了", "打死", "弄死", "报复", "伤害他", "砍死")


class CrisisPolicy:
    """Small deterministic gate for high-consequence routing.

    It intentionally does not estimate a clinical diagnosis. It only answers
    whether the dialogue system must stop normal generation and escalate.
    A plan or a method always overrides a nearby denial clause.
    """

    @staticmethod
    def _compact(text: str) -> str:
        return "".join((text or "").lower().split())

    @staticmethod
    def _matches(text: str, terms: tuple[str, ...], category: str) -> list[EvidenceSpan]:
        return [EvidenceSpan(category=category, text=term) for term in terms if term in text]

    def evaluate(self, text: str) -> SafetyDecision:
        compact = self._compact(text)
        self_harm_evidence = self._matches(compact, _SELF_HARM_TERMS, "self_harm")
        means_evidence = self._matches(compact, _MEANS_TERMS, "means")
        plan_evidence = self._matches(compact, _PLAN_TERMS, "plan")
        immediate_evidence = self._matches(compact, _IMMEDIACY_TERMS, "immediacy")
        historical_evidence = self._matches(compact, _HISTORICAL_TERMS, "historical")
        violence_evidence = self._matches(compact, _VIOLENCE_TERMS, "violence")
        denial_evidence = self._matches(compact, _DENIAL_PATTERNS, "denial")

        self_harm_signal = bool(self_harm_evidence or means_evidence)
        plan = bool(plan_evidence)
        means = bool(means_evidence)
        immediacy = bool(immediate_evidence)
        historical = bool(historical_evidence and self_harm_evidence)
        protective = bool(denial_evidence)
        # A denial only describes protection when it is the sole safety signal.
        current_ideation = bool(self_harm_evidence) and not protective

        if means or (plan and (immediacy or current_ideation)):
            action, risk_level = SafetyAction.EMERGENCY, 9
        elif current_ideation and not protective:
            action, risk_level = SafetyAction.ESCALATE, 7
        elif violence_evidence:
            action, risk_level = SafetyAction.ESCALATE, 6
        elif historical or (self_harm_signal and not protective):
            action, risk_level = SafetyAction.MONITOR, 3
        else:
            action, risk_level = SafetyAction.NONE, 0

        return SafetyDecision(
            current_suicidal_ideation=current_ideation,
            self_harm_signal=self_harm_signal,
            violence_signal=bool(violence_evidence),
            intent=plan and current_ideation,
            plan=plan,
            means=means,
            immediacy=immediacy,
            historical_signal=historical,
            protective_signal=protective,
            evidence_spans=(
                self_harm_evidence + means_evidence + plan_evidence + immediate_evidence
                + historical_evidence + violence_evidence + denial_evidence
            ),
            action=action,
            risk_level=risk_level,
        )
