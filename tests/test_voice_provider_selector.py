"""Config-driven voice provider selection (the ASR/TTS plugin seam).

The application layer never imports a concrete FunASR / VoxCPM2 /
CosyVoice implementation directly: runtime.yaml names the provider
(asr.provider / tts.provider) and the selectors map that name to the
implementation module. Unknown providers fail loudly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import services.tts_service as tts_selector
from voice.providers import SpeechInputProvider, SpeechOutputProvider


ROOT = Path(__file__).resolve().parents[1]


def test_tts_selector_maps_every_configured_provider():
    assert tts_selector.TTS_PROVIDERS == ("cosyvoice", "voxcpm")
    assert tts_selector._provider_module("voxcpm").__name__ == "services.tts_service_voxcpm"
    assert tts_selector._provider_module("cosyvoice").__name__ == "services.tts_service_cosyvoice"


def test_tts_selector_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown TTS provider"):
        tts_selector._provider_module("typo")


def test_tts_selector_follows_the_configured_provider(monkeypatch):
    monkeypatch.setattr("config.TTS_PROVIDER", "cosyvoice")
    assert tts_selector.build_tts_service().__class__.__module__ == "services.tts_service_cosyvoice"

    monkeypatch.setattr("config.TTS_PROVIDER", "voxcpm")
    assert tts_selector.build_tts_service().__class__.__module__ == "services.tts_service_voxcpm"


def test_tts_selector_shares_the_provider_singleton(monkeypatch):
    monkeypatch.setattr("config.TTS_PROVIDER", "voxcpm")
    import services.tts_service_voxcpm as voxcpm_module

    monkeypatch.setattr(voxcpm_module, "_tts_service", None)
    first = tts_selector.get_tts_service()
    assert tts_selector.get_tts_service() is first


def test_tts_selector_import_does_not_pull_provider_modules():
    """Importing the selector must stay dependency-light.

    The heavy provider modules (torch, model code) load only when a
    provider is actually selected, in a separate process so this test
    cannot pollute the module cache of the rest of the suite.
    """
    code = (
        "import sys\n"
        "import services.tts_service as selector\n"
        "loaded = [name for name in sys.modules if name.startswith('services.tts_service_')]\n"
        "assert loaded == [], loaded\n"
        "assert selector.TTS_PROVIDERS\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr


def test_stt_selector_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr("config.ASR_PROVIDER", "typo")
    from services.stt_service import get_stt_service

    with pytest.raises(ValueError, match="unknown ASR provider"):
        get_stt_service()


def test_stt_selector_builds_singleton_for_configured_provider(monkeypatch):
    monkeypatch.setattr("config.ASR_PROVIDER", "funasr")
    from services import stt_service

    monkeypatch.setattr(stt_service, "_stt_service", None)
    first = stt_service.get_stt_service()
    assert stt_service.get_stt_service() is first


def test_speech_providers_satisfy_the_unified_contracts():
    from services.stt_service import STTService
    from services.tts_service_cosyvoice import TTSService as CosyTTSService
    from services.tts_service_voxcpm import TTSService as VoxTTSService

    input_methods = (
        "load_model", "warmup", "start_recording", "stop_recording",
        "transcribe", "is_vad_triggered", "cancel",
    )
    output_methods = (
        "load_model", "warmup", "generate_and_play", "generate",
        "stop_playing", "cancel",
    )
    for method in input_methods:
        assert callable(getattr(STTService, method)), f"STTService.{method}"
    for cls in (VoxTTSService, CosyTTSService):
        for method in output_methods:
            assert callable(getattr(cls, method)), f"{cls.__name__}.{method}"

    assert isinstance(STTService(), SpeechInputProvider)
    assert isinstance(VoxTTSService(), SpeechOutputProvider)
    assert isinstance(CosyTTSService(), SpeechOutputProvider)
