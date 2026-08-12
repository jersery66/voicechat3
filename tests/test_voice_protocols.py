"""Voice adapters use provider-neutral streaming contracts."""

from voice.audio_input import AudioInput
from voice.contracts import AudioFrame, Transcription
from voice.streaming_asr import StreamingASR
from voice.streaming_tts import StreamingTTS
from voice.vad import VoiceActivityDetector


class FakeInput:
    def read_frame(self):
        return AudioFrame(samples=b"pcm", sample_rate=16000)


class FakeVad:
    def is_speech(self, frame):
        return bool(frame.samples)


class FakeAsr:
    def transcribe(self, frame):
        return Transcription(text="partial", is_final=False)


class FakeTts:
    def stream(self, text):
        yield AudioFrame(samples=text.encode(), sample_rate=24000)


def test_voice_protocols_accept_simple_offline_adapters():
    frame = FakeInput().read_frame()

    assert isinstance(FakeInput(), AudioInput)
    assert isinstance(FakeVad(), VoiceActivityDetector)
    assert isinstance(FakeAsr(), StreamingASR)
    assert isinstance(FakeTts(), StreamingTTS)
    assert FakeVad().is_speech(frame) is True
    assert FakeAsr().transcribe(frame).is_final is False
