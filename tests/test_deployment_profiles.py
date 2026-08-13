"""Deployment profiles keep local development and A100 production explicit."""

import pytest

from deployment.profiles import get_deployment_profile, resolve_runtime_models


def test_a100_profile_preserves_the_verified_72b_dialogue_model():
    profile = get_deployment_profile("a100_80g")

    assert profile.expected_gpu_memory_gb == 80
    assert profile.dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert profile.runtime_backend == "vllm"
    assert profile.agent_model == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert profile.agent_base_url == "http://127.0.0.1:8001/v1"
    assert profile.guard_base_url == "http://127.0.0.1:8002/v1"
    assert profile.optional_guard_model == "Qwen/Qwen3Guard-Gen-4B"


def test_dev_vllm_profile_is_an_explicit_local_integration_route():
    from deployment.profiles import _PROFILES

    profile = _PROFILES.get("dev_vllm_6g")

    assert profile is not None
    assert profile.runtime_backend == "vllm"
    assert profile.dialogue_model == "gemma-2b-awq"
    assert profile.enable_streaming_tts is False
    assert profile.vllm_request_mode == "completion"
    assert profile.dialogue_max_tokens == 96
    assert profile.agent_base_url == "http://127.0.0.1:18001/v1"


def test_unknown_profile_fails_closed():
    with pytest.raises(ValueError, match="Unknown deployment profile"):
        get_deployment_profile("not-a-profile")


def test_profile_model_wins_over_accidental_live_ollama_detection():
    models = resolve_runtime_models(get_deployment_profile("a100_80g"), environment={})

    assert models.dialogue == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert models.router == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert models.optional_guard == "Qwen/Qwen3Guard-Gen-4B"


def test_explicit_model_environment_override_is_still_supported():
    models = resolve_runtime_models(
        get_deployment_profile("dev_6g"),
        environment={"OLLAMA_MODEL": "custom-dialogue", "AGENT_MODEL": "custom-router"},
    )

    assert models.dialogue == "custom-dialogue"
    assert models.router == "custom-router"
