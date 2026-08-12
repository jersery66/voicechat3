"""STT resource-release regression tests."""

from services.stt_service import STTService


def test_cleanup_stops_audio_releases_model_and_clears_cuda_cache(monkeypatch):
    class Stream:
        def __init__(self):
            self.stopped = False
            self.closed = False

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    service = STTService()
    stream = Stream()
    service.stream = stream
    service.is_recording = True
    service.model = object()
    service.model_kwargs = {"language": "zh"}
    cache_cleared = []
    monkeypatch.setattr("services.stt_service.torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("services.stt_service.torch.cuda.empty_cache", lambda: cache_cleared.append(True))

    service.cleanup()

    assert service.is_recording is False
    assert stream.stopped and stream.closed
    assert service.stream is None
    assert service.model is None
    assert service.model_kwargs == {}
    assert cache_cleared == [True]
