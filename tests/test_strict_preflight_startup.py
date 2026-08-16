"""Startup must honor profile-owned strict preflight policy."""

import sys
import types
from dataclasses import replace

import pytest

import main as main_module
from deployment.profiles import get_deployment_profile


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
    fake_config.DIALOGUE_BACKEND = "vllm"
    fake_config.DIALOGUE_BASE_URL = "http://127.0.0.1:8000/v1"
    fake_config.OLLAMA_MODEL = "test-dialogue"
    fake_config.print_model_status = lambda: None
    monkeypatch.setitem(sys.modules, "config", fake_config)


@pytest.mark.parametrize(
    "profile_name",
    (
        "rtxpro6000_96g",
        "rtxpro6000_96g_qwen38_candidate",
        "a100_80g",
    ),
)
def test_strict_profile_false_preflight_blocks_ui(profile_name, monkeypatch):
    _install_startup_fakes(monkeypatch)
    monkeypatch.setattr(
        "deployment.profiles.get_deployment_profile",
        lambda: get_deployment_profile(profile_name),
    )
    monkeypatch.setattr("scripts.check_config.run_check", lambda: False)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeApplication.exec_called is False
    assert _FakeMainWindow.created is False


def test_strict_profile_exception_during_preflight_blocks_ui(monkeypatch):
    _install_startup_fakes(monkeypatch)
    monkeypatch.setattr(
        "deployment.profiles.get_deployment_profile",
        lambda: get_deployment_profile("rtxpro6000_96g"),
    )

    def raise_preflight():
        raise RuntimeError("dialogue probe crashed")

    monkeypatch.setattr("scripts.check_config.run_check", raise_preflight)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeMainWindow.created is False


def test_strict_profile_success_continues_to_ui(monkeypatch):
    _install_startup_fakes(monkeypatch)
    monkeypatch.setattr(
        "deployment.profiles.get_deployment_profile",
        lambda: get_deployment_profile("rtxpro6000_96g"),
    )
    monkeypatch.setattr("scripts.check_config.run_check", lambda: True)

    assert main_module.main() == 0
    assert _FakeApplication.created is True
    assert _FakeApplication.exec_called is True
    assert _FakeMainWindow.created is True


@pytest.mark.parametrize("profile_name", ("dev_6g", "dev_vllm_6g"))
def test_development_profile_false_preflight_warns_and_continues(profile_name, monkeypatch):
    _install_startup_fakes(monkeypatch)
    monkeypatch.setattr(
        "deployment.profiles.get_deployment_profile",
        lambda: get_deployment_profile(profile_name),
    )
    monkeypatch.setattr("scripts.check_config.run_check", lambda: False)

    assert main_module.main() == 0
    assert _FakeApplication.created is True
    assert _FakeMainWindow.created is True


def test_development_profile_preflight_exception_warns_and_continues(monkeypatch):
    _install_startup_fakes(monkeypatch)
    monkeypatch.setattr(
        "deployment.profiles.get_deployment_profile",
        lambda: get_deployment_profile("dev_6g"),
    )

    def raise_preflight():
        raise RuntimeError("local service unavailable")

    monkeypatch.setattr("scripts.check_config.run_check", raise_preflight)

    assert main_module.main() == 0
    assert _FakeApplication.created is True
    assert _FakeMainWindow.created is True


def test_strictness_is_profile_owned_not_name_owned(monkeypatch):
    _install_startup_fakes(monkeypatch)
    strict_profile = replace(
        get_deployment_profile("rtxpro6000_96g"),
        name="future_blackwell_profile",
    )
    monkeypatch.setattr(
        "deployment.profiles.get_deployment_profile",
        lambda: strict_profile,
    )
    monkeypatch.setattr("scripts.check_config.run_check", lambda: False)

    assert main_module.main() == 2
    assert _FakeApplication.created is False
    assert _FakeMainWindow.created is False
