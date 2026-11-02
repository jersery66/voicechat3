"""Deterministic, text-derived interview state shared by production and replay.

Why this module exists
----------------------
The decision prompt asks whether *this* is the right moment, "given the
interview phase so far, the reported symptoms, the assessment history, the
offers already made, and the participant's stated wishes".  A single utterance
cannot answer that question.

Until now only production answered it.  The replay harness in
:mod:`decision.timing_eval` hard-coded ``recent_history=""`` and built the
symptom summary from the *current turn's test annotation*, so:

* the model was asked a longitudinal question with no longitudinal input, and
* the rule baseline was fed gold labels (``turns[].domains``) that no live
  session can produce either.

Both problems are the same problem: two implementations of "what do we know
about this interview".  This module is the single implementation.

Hard rule
---------
Everything here is derived from **utterance text** with deterministic keyword
and pattern matching.  It never reads a trajectory's ``domains``, ``expect``,
``windows`` or ``gold_scale`` fields.  Those are annotations; an annotation in
the prompt is a label leak, and a leak would make the timing numbers
uninterpretable.

What is accumulated
-------------------
``domains``        coarse symptom areas the participant actually mentioned
``duration``       a duration expression copied verbatim from the utterance
``frequency``      the strongest frequency expression seen
``impairments``    functional areas the participant said are affected
``denials``        domains the participant explicitly denied, with the round
``contradictions`` domains asserted in one round and denied in another
``missing``        core domains never touched, so the model can see the gaps
``history``        a bounded, role-labelled tail of the dialogue

Deliberately *not* here: severity scores, diagnoses, and anything that would
suggest the state block is a clinical judgement.  It is a description of what
was said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

__all__ = [
    "InterviewState",
    "SYMPTOM_DOMAIN_KEYWORDS",
    "SYMPTOM_DOMAINS",
    "format_history",
    "replay_messages",
    "symptom_turns_from_deltas",
]


#: Coarse, deterministic symptom domains.  Used only to describe the interview
#: state to the decision model and to the rule baseline; never scored, never
#: reported as severity.
SYMPTOM_DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("睡眠", ("睡不着", "失眠", "睡不好", "早醒", "入睡", "睡太多", "多梦",
              "老是醒", "半夜醒", "容易醒", "醒了就", "醒得早")),
    ("情绪", ("心情不好", "不开心", "低落", "难过", "沮丧", "没希望", "绝望",
              "心里堵", "堵得慌", "提不起兴趣", "没意思")),
    ("焦虑", ("焦虑", "紧张", "心慌", "害怕", "担心", "坐立不安")),
    ("烦躁", ("烦躁", "急躁", "容易生气", "控制不住", "静不下来")),
    ("精力", ("累", "没力气", "没劲", "疲惫", "乏力", "没活力")),
    ("食欲", ("吃不下", "不想吃", "没胃口", "没食欲", "吃太多", "食欲", "饭量")),
    ("注意", ("注意力", "集中不了", "看不进去", "分心", "记不住", "忘事", "走神")),
    ("自责", ("自责", "失败", "不够好", "让家人失望")),
    # "胃" on its own used to match inside "没胃口", which then read as a
    # denial of a somatic complaint the participant never made.
    ("躯体", ("头疼", "胸闷", "胃疼", "胃不舒服", "身体不舒服", "乏力感")),
)

SYMPTOM_DOMAINS: tuple[str, ...] = tuple(name for name, _ in SYMPTOM_DOMAIN_KEYWORDS)


#: Explicit disclaimers.  A participant saying "睡得挺好" is *denying* a domain
#: without using a negation prefix, and the decision model has to see that:
#: without it, one earlier mention keeps the domain "present" forever and the
#: system offers an instrument the participant just said they do not need.
_DENIAL_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("睡眠", ("睡得挺好", "睡得好", "睡得不错", "睡眠没问题", "没失眠过")),
    ("情绪", ("心情挺好", "心情不错", "没什么不开心", "情绪挺好")),
    ("焦虑", ("不怎么焦虑", "不紧张", "没什么好担心的")),
    ("精力", ("精力挺好", "不觉得累", "挺有劲")),
    ("食欲", ("胃口挺好", "吃得下", "食欲挺好")),
)


#: Negations that flip a symptom mention into a denial.  Multi-character forms
#: only: a bare "别" would read "特别" as a negation, which is the same defect
#: that once turned "我现在特别烦" into a refusal.
_NEGATIONS: tuple[str, ...] = (
    "没有",
    "没怎么",
    "从来没",
    "从不",
    "从来不",
    "从未",
    "并不",
    "一点也不",
    "不怎么",
    "不太",
    "不是",
    "没",
)

#: How far before a keyword a negation still applies.  Five characters covers
#: "我也没有特别严重的失眠" without reaching back into the previous clause.
_NEGATION_WINDOW = 6

#: Duration expressions, copied verbatim into the state block.  The optional
#: lead-in ("已经", "快") is matched so that it anchors the search but is not
#: part of the value: the state block should read "持续一个多月", not
#: "持续已经一个多月".
_DURATION_RE = re.compile(
    r"(?:已经|大概|大约|差不多|快|将近|有)?"
    r"(?P<span>[0-9一二两三四五六七八九十半]+(?:个)?(?:多)?(?:月|年|周|星期|礼拜|天))"
)

_DURATION_CUES: tuple[str, ...] = (
    "很久",
    "好久",
    "好长时间",
    "挺长时间",
    "有一阵",
    "一阵子",
    "大半年",
    "一直持续",
    "断断续续",
)

#: Strongest-first.  A later weaker mention never downgrades an earlier one:
#: "有时候" in round 3 must not erase "几乎每天" from round 2.
_FREQUENCY_LEVELS: tuple[tuple[str, int], ...] = (
    ("几乎每天", 6),
    ("每一天", 6),
    ("每天", 6),
    ("天天", 6),
    ("总是", 6),
    ("一直", 6),
    ("大多数日子", 5),
    ("大多数时候", 5),
    ("多数时候", 5),
    ("大部分时间", 5),
    ("多数日子", 5),
    ("经常", 4),
    ("常常", 4),
    ("老是", 4),
    ("有时候", 3),
    ("有时", 3),
    ("偶尔", 2),
)

#: Canonical display labels per frequency level.  The table above holds many
#: phrasings for six levels; the state block shows the level, not the phrasing,
#: so "老是" and "总是" and "天天" cannot read as three different findings.
_FREQUENCY_LABELS: dict[int, str] = {
    0: "从不",
    2: "偶尔",
    3: "有时候",
    4: "经常",
    5: "大多数日子",
    6: "几乎每天",
}

_IMPAIRMENT_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("工作", ("上班", "工作", "干活", "请假", "旷工", "干不了", "效率很低")),
    ("学习", ("上课", "学习", "看书看不进", "旷课", "作业", "考试")),
    ("家务", ("家务", "做饭", "收拾屋子", "带孩子", "带娃")),
    ("社交", ("不想见人", "不见人", "不想出门", "社交", "躲着人", "跟人说话都")),
    ("自理", ("起不来床", "下不了床", "洗漱", "顾不上自己", "吃不上饭")),
)

#: Characters kept per history line, matching the production reader.
_HISTORY_LINE_CHARS = 150
_HISTORY_TURNS = 6

USER_LABEL = "来访者"
ASSISTANT_LABEL = "小薇"


def _strip(text: str) -> str:
    return (text or "").strip()


def _negated_at(text: str, index: int) -> bool:
    """True when the keyword at ``index`` sits inside a negation."""
    window = text[max(0, index - _NEGATION_WINDOW):index]
    for negation in _NEGATIONS:
        if window.endswith(negation):
            return True
    return False


def format_history(
    messages: Iterable[Mapping[str, str]],
    *,
    max_turns: int = _HISTORY_TURNS,
    per_turn_chars: int = _HISTORY_LINE_CHARS,
) -> str:
    """Render a bounded, role-labelled dialogue tail.

    ``messages`` is any sequence of ``{"role": "user"|"assistant", "content": ...}``
    mappings -- the production transcript and the replay script both qualify, so
    the two paths cannot format history differently.

    The bound is applied twice on purpose: per line (so one long utterance
    cannot crowd out the rest) and per turn count (so the block stays small
    enough for a 1.7B model to read).
    """
    recent = list(messages)[-max(1, int(max_turns)) * 2:]
    lines: list[str] = []
    for message in recent:
        role = str(message.get("role") or "user")
        label = USER_LABEL if role == "user" else ASSISTANT_LABEL
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{label}: {content[:per_turn_chars]}")
    return "\n".join(lines)


def symptom_turns_from_deltas(deltas: Optional[Mapping[str, Any]]) -> bool:
    """Does one turn's symptom scoring count as a symptom turn?

    Production increments ``symptom_turns`` when the per-scale deltas are
    positive; the replay path has to use the same rule or the two inputs differ
    before the model ever sees them.
    """
    if not deltas:
        return False
    for value in deltas.values():
        try:
            if float(value) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


@dataclass
class InterviewState:
    """Accumulated, utterance-derived interview evidence for one session."""

    max_turns: int = _HISTORY_TURNS
    per_turn_chars: int = _HISTORY_LINE_CHARS

    domains: set[str] = field(default_factory=set)
    asserted: dict[str, int] = field(default_factory=dict)
    denials: dict[str, int] = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    duration: Optional[str] = None
    duration_round: Optional[int] = None
    frequency: Optional[str] = None
    frequency_level: int = -1
    impairments: dict[str, int] = field(default_factory=dict)
    symptom_turns: int = 0
    turns_seen: int = 0
    _messages: list[dict[str, str]] = field(default_factory=list)

    # -- recording ---------------------------------------------------------

    def record_user(
        self,
        text: str,
        *,
        round_count: int = 0,
        assessment_active: bool = False,
        deltas: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Accumulate one participant utterance.

        ``assessment_active`` mirrors production: symptom turns are only counted
        while no questionnaire is running, because answers to scale items are
        not free-form symptom reports.
        """
        body = _strip(text)
        if not body:
            return
        self.turns_seen += 1
        self._messages.append({"role": "user", "content": body})

        asserted, denied = self._classify_domains(body)
        for domain in asserted:
            self._assert_domain(domain, round_count)
        for domain in denied:
            self._deny_domain(domain, round_count)

        self._record_duration(body, round_count)
        self._record_frequency(body)
        self._record_impairments(body, round_count)

        if not assessment_active and symptom_turns_from_deltas(deltas):
            self.symptom_turns += 1

    def record_assistant(self, text: str) -> None:
        body = _strip(text)
        if body:
            self._messages.append({"role": "assistant", "content": body})

    def reset(self) -> None:
        self.domains.clear()
        self.asserted.clear()
        self.denials.clear()
        self.contradictions.clear()
        self.duration = None
        self.duration_round = None
        self.frequency = None
        self.frequency_level = -1
        self.impairments.clear()
        self.symptom_turns = 0
        self.turns_seen = 0
        self._messages.clear()

    # -- extraction --------------------------------------------------------

    @staticmethod
    def _classify_domains(text: str) -> tuple[list[str], list[str]]:
        """Split this utterance's mentions into asserted and denied domains."""
        asserted: list[str] = []
        denied: list[str] = []
        for domain, keywords in SYMPTOM_DOMAIN_KEYWORDS:
            hits_true = False
            hits_false = False
            for keyword in keywords:
                start = text.find(keyword)
                while start != -1:
                    if _negated_at(text, start):
                        hits_false = True
                    else:
                        hits_true = True
                    start = text.find(keyword, start + len(keyword))
            if hits_true and domain not in asserted:
                asserted.append(domain)
            if hits_false and not hits_true and domain not in denied:
                denied.append(domain)

        # A disclaimer ("睡得挺好") denies a domain without a negation prefix.
        # It only counts when the same utterance does not also describe the
        # symptom: "睡得挺好，就是早醒" is still a sleep complaint.
        for domain, phrases in _DENIAL_PHRASES:
            if domain in asserted or domain in denied:
                continue
            if any(phrase in text for phrase in phrases):
                denied.append(domain)
        return asserted, denied

    def _assert_domain(self, domain: str, round_count: int) -> None:
        previous_denial = self.denials.get(domain)
        if previous_denial is not None and previous_denial != round_count:
            self.contradictions.append(
                f"{domain}：第{previous_denial}轮否认、第{round_count}轮又提到"
            )
            self.denials.pop(domain, None)
        self.domains.add(domain)
        self.asserted[domain] = round_count

    def _deny_domain(self, domain: str, round_count: int) -> None:
        previous_assertion = self.asserted.get(domain)
        if previous_assertion is not None and previous_assertion != round_count:
            self.contradictions.append(
                f"{domain}：第{previous_assertion}轮提到、第{round_count}轮否认"
            )
            self.asserted.pop(domain, None)
        self.denials[domain] = round_count

    def _record_duration(self, text: str, round_count: int) -> None:
        match = _DURATION_RE.search(text)
        if match:
            self.duration = match.group("span")
            self.duration_round = round_count
            return
        for cue in _DURATION_CUES:
            if cue in text:
                self.duration = cue
                self.duration_round = round_count
                return

    def _record_frequency(self, text: str) -> None:
        for phrase, level in _FREQUENCY_LEVELS:
            if phrase in text and level > self.frequency_level:
                self.frequency = _FREQUENCY_LABELS.get(level, phrase)
                self.frequency_level = level

    def _record_impairments(self, text: str, round_count: int) -> None:
        for label, cues in _IMPAIRMENT_CUES:
            if label in self.impairments:
                continue
            if any(cue in text for cue in cues):
                self.impairments[label] = round_count

    # -- projections -------------------------------------------------------

    def missing_domains(self) -> tuple[str, ...]:
        return tuple(name for name in SYMPTOM_DOMAINS if name not in self.domains)

    def history_text(
        self,
        *,
        max_turns: Optional[int] = None,
        per_turn_chars: Optional[int] = None,
    ) -> str:
        return format_history(
            self._messages,
            max_turns=self.max_turns if max_turns is None else max_turns,
            per_turn_chars=self.per_turn_chars if per_turn_chars is None else per_turn_chars,
        )

    def symptom_summary(self) -> str:
        """One deterministic line per evidence kind.

        The leading clause keeps the exact wording production already used
        ("…方面的主诉") so existing logs and the rule baseline read the same
        string; the rest is additive and only appears when there is evidence.
        """
        if not (
            self.domains
            or self.denials
            or self.duration
            or self.frequency
            or self.impairments
        ):
            return ""
        parts: list[str] = []
        if self.domains:
            parts.append("、".join(sorted(self.domains)) + "方面的主诉")
        if self.duration:
            parts.append(f"持续{self.duration}")
        if self.frequency:
            parts.append(f"频率{self.frequency}")
        if self.impairments:
            parts.append("影响" + "、".join(sorted(self.impairments)))
        if self.denials:
            parts.append(
                "明确否认"
                + "、".join(
                    f"{domain}（第{round_count}轮）"
                    for domain, round_count in sorted(self.denials.items())
                )
            )
        if self.contradictions:
            parts.append("前后不一致：" + "；".join(self.contradictions[-2:]))
        missing = self.missing_domains()
        if missing and self.domains:
            # "What has not been asked yet" is part of the evidence, but a full
            # enumeration would dominate the block; four plus "等" is enough to
            # tell the model the picture is incomplete.
            shown = "、".join(missing[:4])
            parts.append("尚未涉及" + shown + ("等" if len(missing) > 4 else ""))
        return "；".join(parts)

    def evidence(self) -> dict[str, object]:
        """Metadata-only view for artifacts and assertions."""
        return {
            "domains": sorted(self.domains),
            "duration": self.duration,
            "frequency": self.frequency,
            "impairments": sorted(self.impairments),
            "denials": dict(sorted(self.denials.items())),
            "contradictions": list(self.contradictions),
            "missing_domains": list(self.missing_domains()),
            "symptom_turns": self.symptom_turns,
            "turns_seen": self.turns_seen,
        }


def replay_messages(turns: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Turn a scripted trajectory into transcript-shaped messages.

    Trajectories may carry an ``assistant`` line per turn (the clinician's
    utterance).  Older fixtures do not, and inventing one would be fabricating
    dialogue, so such turns contribute the participant side only.
    """
    messages: list[dict[str, str]] = []
    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if text:
            messages.append({"role": "user", "content": text})
        reply = str(turn.get("assistant") or "").strip()
        if reply:
            messages.append({"role": "assistant", "content": reply})
    return messages
