"""Tests for the local deployment path: detection, provisioning, autostart.

These cover the logic that decides what a machine is missing and what to do
about it. They never start a model server, never download anything, and never
require the speech weights to be present — the point is to keep the decision
rules honest so that a fresh device behaves predictably.

The end-to-end path (launcher -> services -> GUI) is covered separately by
running the launcher on a clean checkout.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deployment import autostart, dependencies  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """Restore process-wide state that the code under test writes directly.

    ``ensure()`` exports ``VOICECHAT_RUNTIME_CONFIG`` with ``os.environ[...] =``,
    which monkeypatch cannot undo. Without this, a later test module would load a
    temporary config path that has already been deleted.
    """
    saved = dict(os.environ)
    autostart._self_configured = False
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
        autostart._self_configured = False


# --------------------------------------------------------------- manifest
def test_manifest_entries_are_complete():
    for dep in dependencies.MANIFEST:
        assert dep.module, dep
        assert dep.spec, dep
        assert dep.tier in {"core", "voice", "tts"}, dep
        assert dep.size_mb >= 0, dep
        # A module name that is a package path (a.b) cannot be detected by
        # find_spec on the parent, so keep them flat.
        assert "." not in dep.module, dep


def test_manifest_has_no_duplicate_modules():
    modules = [d.module for d in dependencies.MANIFEST]
    assert len(modules) == len(set(modules))


def test_spec_names_the_import_where_they_differ():
    # PyYAML ships `yaml`, pillow ships `PIL`; a manifest that maps these wrong
    # would report a missing package forever.
    by_module = {d.module: d.spec for d in dependencies.MANIFEST}
    assert by_module["yaml"].lower().startswith("pyyaml")
    assert by_module["PIL"].lower().startswith("pillow")


def test_missing_respects_tier_and_presence(monkeypatch):
    monkeypatch.setattr(dependencies, "present", lambda name: False)
    core = dependencies.missing("core")
    assert core and all(d.tier == "core" for d in core)

    monkeypatch.setattr(dependencies, "present", lambda name: True)
    assert dependencies.missing("core") == []
    assert dependencies.missing("all") == []


# ------------------------------------------------------------ gpu channel
def test_torch_channel_follows_cuda_version():
    assert dependencies.torch_channels({"cuda": "13.4"})[0] == "cu130"
    assert dependencies.torch_channels({"cuda": "12.8"})[0] == "cu128"
    assert dependencies.torch_channels({"cuda": "12.6"})[0] == "cu126"
    assert dependencies.torch_channels({"cuda": "12.4"})[0] == "cu124"
    assert dependencies.torch_channels({"cuda": "12.1"})[0] == "cu121"


def test_unknown_cuda_version_prefers_a_modern_channel():
    # A driver whose version string we cannot parse is far more likely to be
    # recent than to be a decade old.
    channels = dependencies.torch_channels({"name": "some gpu"})
    assert channels[0] == "cu128"
    assert channels[-1] == "cpu"


def test_no_gpu_means_cpu_build():
    assert dependencies.torch_channels({}) == ["cpu"]


def test_cpu_is_always_the_last_resort():
    for gpu in ({}, {"cuda": "13.4"}, {"cuda": "11.8"}):
        assert dependencies.torch_channels(gpu)[-1] == "cpu"


# -------------------------------------------------------------- mirrors
def test_pick_mirror_falls_back_when_everything_is_unreachable(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("no network")

    monkeypatch.setattr(dependencies, "_opener", lambda: type("O", (), {"open": boom})())
    assert dependencies.pick_mirror() == dependencies.MIRRORS[-1][1]


def test_pick_mirror_uses_first_reachable(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = []

    class Opener:
        def open(self, url, timeout=None):
            calls.append(url)
            return Response()

    monkeypatch.setattr(dependencies, "_opener", lambda: Opener())
    assert dependencies.pick_mirror() == dependencies.MIRRORS[0][1]
    assert calls == [dependencies.MIRRORS[0][1] + "/"]


# ------------------------------------------------------------- autostart
def test_explicit_config_is_never_repointed(monkeypatch, tmp_path):
    pinned = tmp_path / "pinned.yaml"
    pinned.write_text("dialogue: {}\n", encoding="utf-8")
    monkeypatch.setenv(autostart.ENV_RUNTIME_CONFIG, str(pinned))
    monkeypatch.setattr(autostart, "_self_configured", False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("autostart must not probe when a config is pinned")

    monkeypatch.setattr(autostart, "run_autoconfigure", forbidden)
    assert autostart.ensure(verbose=False) == str(pinned)


def test_repeated_ensure_still_heals_services(monkeypatch, tmp_path):
    """The second call must not mistake our own export for a pinned contract.

    Symptom this guards against: services stop mid-session, ensure() is called
    again, sees VOICECHAT_RUNTIME_CONFIG (which it set itself) and returns
    without restarting them.
    """
    local = tmp_path / "runtime.local.yaml"
    local.write_text("dialogue: {}\n", encoding="utf-8")
    monkeypatch.setattr(autostart, "LOCAL_CONFIG", local)
    monkeypatch.setattr(autostart, "_self_configured", False)
    monkeypatch.delenv(autostart.ENV_RUNTIME_CONFIG, raising=False)

    probes: list[int] = []

    def fake_autoconfigure(*, write=True, timeout=90.0):
        probes.append(1)
        return {"chosen": {"base_url": "http://127.0.0.1:8000/v1", "is_stub": False},
                "dialogue_model": "m", "agent": None, "voice": {}}

    monkeypatch.setattr(autostart, "run_autoconfigure", fake_autoconfigure)
    monkeypatch.setattr(autostart, "missing_weights", lambda state: [])

    assert autostart.ensure(verbose=False) == str(local)
    assert autostart.ensure(verbose=False) == str(local)
    assert len(probes) == 2, "第二次 ensure 应当重新探测，而不是直接返回"


def test_pinned_config_still_wins_after_self_configuration(monkeypatch, tmp_path):
    """A config pinned by someone else is respected even if we configured once."""
    pinned = tmp_path / "operator.yaml"
    pinned.write_text("dialogue: {}\n", encoding="utf-8")
    monkeypatch.setattr(autostart, "LOCAL_CONFIG", tmp_path / "runtime.local.yaml")
    monkeypatch.setattr(autostart, "_self_configured", True)
    monkeypatch.setenv(autostart.ENV_RUNTIME_CONFIG, str(pinned))
    monkeypatch.setattr(autostart, "run_autoconfigure",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应探测")))
    assert autostart.ensure(verbose=False) == str(pinned)


def test_autostart_can_be_disabled(monkeypatch):
    monkeypatch.setenv(autostart.ENV_NO_AUTOSTART, "1")
    assert autostart.ensure(verbose=False) is None


def test_autostart_survives_a_missing_probe_script(monkeypatch):
    monkeypatch.delenv(autostart.ENV_RUNTIME_CONFIG, raising=False)
    monkeypatch.setattr(autostart, "AUTOCONFIGURE_SCRIPT", Path("no/such/file.py"))
    assert autostart.run_autoconfigure() == {}
    assert autostart.ensure(verbose=False) is None


def test_status_report_is_ascii_and_shaped(monkeypatch):
    monkeypatch.setattr(autostart, "probe_models",
                        lambda host, port, timeout=1.5: ["m"] if port == 8000 else None)
    monkeypatch.setattr(autostart, "find_wsl_distro", lambda: "Ubuntu-24.04")
    report = autostart.status_report()

    assert report["dialogue"]["ready"] is True
    assert report["agent"]["ready"] is False
    assert report["ready"] is False          # both are required
    assert report["wsl_distro"] == "Ubuntu-24.04"
    # Launchers parse this; non-ASCII would be dropped by a console whose code
    # page differs from the child's.
    import json
    assert json.dumps(report, ensure_ascii=True).isascii()


def test_missing_weights_detects_each_layer():
    assert autostart.missing_weights({"voice": {"funasr": "x", "voxcpm": "y"}}) == []
    assert autostart.missing_weights({"voice": {"funasr": "x"}}) == ["voxcpm"]
    assert autostart.missing_weights({}) == ["funasr", "voxcpm"]


def test_weight_sources_point_somewhere_usable():
    for kind, spec in autostart.WEIGHT_SOURCES.items():
        assert "/" in spec["repo"], kind
        assert spec["target"], kind
        assert spec["size_gb"] > 0, kind


def test_download_without_modelscope_is_reported_not_raised(monkeypatch):
    monkeypatch.setitem(sys.modules, "modelscope", None)
    assert autostart.download_weights(["voxcpm"], verbose=False) == []


def test_probe_models_returns_none_when_nothing_listens():
    # Port 1 is reserved and never served; this must not raise.
    assert autostart.probe_models("127.0.0.1", 1, timeout=0.3) is None


# ---------------------------------------------------------- lazy package
def test_deployment_package_does_not_import_yaml_eagerly():
    """A fresh machine has no PyYAML; the provisioning modules must still import.

    Verified in a child interpreter on purpose: purging ``sys.modules`` here
    would hand later test modules a different ``deployment.runtime_config``
    object than the one ``main`` imports, so their monkeypatching would miss.
    """
    code = (
        "import sys, importlib.abc\n"
        "class _Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'yaml':\n"
        "            raise ModuleNotFoundError('yaml is not installed on this machine')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "try:\n"
        "    import yaml\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('blocker did not work')\n"
        "import deployment, deployment.dependencies, deployment.autostart\n"
        "assert 'deployment.runtime_config' not in sys.modules, 'eager import'\n"
        "print('ok')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


# ------------------------------------------------------------- rendering
def test_generated_config_quotes_windows_paths_with_chinese():
    from scripts.launch import autoconfigure as ac

    state = {
        "chosen": {"base_url": "http://127.0.0.1:8000/v1", "label": "x",
                   "models": ["q"], "host": "127.0.0.1", "port": 8000,
                   "is_stub": False},
        "dialogue_model": "q",
        "agent": {"base_url": "http://127.0.0.1:8001/v1", "model": "a"},
        "voice": {"funasr": r"D:\戒毒所\models\Fun-ASR-Nano-2512",
                  "voxcpm": r"D:\戒毒所\models\VoxCPM2",
                  "voice_prompt": r"D:\voicechat3\data\s1.flac"},
    }
    text = ac.render_yaml(state)
    # Single quotes: YAML double quotes would interpret the backslashes.
    assert r"'D:\戒毒所\models\VoxCPM2'" in text
    assert "strict_preflight: true" in text

    import yaml

    parsed = yaml.safe_load(text)
    assert parsed["tts"]["model_path"] == r"D:\戒毒所\models\VoxCPM2"


def test_stub_config_is_not_strict():
    from scripts.launch import autoconfigure as ac

    state = {
        "chosen": {"base_url": "http://127.0.0.1:8010/v1", "label": "offline stub",
                   "models": ["voicechat-offline-stub"], "host": "127.0.0.1",
                   "port": 8010, "is_stub": True},
        "dialogue_model": "voicechat-offline-stub",
        "agent": None,
        "voice": {"funasr": None, "voxcpm": None, "voice_prompt": None},
    }
    text = ac.render_yaml(state)
    assert "strict_preflight: false" in text
    assert "enabled: false" in text          # agent stays off without a real server
