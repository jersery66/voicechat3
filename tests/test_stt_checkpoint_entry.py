"""The FunASR entry point must load no matter what ``sys.modules`` already holds.

The checkpoint ships an entry point as ``model.py`` next to the weights, and the
service used to import it as ``from model import FunASRNano``. That is a bare
module name: if anything else in the process had already imported a module
called ``model``, Python returned the cached one, the import failed, and the
service fell back to ``funasr.AutoModel`` — which on the bundled Fun-ASR-Nano
checkpoint fails outright. On the user's machine the visible symptom was a record
button that did nothing.

These tests pin the path-based loader so a poisoned ``sys.modules`` cannot bring
the failure back.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.stt_service import load_checkpoint_entry  # noqa: E402


def _checkpoint(tmp_path: Path, body: str) -> Path:
    model_dir = tmp_path / "Fun-ASR-Nano-2512"
    model_dir.mkdir()
    (model_dir / "model.py").write_text(body, encoding="utf-8")
    return model_dir


def test_loads_the_class_from_the_checkpoint(tmp_path):
    model_dir = _checkpoint(tmp_path, "class FunASRNano:\n    pass\n")
    entry = load_checkpoint_entry(str(model_dir))
    assert entry is not None
    assert entry.__name__ == "FunASRNano"


def test_survives_a_poisoned_model_module(tmp_path, monkeypatch):
    """The regression: another module called `model` must not shadow the entry."""
    model_dir = _checkpoint(tmp_path, "class FunASRNano:\n    pass\n")
    decoy = types.ModuleType("model")          # no FunASRNano attribute
    monkeypatch.setitem(sys.modules, "model", decoy)

    entry = load_checkpoint_entry(str(model_dir))

    assert entry is not None, "裸名冲突时仍应按文件路径加载成功"
    assert entry.__name__ == "FunASRNano"
    # The decoy must be untouched: we never import `model` any more.
    assert sys.modules["model"] is decoy


def test_missing_entry_file_returns_none(tmp_path):
    empty_dir = tmp_path / "weights"
    empty_dir.mkdir()
    assert load_checkpoint_entry(str(empty_dir)) is None


def test_entry_without_the_class_returns_none(tmp_path):
    model_dir = _checkpoint(tmp_path, "VALUE = 1\n")
    assert load_checkpoint_entry(str(model_dir)) is None


def test_broken_entry_returns_none_instead_of_raising(tmp_path):
    model_dir = _checkpoint(tmp_path, "raise RuntimeError('bad checkpoint module')\n")
    assert load_checkpoint_entry(str(model_dir)) is None


def test_private_module_name_is_not_left_in_sys_modules(tmp_path):
    model_dir = _checkpoint(tmp_path, "class FunASRNano:\n    pass\n")
    load_checkpoint_entry(str(model_dir))
    # It may be registered under the private name, but never under `model`.
    assert sys.modules.get("model") is None or not hasattr(sys.modules["model"], "FunASRNano")
