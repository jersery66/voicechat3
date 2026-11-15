"""Startup must honor runtime-config-owned strict preflight policy."""

import sys
import types
from dataclasses import replace

import pytest

import main as main_module
from deployment import runtime_config as rc
from deployment.runtime_config import load_runtime_config


class _FakeApplication:
    created = False
    exec_called = False

    @classmethod
    def setHighDpiScaleFactorRoundingPolicy(cls, _policy):
        return None

    def __init__(self, _argv):
        type(self).created = True

    def setFont(self, _font):
        return None

    def exec(self):
        type(self).exec_called = True
        return 0


class _FakeMainWindow:
    created = False

    def __init__(self):
        type(self).created = True

    def showMaximized(self):
        return None


def _install_startup_fakes(monkeypatch):
    _FakeApplication.created = False
    _FakeApplication.exec_called = False
    _FakeMainWindow.created = False
    monkeypatch.setattr(main_module, "QApplication", _FakeApplication)
    monkeypatch.setattr(main_module, "MainWindow", _FakeMainWindow)
    monkeypatch.setattr(main_module, "QFont", lambda *_args: object())
    monkeypatch.setattr(main_module, "setup_logging", lambda: None)
    monkeypatch.setattr(
        main_module, "_initialize_error_monitor", lambda: None, raising=False
    )
    fake_config = types.ModuleType("config")
    fake_config.DIALOGUE_PROVIDER = "openai_compatible"
    fake_config.DIALOGUE_BASE_URL = "http://127.0.0.1:8000/v1"
    fake_config.DIALOGUE_MODEL = "test-dialogue"
    fake_config.print_model_status = lambda: None
    monkeypatch.setitem(sys.modules, "config", fake_config)


def _runtime_with_strict_preflight(strict: bool):
    base = load_runtime_config()
    return replace(base, runtime=replace(base.runtime, strict_preflight=strict))


def _use_runtime(monkeypatch, config):
    monkeypatch.setattr(rc, "get_runtime_config", lambda: config)


def test_strict_runtime_false_preflight_blocks_ui(monkeypatch):
    _install_startup_fakes(monkeypatch)
    _use_runtime(monkeypatch, _runtime_with_strict_preflight(True))
    monkeypatch.setattr("scripts.check_config.run_check", lambda: False)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeApplication.exec_called is False
    assert _FakeMainWindow.created is False


def test_strict_runtime_exception_during_preflight_blocks_ui(monkeypatch):
    _install_startup_fakes(monkeypatch)
    _use_runtime(monkeypatch, _runtime_with_strict_preflight(True))

    def raise_preflight():
        raise RuntimeError("dialogue probe crashed")

    monkeypatch.setattr("scripts.check_config.run_check", raise_preflight)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeMainWindow.created is False


def test_strict_runtime_success_continues_to_ui(monkeypatch):
    _install_startup_fakes(monkeypatch)
    _use_runtime(monkeypatch, _runtime_with_strict_preflight(True))
    monkeypatch.setattr("scripts.check_config.run_check", lambda: True)

    assert main_module.main() == 0
    assert _FakeApplication.created is True
    assert _FakeApplication.exec_called is True
    assert _FakeMainWindow.created is True


def test_lenient_runtime_false_preflight_warns_and_continues(monkeypatch):
    _install_startup_fakes(monkeypatch)
    _use_runtime(monkeypatch, _runtime_with_strict_preflight(False))
    monkeypatch.setattr("scripts.check_config.run_check", lambda: False)

    assert main_module.main() == 0
    assert _FakeApplication.created is True
    assert _FakeMainWindow.created is True


def test_lenient_runtime_preflight_exception_warns_and_continues(monkeypatch):
    _install_startup_fakes(monkeypatch)
    _use_runtime(monkeypatch, _runtime_with_strict_preflight(False))

    def raise_preflight():
        raise RuntimeError("local service unavailable")

    monkeypatch.setattr("scripts.check_config.run_check", raise_preflight)

    assert main_module.main() == 0
    assert _FakeApplication.created is True
    assert _FakeMainWindow.created is True


def test_unloadable_runtime_config_blocks_startup(monkeypatch):
    _install_startup_fakes(monkeypatch)

    def raise_config():
        raise rc.RuntimeConfigError("runtime config file not found")

    monkeypatch.setattr(rc, "get_runtime_config", raise_config)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeMainWindow.created is False


def test_strictness_is_runtime_owned_not_model_owned(monkeypatch):
    """Any strict runtime config blocks startup, regardless of model names."""
    _install_startup_fakes(monkeypatch)
    base = load_runtime_config()
    renamed = replace(
        base,
        dialogue=replace(base.dialogue, model="some-other-dialogue-model"),
        runtime=replace(base.runtime, strict_preflight=True),
    )
    _use_runtime(monkeypatch, renamed)
    monkeypatch.setattr("scripts.check_config.run_check", lambda: False)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeMainWindow.created is False
