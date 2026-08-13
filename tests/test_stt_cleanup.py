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


def test_load_model_normalizes_a_mixed_dtype_checkpoint(monkeypatch, tmp_path):
    import sys
    import types

    class Parameter:
        def __init__(self, dtype):
            self.dtype = dtype

    class MixedModel:
        def __init__(self):
            self.float_called = False
            self.eval_called = False

        def parameters(self):
            return [Parameter("float32"), Parameter("bfloat16")]

        def float(self):
            self.float_called = True
            return self

        def eval(self):
            self.eval_called = True
            return self

    mixed = MixedModel()

    class FunASRNano:
        @staticmethod
        def from_pretrained(**_kwargs):
            return mixed, {}

    monkeypatch.setitem(sys.modules, "model", types.SimpleNamespace(FunASRNano=FunASRNano))
    monkeypatch.setattr("services.stt_service.torch.nn.Module.to", lambda module, *_a, **_k: module)

    service = STTService(model_path=str(tmp_path), device="cpu")
    service.load_model()

    assert mixed.float_called is True
    assert mixed.eval_called is True
    assert service.model_kwargs["language"] == "zh"
