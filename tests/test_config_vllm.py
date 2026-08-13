"""Production vLLM configuration is selected by profile, not model inventory."""

import importlib
import sys


def test_config_exposes_vllm_endpoint_for_the_a100_profile(monkeypatch):
    original = sys.modules.get("config")
    monkeypatch.setenv("VOICECHAT_DEPLOYMENT_PROFILE", "a100_80g")
    sys.modules.pop("config", None)
    try:
        config = importlib.import_module("config")

        assert config.DIALOGUE_BACKEND == "vllm"
        assert config.DIALOGUE_BASE_URL == "http://127.0.0.1:8000/v1"
        assert config.OLLAMA_MODEL == "Qwen/Qwen2.5-72B-Instruct-AWQ"
        assert config.AGENT_BACKEND == "vllm"
        assert config.AGENT_MODEL == "Qwen/Qwen2.5-3B-Instruct-AWQ"
        assert config.AGENT_MODEL_SERVER == "http://127.0.0.1:8001/v1"
        assert config.GUARD_MODEL is None
        assert config.GUARD_MODEL_SERVER == ""
    finally:
        sys.modules.pop("config", None)
        if original is not None:
            sys.modules["config"] = original


def test_config_exposes_the_local_vllm_profile(monkeypatch):
    original = sys.modules.get("config")
    monkeypatch.setenv("VOICECHAT_DEPLOYMENT_PROFILE", "dev_vllm_6g")
    sys.modules.pop("config", None)
    try:
        config = importlib.import_module("config")

        assert config.DIALOGUE_BACKEND == "vllm"
        assert config.DIALOGUE_BASE_URL == "http://127.0.0.1:18000/v1"
        assert config.OLLAMA_MODEL == "gemma-2b-awq"
        assert config.AGENT_BACKEND == "vllm"
        assert config.AGENT_MODEL_SERVER == "http://127.0.0.1:18001/v1"
    finally:
        sys.modules.pop("config", None)
        if original is not None:
            sys.modules["config"] = original
