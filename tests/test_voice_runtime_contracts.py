"""Small real-time voice primitives remain independent of PySide and providers."""

from voice.interruption import InterruptionController
from voice.turn_detector import SilenceTurnDetector


def test_turn_detector_finalizes_only_after_configured_silence():
    detector = SilenceTurnDetector(silence_seconds=1.0)

    assert detector.feed(is_speech=True, timestamp=0.0) is False
    assert detector.feed(is_speech=False, timestamp=0.4) is False
    assert detector.feed(is_speech=False, timestamp=0.6) is False
    assert detector.feed(is_speech=False, timestamp=1.6) is True
    assert detector.feed(is_speech=False, timestamp=1.8) is False


def test_interruption_controller_stops_current_playback_once_per_user_barge_in():
    controller = InterruptionController()

    controller.mark_playback_started()
    assert controller.on_user_speech_started() is True
    assert controller.on_user_speech_started() is False
    controller.mark_playback_stopped()
    assert controller.on_user_speech_started() is False
