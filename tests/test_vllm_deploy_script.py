"""The engine service scripts must make the deployment contract observable.

The A100-specific PowerShell launchers were archived with the hardware
profiles; the supported entry points are the engine service scripts plus
the configuration-driven health check.
"""

from pathlib import Path


def test_vllm_engine_service_has_required_model_and_memory_flags():
    source = Path("scripts/engines/vllm/start_service.sh").read_text(encoding="utf-8")

    assert "serve" in source
    assert "--gpu-memory-utilization" in source
    assert "--max-model-len" in source
    assert "--enable-prefix-caching" in source
    assert "--host" in source
    assert "--host 127.0.0.1" in source
    assert "--port" in source


def test_llama_cpp_engine_service_binds_loopback_with_exact_alias():
    source = Path("scripts/engines/llama_cpp/start_service.sh").read_text(encoding="utf-8")

    assert "--host 127.0.0.1" in source
    assert "--alias" in source
    assert "-m" in source


def test_vllm_health_check_uses_models_endpoint_not_ollama():
    source = Path("scripts/check_config.py").read_text(encoding="utf-8")

    assert "def check_dialogue_backend" in source
    assert "DIALOGUE_PROVIDER" in source
    assert '"models"' in source
