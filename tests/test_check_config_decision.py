"""The decision-layer preflight never blocks a launch; it only reports."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import check_config  # noqa: E402


def _decision(**overrides):
    base = dict(
        mode="legacy",
        backend="legacy_qwen",
        transport="server",
        endpoint=None,
        timeout=2.0,
        model=None,
        model_path=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _install(monkeypatch, decision):
    monkeypatch.setattr(
        check_config, "get_runtime_config", lambda: SimpleNamespace(decision=decision)
    )


def test_legacy_mode_is_reported_as_ok(monkeypatch, capsys):
    _install(monkeypatch, _decision())
    assert check_config.check_decision_backend() is True
    assert "[OK]" in capsys.readouterr().out


def test_missing_endpoint_is_a_warning_not_a_failure(monkeypatch, capsys):
    _install(monkeypatch, _decision(mode="jev", backend="anyjev_decider", endpoint=None))
    assert check_config.check_decision_backend() is True
    output = capsys.readouterr().out
    assert "[WARN]" in output
    assert "fall back" in output


def test_unreachable_service_is_a_warning(monkeypatch, capsys):
    _install(
        monkeypatch,
        _decision(mode="jev", backend="anyjev_decider", endpoint="http://127.0.0.1:1"),
    )
    assert check_config.check_decision_backend() is True
    assert "[WARN]" in capsys.readouterr().out


def test_inprocess_without_a_model_path_warns(monkeypatch, capsys):
    _install(monkeypatch, _decision(mode="jev", backend="tacit", transport="inprocess"))
    assert check_config.check_decision_backend() is True
    assert "[WARN]" in capsys.readouterr().out


def test_inprocess_with_a_missing_checkpoint_warns(monkeypatch, capsys, tmp_path):
    _install(
        monkeypatch,
        _decision(
            mode="jev",
            backend="tacit",
            transport="inprocess",
            model_path=str(tmp_path / "absent"),
        ),
    )
    assert check_config.check_decision_backend() is True
    assert "[WARN]" in capsys.readouterr().out


def test_inprocess_with_a_verified_checkpoint_is_ok(monkeypatch, capsys, tmp_path):
    from decision.integrity import write_manifest

    root = tmp_path / "Tacit-1.7B"
    root.mkdir()
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "tokenizer.json").write_text("{}", encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"\x00" * 32)
    write_manifest(root)

    _install(
        monkeypatch,
        _decision(mode="jev", backend="tacit", transport="inprocess", model_path=str(root)),
    )
    assert check_config.check_decision_backend() is True
    output = capsys.readouterr().out
    assert "[OK]" in output
    assert "model integrity ok" in output


def test_fake_backend_is_flagged_as_not_production(monkeypatch, capsys):
    _install(monkeypatch, _decision(mode="jev", backend="fake"))
    assert check_config.check_decision_backend() is True
    assert "tests only" in capsys.readouterr().out
