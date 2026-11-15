"""Model integrity: structural gate, manifest round-trip, tamper detection."""

from __future__ import annotations

import json

import pytest

from decision.integrity import (
    MANIFEST_NAME,
    ModelIntegrityError,
    build_manifest,
    load_manifest,
    sha256_file,
    structural_problems,
    summarise,
    verify_model_dir,
    write_manifest,
)


def _checkpoint(root, *, shards=None, index=None, weights=b"\x00" * 512):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text('{"model_type": "qwen3"}', encoding="utf-8")
    (root / "tokenizer.json").write_text("{}", encoding="utf-8")
    names = shards or ["model-00001-of-00001.safetensors"]
    for name in names:
        (root / name).write_bytes(weights)
    if index:
        (root / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {f"w{i}": name for i, name in enumerate(index)}}), encoding="utf-8"
        )
    return root


def test_structural_check_accepts_a_complete_directory(tmp_path):
    root = _checkpoint(tmp_path / "Tacit-1.7B")
    assert structural_problems(root) == []


def test_structural_check_flags_missing_config(tmp_path):
    root = _checkpoint(tmp_path / "m")
    (root / "config.json").unlink()
    assert "missing_required_file:config.json" in structural_problems(root)


def test_structural_check_flags_missing_tokenizer(tmp_path):
    root = _checkpoint(tmp_path / "m")
    (root / "tokenizer.json").unlink()
    problems = structural_problems(root)
    assert any(problem.startswith("missing_tokenizer:") for problem in problems)


def test_structural_check_flags_missing_weights(tmp_path):
    root = _checkpoint(tmp_path / "m")
    (root / "model-00001-of-00001.safetensors").unlink()
    assert any(problem.startswith("missing_weights:") for problem in structural_problems(root))


def test_structural_check_flags_an_incomplete_shard_set(tmp_path):
    root = _checkpoint(
        tmp_path / "m",
        shards=["model-00001-of-00002.safetensors"],
        index=["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"],
    )
    assert "missing_shard:model-00002-of-00002.safetensors" in structural_problems(root)


def test_manifest_round_trip(tmp_path):
    root = _checkpoint(tmp_path / "m")
    manifest = build_manifest(root, model_id="morriszjm/Tacit-1.7B")
    assert manifest["file_count"] == 3
    assert manifest["hashed_count"] == 3
    assert manifest["model_id"] == "morriszjm/Tacit-1.7B"
    assert {entry["path"] for entry in manifest["files"]} == {
        "config.json",
        "tokenizer.json",
        "model-00001-of-00001.safetensors",
    }

    path = write_manifest(root, model_id="morriszjm/Tacit-1.7B")
    assert path.name == MANIFEST_NAME
    report = verify_model_dir(root)
    assert report.ok is True
    assert report.manifest_found is True
    assert report.files_checked == 3


def test_same_size_corruption_is_detected(tmp_path):
    root = _checkpoint(tmp_path / "m")
    write_manifest(root)
    (root / "model-00001-of-00001.safetensors").write_bytes(b"\x01" * 512)
    report = verify_model_dir(root)
    assert report.ok is False
    assert any(problem.startswith("sha256_mismatch:") for problem in report.problems)


def test_truncation_is_detected(tmp_path):
    root = _checkpoint(tmp_path / "m")
    write_manifest(root)
    (root / "model-00001-of-00001.safetensors").write_bytes(b"\x01" * 100)
    report = verify_model_dir(root)
    assert report.ok is False
    assert any(problem.startswith("size_mismatch:") for problem in report.problems)


def test_missing_file_is_detected(tmp_path):
    root = _checkpoint(tmp_path / "m")
    write_manifest(root)
    (root / "tokenizer.json").unlink()
    report = verify_model_dir(root)
    assert report.ok is False
    assert any(problem.startswith("missing_file:") for problem in report.problems)


def test_extra_file_is_detected_unless_allowed(tmp_path):
    root = _checkpoint(tmp_path / "m")
    write_manifest(root)
    (root / "notes.txt").write_text("scratch", encoding="utf-8")

    strict = verify_model_dir(root, extra_files_ok=False)
    assert strict.ok is False
    assert "unexpected_file:notes.txt" in strict.problems

    tolerant = verify_model_dir(root, extra_files_ok=True)
    assert tolerant.ok is True


def test_missing_manifest_fails_closed(tmp_path):
    root = _checkpoint(tmp_path / "m")
    report = verify_model_dir(root)
    assert report.ok is False
    assert report.manifest_found is False
    assert any(problem.startswith("manifest_missing:") for problem in report.problems)

    relaxed = verify_model_dir(root, require_manifest=False)
    assert relaxed.ok is True


def test_missing_directory_is_reported_not_raised(tmp_path):
    report = verify_model_dir(tmp_path / "absent")
    assert report.ok is False
    assert report.problems == ("model_dir_missing",)


def test_raise_for_status_carries_the_report(tmp_path):
    root = _checkpoint(tmp_path / "m")
    report = verify_model_dir(root)
    with pytest.raises(ModelIntegrityError) as excinfo:
        report.raise_for_status()
    assert excinfo.value.report is report


def test_manifest_schema_is_enforced(tmp_path):
    root = _checkpoint(tmp_path / "m")
    path = root / MANIFEST_NAME
    path.write_text(json.dumps({"schema": 99, "files": []}), encoding="utf-8")
    with pytest.raises(ModelIntegrityError, match="schema"):
        load_manifest(path)


def test_manifest_without_files_is_rejected(tmp_path):
    root = _checkpoint(tmp_path / "m")
    path = root / MANIFEST_NAME
    path.write_text(json.dumps({"schema": 1, "files": []}), encoding="utf-8")
    with pytest.raises(ModelIntegrityError, match="no files"):
        load_manifest(path)


def test_build_manifest_rejects_a_missing_directory(tmp_path):
    with pytest.raises(ModelIntegrityError):
        build_manifest(tmp_path / "absent")


def test_size_only_manifest_still_catches_truncation(tmp_path):
    root = _checkpoint(tmp_path / "m")
    write_manifest(root, include_hashes=False)
    payload = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert payload["hashed_count"] == 0
    (root / "model-00001-of-00001.safetensors").write_bytes(b"\x01" * 10)
    report = verify_model_dir(root)
    assert report.ok is False
    assert any(problem.startswith("size_mismatch:") for problem in report.problems)


def test_summary_is_log_safe(tmp_path):
    root = _checkpoint(tmp_path / "m")
    write_manifest(root)
    text = summarise(verify_model_dir(root))
    assert "ok" in text
    assert "files=3" in text
    # no file contents, no full path leakage beyond the directory itself
    assert "model_type" not in text


def test_sha256_matches_a_known_vector(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
