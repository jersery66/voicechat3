"""Offline-oriented decision config: local model path, dtype, strict keys."""

from __future__ import annotations

import pytest

from deployment.runtime_config import RuntimeConfigError, load_runtime_config

BASE = """dialogue:
  provider: openai_compatible
  base_url: http://127.0.0.1:8000/v1
  model: contract-model
  timeout: 90
  max_tokens: 256
  temperature: 0.7
  top_p: 0.9
  enable_thinking: false
  request_mode: chat
  system_role_mode: native
agent:
  enabled: false
asr:
  provider: funasr
tts:
  provider: voxcpm
runtime:
  strict_preflight: true
  immutable_contract: true
"""


def _load(tmp_path, decision_yaml: str = ""):
    path = tmp_path / "runtime.yaml"
    text = BASE + ("decision:\n" + decision_yaml if decision_yaml else "")
    path.write_text(text, encoding="utf-8")
    return load_runtime_config(path)


def test_defaults_reproduce_the_legacy_contract(tmp_path):
    config = _load(tmp_path)
    assert config.decision.mode == "legacy"
    assert config.decision.backend == "legacy_qwen"
    assert config.decision.is_legacy is True
    assert config.decision.model_path is None
    assert config.decision.model_dtype == "bfloat16"
    assert config.decision.allow_remote_endpoint is False
    assert config.decision.allow_model_download is False
    assert config.decision.clinical_review.enabled is False


def test_absolute_model_path_is_kept(tmp_path):
    config = _load(tmp_path, "  model_path: /models/Tacit-1.7B\n")
    assert config.decision.model_path is not None
    assert config.decision.model_path.replace("\\", "/").endswith("models/Tacit-1.7B")


def test_relative_model_path_resolves_against_the_config_file(tmp_path):
    (tmp_path / "bundle").mkdir()
    config = _load(tmp_path, "  model_path: bundle/Tacit-1.7B\n")
    assert config.decision.model_path is not None
    assert config.decision.model_path.endswith("Tacit-1.7B")
    assert config.decision.model_path.startswith(str(tmp_path.resolve()))


def test_model_path_is_not_required_to_exist(tmp_path):
    """A config must load on a machine that has not been provisioned yet."""
    config = _load(tmp_path, "  model_path: nowhere/Tacit-1.7B\n")
    assert config.decision.model_path is not None


def test_model_dtype_must_be_known(tmp_path):
    with pytest.raises(RuntimeConfigError, match="model_dtype"):
        _load(tmp_path, "  model_dtype: int4\n")


@pytest.mark.parametrize("dtype", ["auto", "bfloat16", "float16", "float32"])
def test_known_model_dtypes_are_accepted(tmp_path, dtype):
    config = _load(tmp_path, f"  model_dtype: {dtype}\n")
    assert config.decision.model_dtype == dtype


def test_unknown_decision_key_is_rejected(tmp_path):
    with pytest.raises(RuntimeConfigError, match="unknown"):
        _load(tmp_path, "  model_dir: /models/x\n")


def test_jev_requires_a_jev_backend(tmp_path):
    with pytest.raises(RuntimeConfigError, match="jev"):
        _load(tmp_path, "  mode: jev\n  backend: legacy_qwen\n")


def test_tacit_inprocess_needs_a_local_model(tmp_path):
    with pytest.raises(RuntimeConfigError, match="model_path"):
        _load(tmp_path, "  mode: jev\n  backend: tacit\n  transport: inprocess\n")


def test_tacit_inprocess_accepts_a_local_model_path(tmp_path):
    config = _load(
        tmp_path,
        "  mode: jev\n  backend: tacit\n  transport: inprocess\n  model_path: /models/Tacit-1.7B\n",
    )
    assert config.decision.backend == "tacit"
    assert config.decision.model_path is not None


def test_anyjev_server_needs_an_endpoint(tmp_path):
    with pytest.raises(RuntimeConfigError, match="endpoint"):
        _load(tmp_path, "  mode: jev\n  backend: anyjev_decider\n  transport: server\n")


def test_shadow_needs_a_non_legacy_shadow_backend(tmp_path):
    with pytest.raises(RuntimeConfigError, match="shadow"):
        _load(tmp_path, "  mode: shadow\n")
    with pytest.raises(RuntimeConfigError, match="legacy_qwen"):
        _load(tmp_path, "  mode: shadow\n  shadow_backend: legacy_qwen\n")


def test_summary_exposes_the_offline_knobs(tmp_path):
    config = _load(
        tmp_path,
        "  mode: jev\n  backend: tacit\n  transport: inprocess\n  model_path: /models/Tacit-2B\n"
        "  model_dtype: float16\n",
    )
    summary = config.summary()["decision"]
    assert summary["model_path"].endswith("Tacit-2B")
    assert summary["model_dtype"] == "float16"
    assert summary["mode"] == "jev"
