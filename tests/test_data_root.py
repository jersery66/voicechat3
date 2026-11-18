"""Where session data goes, and what happens when the install folder is read-only.

A deployment under Program Files only fails at the first save — in the middle of
an interview. These tests pin the fallback so it happens (and is visible) at
startup instead.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def test_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICECHAT_DATA_DIR", str(tmp_path))
    assert config._resolve_data_root() == str(tmp_path)


def test_relative_override_resolves_against_app_root(monkeypatch):
    monkeypatch.setenv("VOICECHAT_DATA_DIR", "sessions")
    resolved = config._resolve_data_root()
    assert os.path.isabs(resolved)
    assert resolved.startswith(config.APP_ROOT)


def test_writable_probe_is_honest(tmp_path):
    assert config._is_writable(str(tmp_path)) is True
    # Nothing is left behind.
    assert not (tmp_path / ".vc_write_probe").exists()


def test_writable_probe_rejects_an_impossible_path():
    assert config._is_writable(r"Q:\does\not\exist\anywhere") is False


def test_portable_dir_is_preferred_when_writable(monkeypatch, tmp_path):
    portable = tmp_path / "runtime" / "data"
    monkeypatch.setattr(config, "PROGRAM_ROOT", str(tmp_path))
    monkeypatch.delenv("VOICECHAT_DATA_DIR", raising=False)
    monkeypatch.setattr(config.os.path, "isdir", lambda p: False)
    with monkeypatch.context() as patch:
        patch.setattr(config, "_is_writable", lambda p: True)
        assert config._resolve_data_root() == str(portable)


def test_read_only_install_falls_back_to_local_appdata(monkeypatch, tmp_path):
    local = tmp_path / "localappdata"
    local.mkdir()
    monkeypatch.setattr(config, "PROGRAM_ROOT", str(tmp_path))
    monkeypatch.delenv("VOICECHAT_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(config.os.path, "isdir", lambda p: False)
    # Program dir refuses writes, the user profile accepts them.
    monkeypatch.setattr(config, "_is_writable",
                        lambda p: str(p).startswith(str(local)))
    assert config._resolve_data_root() == str(local / "VoiceChat" / "data")


def test_everything_read_only_still_returns_an_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROGRAM_ROOT", str(tmp_path))
    monkeypatch.delenv("VOICECHAT_DATA_DIR", raising=False)
    monkeypatch.setattr(config.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(config, "_is_writable", lambda p: False)
    # Must not raise: the caller reports the failure, startup still happens.
    assert config._resolve_data_root()
