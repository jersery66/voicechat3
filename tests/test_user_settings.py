"""Settings that a technician edits on site: audio devices, interface scale.

The point of these tests is that a hand-edited file must never stop the app from
starting: a typo should degrade to the default for that key, be reported, and
leave the rest of the file alone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.user_settings import (  # noqa: E402
    DEFAULTS, UserSettings, load_settings, settings_path,
)


@pytest.fixture()
def store(tmp_path):
    return UserSettings(tmp_path / "user_settings.json")


def test_first_run_writes_a_template_to_edit(store):
    settings = store.load()
    assert settings.path.is_file(), "首次运行应生成可手改的模板文件"
    parsed = json.loads(settings.path.read_text(encoding="utf-8"))
    assert parsed["audio"]["input_device"] == "auto"
    assert parsed["_readme"], "模板里要写清怎么改"


def test_defaults_are_used_when_the_file_is_absent(tmp_path):
    settings = UserSettings(tmp_path / "missing.json").load()
    assert settings.scale == 1.0
    assert settings.input_device == "auto"
    assert settings.output_device == "default"


def test_broken_json_falls_back_without_raising(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ this is not json", encoding="utf-8")
    settings = store.load()
    assert settings.scale == 1.0
    assert settings.warnings and "无法解析" in settings.warnings[0]


def test_hand_edited_scale_is_clamped(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"ui": {"scale": 9.5}}), encoding="utf-8")
    settings = store.load()
    assert settings.scale == 1.6
    assert any("超出" in w for w in settings.warnings)


def test_non_numeric_scale_falls_back(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"ui": {"scale": "big"}}), encoding="utf-8")
    settings = store.load()
    assert settings.scale == 1.0


def test_device_can_be_an_index_or_a_name(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"audio": {"input_device": 7,
                                                "output_device": "USB Headset"}}),
                          encoding="utf-8")
    settings = store.load()
    assert settings.input_device == "7"
    assert settings.output_device == "USB Headset"


def test_unknown_keys_survive_a_save(store):
    store.load()
    settings = store.data
    settings["future_option"] = {"keep": True}
    assert store.save()
    reread = json.loads(store.path.read_text(encoding="utf-8"))
    assert reread["future_option"] == {"keep": True}


def test_save_is_atomic(store):
    store.load()
    store.set("ui.scale", 0.85)
    assert store.save()
    assert not store.path.with_suffix(".json.tmp").exists()
    assert json.loads(store.path.read_text(encoding="utf-8"))["ui"]["scale"] == 0.85


def test_raw_edit_reports_syntax_errors_and_keeps_old_values(store):
    store.load()
    store.set("ui.scale", 0.9)
    ok, message = store.replace_raw('{"ui": {"scale": 1.1}')
    assert ok is False and "语法" in message
    assert store.scale == 0.9


def test_raw_edit_applies_valid_json(store):
    store.load()
    ok, message = store.replace_raw('{"ui": {"scale": 1.15}, "audio": {"input_device": 3}}')
    assert ok is True, message
    assert store.scale == 1.15
    assert store.input_device == "3"


def test_reset_restores_defaults(store):
    store.load()
    store.set("ui.scale", 1.4)
    store.reset()
    assert store.scale == DEFAULTS["ui"]["scale"]


def test_env_var_can_point_elsewhere(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("VOICECHAT_USER_SETTINGS", str(target))
    assert settings_path() == target
    settings = load_settings()
    assert settings.path == target


def test_scale_property_is_a_float(store):
    store.load()
    store.set("ui.scale", "1.10")
    settings, _ = __import__("services.user_settings", fromlist=["_coerce"])._coerce(store.data)
    assert isinstance(settings["ui"]["scale"], float)
