"""Regression tests for explicit persistence and media failure signals."""

from data.data_manager import DataManager
from services.tools.video_tool import VideoPlayTool


def test_session_report_storage_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    dm = DataManager(data_root=str(tmp_path))
    dm.set_user_id("report-failure")
    dm.start_new_session()

    monkeypatch.setattr(dm, "_write_json", lambda *args, **kwargs: False)
    monkeypatch.setattr(dm, "_write_text", lambda *args, **kwargs: False,
                        raising=False)

    result = dm.save_session_report({"summary": "test"}, "feedback", "QUIT")

    assert result["ok"] is False
    assert result["errors"]


def test_video_tool_missing_media_returns_failure(tmp_path):
    tool = VideoPlayTool(str(tmp_path))
    assert tool.execute(relaxation_type="breathing") is None


def test_video_tool_resolves_documented_media_library_location(tmp_path, monkeypatch):
    media_file = tmp_path / "media_library" / "relaxation" / "呼吸训练.mp4"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"placeholder")
    tool = VideoPlayTool(str(tmp_path))

    class Player:
        def __init__(self):
            self.played = None

        def play_video(self, path):
            self.played = path

    player = Player()
    monkeypatch.setattr("services.video_service.get_video_player", lambda: player)

    assert tool.execute(relaxation_type="breathing") == "呼吸训练"
    assert player.played == str(media_file)
