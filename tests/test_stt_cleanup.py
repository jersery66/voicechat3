"""STT resource-release regression tests."""

import numpy as np

from services import stt_service as stt_module
from services.stt_service import STTService

# Entry point written beside the fake weights. Kept as a triple-quoted literal
# so the file content is exactly what a checkpoint would ship.
_STUB_ENTRY = '''class _Parameter:
    def __init__(self, dtype):
        self.dtype = dtype


class MixedModel:
    def __init__(self):
        self.float_called = False
        self.eval_called = False

    def parameters(self):
        return [_Parameter("float32"), _Parameter("bfloat16")]

    def float(self):
        self.float_called = True
        return self

    def eval(self):
        self.eval_called = True
        return self


_MIXED = MixedModel()


class FunASRNano:
    @staticmethod
    def from_pretrained(**_kwargs):
        return _MIXED, {}
'''


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
    """Mixed bf16/fp32 checkpoint must be normalized before inference.

    The stub entry point is written as a real ``model.py`` beside the weights
    instead of being injected into ``sys.modules``: the service loads it by file
    path exactly as it does for a real checkpoint, so a cached module named
    ``model`` can neither satisfy nor hijack that import.
    """
    import sys

    (tmp_path / "model.py").write_text(_STUB_ENTRY, encoding="utf-8")
    # A decoy under the bare name must be irrelevant now.
    monkeypatch.setitem(sys.modules, "model", type(sys)("model"))
    monkeypatch.setattr("services.stt_service.torch.nn.Module.to", lambda module, *_a, **_k: module)

    service = STTService(model_path=str(tmp_path), device="cpu")
    service.load_model()

    assert service.model is not None
    assert service.model.float_called is True
    assert service.model.eval_called is True
    assert service.model_kwargs["language"] == "zh"


def test_stop_recording_consumes_completed_audio_once(monkeypatch):
    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.closed = False

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            self.closed = True

    stream_holder = {}

    monkeypatch.setattr(
        stt_module.sd,
        "query_devices",
        lambda kind=None: [{"name": "Test Microphone", "max_input_channels": 1}],
    )
    monkeypatch.setattr(
        stt_module.sd,
        "InputStream",
        lambda **kwargs: stream_holder.setdefault("stream", Stream(**kwargs)),
    )

    service = STTService()
    service.set_vad_enabled(False)
    assert service.start_recording() is True
    frame = np.ones((1024, 1), dtype=np.float32)
    stream_holder["stream"].callback(frame, 1024, None, None)

    first = service.stop_recording()
    second = service.stop_recording()
    service.cleanup()

    assert len(first) == 1024
    assert second.size == 0
    assert stream_holder["stream"].closed is True
