"""Prevent unsupported GPUs from entering VoxCPM's native crash path."""

from services.tts_service_voxcpm import TTSService


def test_voxcpm_preflight_rejects_a_6gb_cuda_gpu(monkeypatch):
    class Properties:
        total_memory = 6 * 1024**3

    monkeypatch.setattr("services.tts_service_voxcpm.torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("services.tts_service_voxcpm.torch.cuda.get_device_properties", lambda index: Properties())

    reason = TTSService.get_load_blocker()

    assert reason is not None
    assert "6GB" in reason


def test_load_model_stops_before_importing_native_voxcpm(monkeypatch):
    service = TTSService()
    monkeypatch.setattr(service, "get_load_blocker", lambda: "unsupported test GPU")

    import pytest

    with pytest.raises(RuntimeError, match="unsupported test GPU"):
        service.load_model()


def test_cleanup_releases_model_resources(monkeypatch):
    service = TTSService()
    stopped = []
    unloaded = []
    monkeypatch.setattr("services.tts_service_voxcpm.sd.stop", lambda: stopped.append(True))
    monkeypatch.setattr(service, "unload_model", lambda: unloaded.append(True))

    service.cleanup()

    assert stopped == [True]
    assert unloaded == [True]
