"""Participant-facing prompt and wording contracts."""

from __future__ import annotations

import ast
from pathlib import Path
import re

import config


ROOT = Path(__file__).resolve().parents[1]
AGENT_SOURCE = (ROOT / "services" / "agent_service.py").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    tree = ast.parse(AGENT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(AGENT_SOURCE, node) or ""
    raise AssertionError(f"missing function: {name}")


def _static_participant_prompts() -> list[str]:
    names = (
        "SYSTEM_PROMPT",
        "GREETING_VARIANTS",
        "POST_RELAXATION_MESSAGE",
        "CONTINUE_CHAT_MESSAGE",
        "TIMEOUT_END_MESSAGE",
        "TRANSITION_PROMPT",
        "SUGGESTIONS_PROMPT",
    )
    values: list[str] = []
    for name in names:
        value = getattr(config, name, "")
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value))
    return values


def test_prompt01_system_prompt_says_action_is_already_system_decided():
    assert "本轮动作已经由系统确定" in config.SYSTEM_PROMPT


def test_prompt02_system_prompt_prohibits_redeciding_business_actions():
    prompt = config.SYSTEM_PROMPT
    assert "不要重新决定是否聊天、开始量表、放松、游戏或结束" in prompt


def test_prompt03_system_prompt_preserves_scale_semantics():
    prompt = config.SYSTEM_PROMPT
    for marker in ("时间范围", "频率含义", "否定关系", "核心症状含义"):
        assert marker in prompt


def test_prompt04_system_prompt_limits_one_primary_question():
    assert "每轮最多一个主要问题" in config.SYSTEM_PROMPT


def test_prompt05_system_prompt_confirms_critical_asr_ambiguity():
    prompt = config.SYSTEM_PROMPT
    assert "有无、否定、频率、持续时间、数字、药物、量表答案" in prompt
    assert "简短确认" in prompt


def test_prompt06_system_prompt_withholds_diagnosis_and_treatment_authority():
    prompt = config.SYSTEM_PROMPT
    assert "不进行医学诊断" in prompt
    assert "不替代专业人员作治疗或用药决定" in prompt


def test_prompt07_static_participant_prompts_have_no_audio_control_tags():
    combined = "\n".join(_static_participant_prompts())
    assert "[breath]" not in combined
    assert "[laughter]" not in combined


def test_prompt08_opening_greetings_do_not_assume_prior_relationship():
    combined = "\n".join(config.GREETING_VARIANTS)
    assert "又见面" not in combined
    assert "老朋友" not in combined


def test_prompt09_opening_greetings_do_not_assume_illness_or_distress():
    combined = "\n".join(config.GREETING_VARIANTS)
    for marker in ("不痛快", "不舒服", "堵得慌", "难受", "憋"):
        assert marker not in combined


def test_prompt10_post_relaxation_messages_are_phenomenological_not_leading():
    combined = "\n".join(config.POST_RELAXATION_MESSAGE)
    for marker in ("舒服点", "没那么乱", "紧绷", "松快", "舒坦", "缓过来"):
        assert marker not in combined
    assert any("感觉怎么样" in item or "有什么变化" in item for item in config.POST_RELAXATION_MESSAGE)


def test_prompt11_dynamic_greeting_uses_support_assistant_identity():
    source = _function_source("generate_greeting")
    assert "心理咨询师" not in source
    assert "老朋友" not in source
    assert "心理支持对话助手" in source


def test_prompt12_dynamic_post_relaxation_prompt_has_no_control_tags():
    source = _function_source("generate_post_relaxation_greeting")
    assert "[breath]" not in source
    assert "[laughter]" not in source
    assert "不预设" in source or "不暗示" in source


def test_prompt13_transition_and_suggestion_prompts_use_support_assistant_framing():
    for prompt in (config.TRANSITION_PROMPT, config.SUGGESTIONS_PROMPT):
        assert "心理支持对话助手" in prompt
        assert "心理咨询师" not in prompt


def test_prompt14_router_example_does_not_start_scale_before_policy_minimum():
    source = _function_source("route_conversation_actions")
    assert not re.search(r"第\s*[0-4]轮[^\n]*start_scale", source)
    assert "系统允许的最低轮次" in source or "当前最低轮次门槛" in source


def test_prompt15_participant_prompts_grant_no_action_or_scoring_authority():
    prompt = config.SYSTEM_PROMPT
    forbidden_authority_phrases = (
        "自行选题、打分或推进状态",
        "重新决定是否聊天、开始量表、放松、游戏或结束",
        "输出任何控制标记",
    )
    for phrase in forbidden_authority_phrases:
        assert phrase in prompt
