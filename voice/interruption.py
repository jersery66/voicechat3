"""Playback barge-in state independent of a concrete audio player."""


class InterruptionController:
    """Returns whether a caller must stop playback for a new user utterance."""

    def __init__(self) -> None:
        self._playing = False
        self._interrupted = False

    def mark_playback_started(self) -> None:
        self._playing = True
        self._interrupted = False

    def mark_playback_stopped(self) -> None:
        self._playing = False
        self._interrupted = False

    def on_user_speech_started(self) -> bool:
        if not self._playing or self._interrupted:
            return False
        self._interrupted = True
        return True
