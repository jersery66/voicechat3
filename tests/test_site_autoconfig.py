"""Site installer detection rules.

The deployed archive contains two generations of layout in one tree: the offline
guide describes D:\\戒毒所\\voicechat3 with weights in D:\\戒毒所\\models, while the
older scripts/run_llm.bat points at D:\\models\\voicechat3\\llm and
D:\\models\\voicechat3\\asr. The installer has to find the weights under either, or
a technician has to hand-edit config on a machine nobody can reach.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, rel: str):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


autoconfig = _module("site_autoconfig_under_test", "scripts/launch/site_autoconfig.py")
launcher = _module("site_launch_under_test", "scripts/launch/site_launch.py")


def _weights(base: Path) -> Path:
    target = base / "Fun-ASR-Nano-2512"
    target.mkdir(parents=True)
    (target / "model.pt").write_bytes(b"x")
    return target


def test_finds_weights_beside_the_program(tmp_path):
    """D:\\戒毒所\\voicechat3 的上一级是 D:\\戒毒所，权重在 D:\\戒毒所\\models。"""
    models = tmp_path / "models"
    expected = _weights(models)
    assert autoconfig.find_dir(models, "Fun-ASR-Nano-2512", "model.pt") == expected


def test_finds_weights_two_levels_down(tmp_path):
    """D:\\models\\voicechat3\\asr\\Fun-ASR-Nano-2512 —— 早期脚本那套目录。"""
    nested = tmp_path / "voicechat3" / "asr"
    nested.mkdir(parents=True)
    expected = _weights(nested)
    assert autoconfig.find_dir(tmp_path, "Fun-ASR-Nano-2512", "model.pt") == expected


def test_ignores_a_directory_without_the_marker(tmp_path):
    (tmp_path / "Fun-ASR-Nano-2512").mkdir()          # 空壳，没有 model.pt
    assert autoconfig.find_dir(tmp_path, "Fun-ASR-Nano-2512", "model.pt") is None


def test_voice_prompt_resolves_to_the_file_not_the_folder(monkeypatch, tmp_path):
    """Config expects the .mp3 itself; handing it a directory breaks TTS."""
    prompt_dir = tmp_path / "voice_prompt"
    prompt_dir.mkdir()
    (prompt_dir / "s1.mp3").write_bytes(b"id3")
    vox = tmp_path / "VoxCPM2"
    vox.mkdir()
    (vox / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(autoconfig, "candidate_model_roots", lambda: [tmp_path])
    found = autoconfig.detect_models()

    assert found["voxcpm"] == str(vox)
    assert found["voice_prompt"] == str(prompt_dir / "s1.mp3")


def test_user_override_parses_set_lines(monkeypatch, tmp_path):
    config = tmp_path / "site-config.cmd"
    config.write_text(
        "@echo off\r\nREM set VOICECHAT_INPUT_DEVICE=1\r\n"
        'set "VOICECHAT_INPUT_DEVICE=28"\r\nset QT_SCALE_FACTOR=0.85\r\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "USER_CONFIG", config)
    values = launcher.load_user_config()
    assert values == {"VOICECHAT_INPUT_DEVICE": "28", "QT_SCALE_FACTOR": "0.85"}


def test_user_override_ignores_non_ascii_values(monkeypatch, tmp_path, capsys):
    """Batch is read in the console code page; a CJK value would be mangled."""
    config = tmp_path / "site-config.cmd"
    config.write_text('set "VOICECHAT_INPUT_DEVICE=麦克风"\r\n', encoding="utf-8")
    monkeypatch.setattr(launcher, "USER_CONFIG", config)
    assert launcher.load_user_config() == {}
    assert "含中文" in capsys.readouterr().out


def test_generated_env_is_read_back(monkeypatch, tmp_path):
    env_file = tmp_path / "site-env.json"
    env_file.write_text(
        '{"env": {"FUNASR_MODEL_PATH": "D:\\\\戒毒所\\\\models\\\\Fun-ASR-Nano-2512"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "ENV_FILE", env_file)
    values = launcher.load_generated()
    assert values["FUNASR_MODEL_PATH"].endswith("Fun-ASR-Nano-2512")


def test_missing_env_file_is_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "ENV_FILE", tmp_path / "absent.json")
    assert launcher.load_generated() == {}
