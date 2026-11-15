# Video Play Tool - Wraps video_service for relaxation video playback

import os


class VideoPlayTool:
    """Play fullscreen relaxation videos (breathing/muscle/meditation)."""

    name = "play_relaxation_video"
    description = "Play a fullscreen relaxation video"

    FILE_MAP = {
        "breathing": "呼吸训练.mp4",
        "muscle": "肌肉放松.mp4",
        "meditation": "冥想训练.mp4",
    }

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def _resolve_video_path(self, filename: str) -> str:
        """Resolve both legacy root media and the documented media library."""
        candidates = (
            os.path.join(self.base_dir, filename),
            os.path.join(self.base_dir, "media_library", "relaxation", filename),
        )
        return next((path for path in candidates if os.path.isfile(path)), "")

    def execute(self, relaxation_type: str = None, filename: str = None) -> str:
        """
        Play a relaxation video. Blocks until video finishes or is stopped.

        Args:
            relaxation_type: One of 'breathing', 'muscle', 'meditation'
            filename: Direct filename (overrides relaxation_type)

        Returns:
            The Chinese relaxation name when the clip played to its end, an
            empty string when the participant stopped it early, and ``None``
            when it could not be played at all.  The three cases are kept
            apart on purpose: an abandoned exercise is neither a failure nor
            a completed intervention.
        """
        if filename is None:
            filename = self.FILE_MAP.get(relaxation_type)
        if not filename:
            return None

        from services.video_service import get_video_player
        video_path = self._resolve_video_path(filename)
        if not video_path:
            print(
                "[WARNING] Video file not found in either media location: "
                f"{filename}"
            )
            return None

        player = get_video_player()
        # ``play_video`` returns True when the clip ran to its end and False
        # when it was interrupted.  Any other return value (a stub, an older
        # player) keeps the legacy "it played" reading.
        if player.play_video(video_path) is False:
            return ""
        return filename.replace(".mp4", "")
