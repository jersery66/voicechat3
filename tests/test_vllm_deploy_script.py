"""The deployment launcher must make the A100 vLLM contract observable."""

from pathlib import Path


def test_vllm_launcher_has_required_model_and_memory_flags():
    source = Path("scripts/start_vllm_a100.ps1").read_text(encoding="utf-8")

    assert "vllm serve $Model" in source
    assert "--gpu-memory-utilization" in source
    assert "--max-model-len" in source
    assert "--enable-prefix-caching" in source
    assert "--host" in source
    assert "--port" in source
    assert "vllm serve $Model" in source


def test_vllm_health_check_uses_models_endpoint_not_ollama():
    source = Path("scripts/check_config.py").read_text(encoding="utf-8")

    assert "def check_dialogue_backend" in source
    assert "DIALOGUE_BACKEND" in source
    assert '"models"' in source
