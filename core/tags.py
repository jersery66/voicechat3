# core.tags — tag detection & cleaning (single source of truth).
#
# Extracted from services/pipeline.py without behavior change.
# services/pipeline.py re-exports every name below for backward
# compatibility, so existing call sites keep working untouched.

import re
from typing import Optional, Dict


# ==================== Tag Detection Constants ====================

# Regex pattern -> string name (used for detection in pipeline)
END_PATTERNS = {
    r'\[END_GOAL_ACHIEVED\]': 'goal_achieved',
    r'\[END_TIME_LIMIT\]': 'time_limit',
    r'\[END_SAFETY\]': 'safety',
    r'\[END_INVALID\]': 'invalid',
    r'\[END_QUIT\]': 'quit',
}

REC_TAGS = {
    r'\[REC_BREATHING\]': 'breathing',
    r'\[REC_MUSCLE\]': 'muscle',
    r'\[REC_MEDITATION\]': 'meditation',
    r'\[REC_GAME\]': 'game',
}

SCALE_PATTERN = re.compile(r'\[SCALE:(\w+-\d+):Q(\d+):S?(\d+)\]', re.IGNORECASE)

_SCALE_TAG_BOUNDS = {
    "PHQ-9": (9, 3),
    "GAD-7": (7, 3),
    "PCL-5": (8, 4),
}

# Pre-compiled regexes for hot-path tag stripping (avoids re-compiling per chunk)
_RE_REC_TAG = re.compile(r'\[REC_[A-Z_]+\]')
_RE_END_TAG = re.compile(r'\[END_[A-Z_]+\]')
_RE_SCALE_TAG = re.compile(r'\[SCALE:[^\]]+\]', re.IGNORECASE)
_RE_BRACKETS_CN = re.compile(r'【.*?】')
_RE_PIPE_TAG = re.compile(r'<\|[^|]+\|>')
_RE_BREATH_LAUGH = re.compile(r'\[(?:breath|laughter)\]')
_RE_THINK = re.compile(r'<think>[\s\S]*?</think>')

# Compile END_PATTERNS / REC_TAGS once for fast detection
_COMPILED_END_PATTERNS = [(re.compile(p), name) for p, name in END_PATTERNS.items()]
_COMPILED_REC_TAGS = [(re.compile(p), name) for p, name in REC_TAGS.items()]


def parse_scale_tags(text: str) -> Dict[str, Dict[int, int]]:
    """Extract scale answers from text. Returns {scale_name: {question_num: score}}.

    When the same question appears multiple times, the last occurrence wins
    (consistent with the original behavior and the test contract).
    """
    results: Dict[str, Dict[int, int]] = {}
    for match in SCALE_PATTERN.finditer(text):
        scale_name = match.group(1).upper()
        q_num = int(match.group(2))
        score = int(match.group(3))
        bounds = _SCALE_TAG_BOUNDS.get(scale_name)
        if not bounds:
            continue
        max_question, max_score = bounds
        if not 1 <= q_num <= max_question or not 0 <= score <= max_score:
            continue
        results.setdefault(scale_name, {})[q_num] = score
    return results


def detect_tag(text: str, patterns: dict) -> Optional[str]:
    """Find the first matching tag in text. Returns string name or None.

    Optimized path for the two well-known dicts (END_PATTERNS / REC_TAGS) using
    pre-compiled regexes; falls back to ad-hoc compilation for other dicts.
    """
    if patterns is END_PATTERNS:
        compiled = _COMPILED_END_PATTERNS
    elif patterns is REC_TAGS:
        compiled = _COMPILED_REC_TAGS
    else:
        compiled = [(re.compile(p), name) for p, name in patterns.items()]
    for pattern, tag_type in compiled:
        if pattern.search(text):
            return tag_type
    return None


# ==================== Tag Cleaning ====================

# Internal strategy terms that must NEVER appear in spoken output.
# NOTE: keep this list precise. Over-broad tokens (e.g. the bare word "防御",
# which is a common everyday term) would silently delete legitimate reply
# content, so only unambiguous strategy phrases / model-internal jargon belong
# here.
_FORBIDDEN_INTERNAL_TERMS = [
    "高防御", "中防御", "中等防御", "低防御",
    "无情感反映", "情感反映",
    "具体化开放式提问", "开放式提问",
    "策略选择", "状态评估", "情绪识别", "变革话语",
    "PHQ", "GAD", "PCL", "量表", "问卷", "风险等级", "分数",
    "内部策略", "危机干预", "crisis", "risk level",
    "intent", "emotion detection",
]


def _contains_internal_leak(text: str) -> bool:
    """Check if spoken text contains internal strategy terms that leaked."""
    return any(term in (text or "") for term in _FORBIDDEN_INTERNAL_TERMS)


def clean_for_display(text: str) -> str:
    """Remove all control tags for UI display. Strips [breath]/[laughter] too."""
    if not text:
        return ""
    # If analysis|||spoken got mixed, only keep the spoken part
    if "|||" in text:
        text = text.rsplit("|||", 1)[-1]
    text = _RE_THINK.sub('', text)
    text = _RE_REC_TAG.sub('', text)
    text = _RE_END_TAG.sub('', text)
    text = _RE_SCALE_TAG.sub('', text)
    text = _RE_PIPE_TAG.sub('', text)
    text = _RE_BRACKETS_CN.sub('', text)
    text = _RE_BREATH_LAUGH.sub('', text)
    # Remove internal strategy terms that leaked into spoken output
    for term in _FORBIDDEN_INTERNAL_TERMS:
        text = text.replace(term, "")
    return text.strip()


def clean_for_tts(text: str) -> str:
    """Keep [breath]/[laughter] for TTS, strip control tags."""
    if not text:
        return ""
    # If analysis|||spoken got mixed, only keep the spoken part
    if "|||" in text:
        text = text.rsplit("|||", 1)[-1]
    text = _RE_THINK.sub('', text)
    text = _RE_REC_TAG.sub('', text)
    text = _RE_END_TAG.sub('', text)
    text = _RE_SCALE_TAG.sub('', text)
    text = _RE_PIPE_TAG.sub('', text)
    text = _RE_BRACKETS_CN.sub('', text)
    return text.strip()
