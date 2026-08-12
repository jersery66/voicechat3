"""Deployment profiles keep local development and A100 production explicit."""

import pytest

from deployment.profiles import get_deployment_profile, resolve_runtime_models


def test_a100_profile_preserves_the_verified_72b_dialogue_model():
    profile = get_deployment_profile("a100_80g")

    assert profile.expected_gpu_memory_gb == 80
    assert profile.dialogue_model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert profile.runtime_backend == "vllm"
    assert profile.optional_guard_model == "qwen3guard:4b"


def test_unknown_profile_fails_closed():
    with pytest.raises(ValueError, match="Unknown deployment profile"):
        get_deployment_profile("not-a-profile")


def test_profile_model_wins_over_accidental_live_ollama_detection():
    models = resolve_runtime_models(get_deployment_profile("a100_80g"), environment={})

    assert models.dialogue == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert models.router == "qwen2.5:3b"


def test_explicit_model_environment_override_is_still_supported():
    models = resolve_runtime_models(
        get_deployment_profile("dev_6g"),
        environment={"OLLAMA_MODEL": "custom-dialogue", "AGENT_MODEL": "custom-router"},
    )

    assert models.dialogue == "custom-dialogue"
    assert models.router == "custom-router"
