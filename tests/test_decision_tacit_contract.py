"""Contract tests for the Tacit integration.

These exist because the decision layer is easy to get *almost* right: a
checkpoint can load, the probabilities can sum to one, and the answer can still
be produced by a readout the model was never trained for.  The tests here pin
the parts that make the difference verifiable:

* a Tacit checkpoint is recognised, and the project's own readout refuses it;
* the official provider calls ``decide_batch`` once per turn (Tacit's own
  primitive), not five hand-rolled ``decide`` calls;
* offline is enforced before the checkpoint is touched, not after;
* a manifest can record that its hashes came from the source.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from decision.contracts import DecisionRequest
from decision.integrity import (
    build_manifest,
    tacit_checkpoint_markers,
    verify_model_dir,
)
from decision.providers.base import DECISION_QUESTIONS, DecisionBackendUnavailable
from decision.providers.tacit import TacitDecisionProvider
from decision.service.engine import DecisionEngine, EngineUnavailable


def _request() -> DecisionRequest:
    return DecisionRequest(
        user_text="我最近总是睡不好",
        session_state="interview",
        turn_index=3,
        active_scale=None,
        game_active=False,
        recent_history="来访者：我不知道该说什么",
    )


def _checkpoint(root: Path, *, tacit: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"model_type": "qwen3"}), encoding="utf-8")
    (root / "tokenizer.json").write_text("{}", encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"\x00" * 512)
    if tacit:
        (root / "tacit_decision.py").write_text("# official reference impl\n", encoding="utf-8")
        (root / "README.md").write_text(
            "# Tacit\n\nSelf-distilled with anyjev.\n", encoding="utf-8"
        )
    return root


class RecordingTacit:
    """Official `Tacit` double that records how it was called."""

    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def decide_batch(self, items):
        self.batches.append(list(items))
        out = []
        for item in items:
            options = list(item["options"])
            probs = {option: 0.0 for option in options}
            probs[options[0]] = 1.0
            out.append(
                {"answer": options[0], "probs": probs, "route": "one_forward", "margin": 9.0}
            )
        return out


# ---------------------------------------------------------------------------
# checkpoint recognition
# ---------------------------------------------------------------------------


def test_markers_find_a_tacit_checkpoint(tmp_path):
    root = _checkpoint(tmp_path / "Tacit-1.7B")
    markers = tacit_checkpoint_markers(root)
    assert "tacit_decision.py" in markers
    assert any(marker.startswith("directory_name:") for marker in markers)
    assert "model_card:anyjev-tacit" in markers


def test_markers_stay_quiet_for_a_plain_causal_lm(tmp_path):
    root = _checkpoint(tmp_path / "Qwen3-4B", tacit=False)
    # the directory name avoids "tacit" and there is no shipped reference impl
    assert tacit_checkpoint_markers(root) == []


def test_service_refuses_to_serve_a_tacit_checkpoint_with_its_own_readout(tmp_path):
    root = _checkpoint(tmp_path / "Tacit-1.7B")
    engine = DecisionEngine(model_path=str(root), require_manifest=False)
    with pytest.raises(EngineUnavailable) as excinfo:
        engine.load()
    message = str(excinfo.value)
    assert "Tacit checkpoint" in message
    assert "NOT Tacit's letter readout" in message


def test_service_can_serve_it_only_when_the_operator_opts_in(tmp_path, monkeypatch):
    root = _checkpoint(tmp_path / "Tacit-1.7B")
    engine = DecisionEngine(
        model_path=str(root), require_manifest=False, refuse_tacit_checkpoint=False
    )
    markers = tacit_checkpoint_markers(root)
    assert markers  # detection still happens, the refusal is the only thing lifted
    engine._tacit_markers = markers
    health = engine.health()
    assert health["readout_semantics"].startswith("option_label_tokens")
    assert "not official Tacit" in health["readout_semantics"]


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------


def test_official_provider_uses_one_batch_per_turn(tmp_path, monkeypatch):
    root = _checkpoint(tmp_path / "Tacit-1.7B")
    fake = RecordingTacit()
    provider = TacitDecisionProvider(
        transport="inprocess", model=str(root), tacit_factory=lambda _provider: fake
    )
    decision = provider.decide(_request())

    assert len(fake.batches) == 1, "official Tacit batches the questions of one turn"
    assert len(fake.batches[0]) == len(DECISION_QUESTIONS)
    assert {item["kind"] for item in fake.batches[0]} == {"choice"}
    assert decision.route == "one_forward"
    assert decision.action == DECISION_QUESTIONS[2].options[0]
    assert abs(sum(decision.probabilities.values()) - 1.0) < 1e-9


def test_official_provider_sets_offline_before_loading(tmp_path, monkeypatch):
    root = _checkpoint(tmp_path / "Tacit-1.7B")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    provider = TacitDecisionProvider(transport="inprocess", model=str(root))

    # anyjev may or may not be installed in this interpreter; either way the
    # offline environment must already be set by the time loading starts.
    try:
        provider._ensure_tacit()
    except DecisionBackendUnavailable:
        pass
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"


def test_official_provider_refuses_a_repo_id_without_download_permission():
    provider = TacitDecisionProvider(
        transport="inprocess", model="morriszjm/Tacit-1.7B", allow_model_download=False
    )
    with pytest.raises(DecisionBackendUnavailable) as excinfo:
        provider._ensure_tacit()
    assert "not a local directory" in str(excinfo.value)


def test_official_provider_reports_an_engine_failure_as_unavailable(tmp_path):
    root = _checkpoint(tmp_path / "Tacit-1.7B")

    class Broken:
        def decide_batch(self, _items):
            raise RuntimeError("CUDA out of memory")

    provider = TacitDecisionProvider(
        transport="inprocess", model=str(root), tacit_factory=lambda _provider: Broken()
    )
    with pytest.raises(DecisionBackendUnavailable) as excinfo:
        provider.decide(_request())
    assert "CUDA out of memory" in str(excinfo.value)


# ---------------------------------------------------------------------------
# manifest provenance
# ---------------------------------------------------------------------------


def test_manifest_can_record_that_a_hash_came_from_the_source(tmp_path):
    root = _checkpoint(tmp_path / "Tacit-1.7B", tacit=False)
    manifest = build_manifest(
        root,
        model_id="morriszjm/Tacit-1.7B",
        source="hub:morriszjm/Tacit-1.7B@main",
        hash_sources={"model.safetensors": "hub_lfs_oid"},
        extra={"hub_revision": "main"},
    )
    by_path = {entry["path"]: entry for entry in manifest["files"]}
    assert by_path["model.safetensors"]["sha256_source"] == "hub_lfs_oid"
    assert "sha256_source" not in by_path["config.json"]  # local hash, not claimed otherwise
    assert manifest["source"].endswith("@main")
    assert manifest["hub_revision"] == "main"


def test_source_manifest_still_verifies_and_still_catches_tampering(tmp_path):
    root = _checkpoint(tmp_path / "Tacit-1.7B", tacit=False)
    manifest = build_manifest(root, source="hub:x@main")
    (root / "tacit_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert verify_model_dir(root, require_manifest=True, extra_files_ok=True).ok

    target = root / "model.safetensors"
    target.write_bytes(b"\x01" * 512)  # same size, different bytes
    report = verify_model_dir(root, require_manifest=True, extra_files_ok=True)
    assert not report.ok
    assert any(problem.startswith("sha256_mismatch:") for problem in report.problems)
