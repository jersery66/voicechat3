"""Version-compatibility tests for the per-session runtime manifest.

Schema 1 sessions were recorded before the config-driven merge and cannot be
regenerated, so the reader must accept both versions, must not invent values a
version never carried, and must stay read-only on historical files.
"""

from __future__ import annotations

import json

import pytest

from deployment.runtime_config import load_runtime_config
from research.runtime_manifest import (
    MANIFEST_ARTIFACT_TYPE,
    MANIFEST_SCHEMA_VERSION,
    SCHEMA_2_ONLY_FIELDS,
    RuntimeManifestError,
    build_runtime_manifest,
    normalize_runtime_manifest,
    read_runtime_manifest,
)

# Exactly the field set produced by the last schema-1 implementation
# (research/runtime_manifest.py before the config-driven merge).
V1_SAMPLE = {
    "schema_version": 1,
    "artifact_type": MANIFEST_ARTIFACT_TYPE,
    "generated_at": "2026-08-20T01:02:03+00:00",
    "app_version": "1.4.0",
    "git_sha": "32793d3",
    "deployment_profile": "rtxpro6000_96g",
    "dialogue_model": "Qwen/Qwen2.5-72B-Instruct-AWQ",
    "dialogue_base_url": "http://127.0.0.1:8000/v1",
    "agent_model": "Qwen/Qwen2.5-3B-Instruct-AWQ",
    "agent_base_url": "http://127.0.0.1:8001/v1",
    "stt_model": "funasr-paraformer",
    "tts_model": "voxcpm2",
    "prompt_version": "a" * 64,
    "effective_prompt_hash": "a" * 64,
    "scale_definition_hash": "b" * 64,
    "rag_corpus_hash": "c" * 64,
    "relaxation_catalog_version": "d" * 64,
    "git_dirty": False,
    "secrets_included": False,
}


def _config(tmp_path, *, engine: str | None = "llamacpp", base_url="http://127.0.0.1:8000/v1",
            api_key="EMPTY"):
    engine_line = f"  engine: {engine}\n" if engine else ""
    agent_engine_line = f"  engine: {engine}\n" if engine else ""
    path = tmp_path / "runtime.yaml"
    path.write_text(
        "dialogue:\n"
        "  provider: openai_compatible\n"
        f"{engine_line}"
        f"  base_url: {base_url}\n"
        "  model: dialogue-model\n"
        f"  api_key: {api_key}\n"
        "agent:\n"
        "  enabled: true\n"
        "  provider: openai_compatible\n"
        f"{agent_engine_line}"
        "  base_url: http://127.0.0.1:8001/v1\n"
        "  model: agent-model\n"
        "asr:\n"
        "  provider: funasr\n"
        "tts:\n"
        "  provider: voxcpm\n"
        "runtime:\n"
        "  immutable_contract: true\n",
        encoding="utf-8",
    )
    return load_runtime_config(path)


# ---------------------------------------------------------------------------
# Reading schema 1
# ---------------------------------------------------------------------------

def test_schema_1_manifest_is_read_without_inventing_provenance():
    normalized = normalize_runtime_manifest(V1_SAMPLE)

    assert normalized["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert normalized["source_schema_version"] == 1
    assert normalized["deployment_profile"] == "rtxpro6000_96g"
    assert normalized["dialogue_model"] == "Qwen/Qwen2.5-72B-Instruct-AWQ"
    assert normalized["agent_model"] == "Qwen/Qwen2.5-3B-Instruct-AWQ"
    assert normalized["dialogue_base_url"] == "http://127.0.0.1:8000/v1"
    assert normalized["prompt_version"] == "a" * 64
    # Nothing a schema-1 file never carried may be guessed.
    assert normalized["dialogue_provider"] is None
    assert normalized["dialogue_engine"] is None
    assert normalized["agent_enabled"] is None
    assert normalized["asr_provider"] is None
    assert normalized["tts_provider"] is None
    assert normalized["runtime_config"] is None


def test_schema_1_manifest_reports_every_gap_it_cannot_answer():
    normalized = normalize_runtime_manifest(V1_SAMPLE)

    assert set(normalized["provenance_gaps"]) == set(SCHEMA_2_ONLY_FIELDS)
    assert "dialogue_provider" in normalized["provenance_gaps"]
    assert "asr_provider" in normalized["provenance_gaps"]


def test_reading_a_schema_1_file_is_read_only(tmp_path):
    path = tmp_path / "runtime_manifest.json"
    path.write_text(json.dumps(V1_SAMPLE, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    normalized = read_runtime_manifest(path)

    assert normalized["source_schema_version"] == 1
    assert path.read_bytes() == before, "a historical manifest must never be rewritten"


def test_schema_1_manifest_with_only_the_core_fields_still_reads():
    minimal = {
        "schema_version": 1,
        "deployment_profile": "dev_6g",
        "dialogue_model": "qwen2.5:7b",
    }

    normalized = normalize_runtime_manifest(minimal)

    assert normalized["dialogue_model"] == "qwen2.5:7b"
    assert normalized["generated_at"] is None
    assert normalized["secrets_included"] is None


# ---------------------------------------------------------------------------
# Reading schema 2
# ---------------------------------------------------------------------------

def test_schema_2_manifest_keeps_its_provenance_and_reports_no_gaps(tmp_path):
    raw = build_runtime_manifest(_config(tmp_path), git_sha="abc123", started_at="2026-01-01T00:00:00Z")
    normalized = normalize_runtime_manifest(raw)

    assert raw["schema_version"] == MANIFEST_SCHEMA_VERSION == 2
    assert normalized["source_schema_version"] == 2
    assert normalized["runtime_config"] == "runtime.yaml"
    assert normalized["dialogue_provider"] == "openai_compatible"
    assert normalized["dialogue_engine"] == "llamacpp"
    assert normalized["agent_enabled"] is True
    assert normalized["asr_provider"] == "funasr"
    assert normalized["tts_provider"] == "voxcpm"
    assert normalized["provenance_gaps"] == []
    assert normalized["deployment_profile"] is None


def test_undeclared_engine_shows_up_as_a_provenance_gap(tmp_path):
    raw = build_runtime_manifest(_config(tmp_path, engine=None), git_sha="abc123")
    normalized = normalize_runtime_manifest(raw)

    assert "dialogue_engine" in normalized["provenance_gaps"]
    assert "agent_engine" in normalized["provenance_gaps"]
    # The rest of the schema-2 provenance is still complete.
    assert normalized["dialogue_provider"] == "openai_compatible"
    assert normalized["asr_provider"] == "funasr"


def test_schema_2_round_trips_through_a_file(tmp_path):
    raw = build_runtime_manifest(_config(tmp_path), git_sha="round-trip")
    path = tmp_path / "runtime_manifest.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    normalized = read_runtime_manifest(path)

    assert normalized["git_sha"] == "round-trip"
    assert normalized["source_schema_version"] == 2


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version", [3, 0, "2", True])
def test_unsupported_or_malformed_schema_version_is_rejected(version):
    payload = dict(V1_SAMPLE, schema_version=version)

    with pytest.raises(RuntimeManifestError) as exc:
        normalize_runtime_manifest(payload)

    assert "schema_version" in str(exc.value)


def test_missing_schema_version_is_rejected():
    payload = {key: value for key, value in V1_SAMPLE.items() if key != "schema_version"}

    with pytest.raises(RuntimeManifestError) as exc:
        normalize_runtime_manifest(payload)

    assert "schema_version" in str(exc.value)


def test_unrelated_artifact_is_rejected():
    with pytest.raises(RuntimeManifestError) as exc:
        normalize_runtime_manifest(dict(V1_SAMPLE, artifact_type="TURN_TRACE"))

    assert "artifact_type" in str(exc.value)


def test_non_mapping_payload_is_rejected():
    with pytest.raises(RuntimeManifestError):
        normalize_runtime_manifest([V1_SAMPLE])


def test_missing_and_malformed_files_raise_a_typed_error(tmp_path):
    with pytest.raises(RuntimeManifestError):
        read_runtime_manifest(tmp_path / "absent.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeManifestError):
        read_runtime_manifest(broken)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def test_manifest_never_records_api_keys_or_endpoint_credentials(tmp_path):
    raw = build_runtime_manifest(
        _config(tmp_path, base_url="http://operator:s3cr3t@127.0.0.1:8000/v1?token=abc123"),
        git_sha="secret-check",
    )
    encoded = json.dumps(raw, ensure_ascii=False)

    assert "s3cr3t" not in encoded
    assert "token=abc123" not in encoded
    assert "api_key" not in encoded.lower()
    assert raw["secrets_included"] is False
    assert raw["dialogue_base_url"] == "http://127.0.0.1:8000/v1"


def test_plain_loopback_endpoints_are_recorded_verbatim(tmp_path):
    raw = build_runtime_manifest(_config(tmp_path), git_sha="plain")

    assert raw["dialogue_base_url"] == "http://127.0.0.1:8000/v1"
    assert raw["agent_base_url"] == "http://127.0.0.1:8001/v1"
