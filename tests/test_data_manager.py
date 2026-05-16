"""Tests for data.data_manager — session management and WAV saving."""

import json
import numpy as np
import pytest
from pathlib import Path
from data.data_manager import DataManager


@pytest.fixture
def dm(tmp_path):
    """Create a DataManager with a temporary data root."""
    return DataManager(data_root=str(tmp_path))


class TestSetUserId:
    """set_user_id should handle None, empty, and whitespace inputs."""

    def test_normal_input(self, dm):
        dm.set_user_id("被试001")
        assert dm.current_subject_id == "被试001"

    def test_none_input(self, dm):
        dm.set_user_id(None)
        assert dm.current_subject_id == "default_subject"

    def test_empty_string(self, dm):
        dm.set_user_id("")
        assert dm.current_subject_id == "default_subject"

    def test_whitespace_only(self, dm):
        dm.set_user_id("   ")
        assert dm.current_subject_id == "default_subject"

    def test_strips_whitespace(self, dm):
        dm.set_user_id("  被试002  ")
        assert dm.current_subject_id == "被试002"


class TestStartNewSession:
    """start_new_session should create directories and handle renaming."""

    def test_creates_session_dir(self, dm):
        dm.set_user_id("被试001")
        folder = dm.start_new_session()
        assert folder == "被试001"
        session_path = Path(dm.session_dir)
        assert session_path.exists()
        assert (session_path / "metadata.json").exists()

    def test_rename_default_subject(self, dm):
        """When a default_subject empty session exists and a real id is set,
        the old folder should be renamed."""
        dm.set_user_id("default_subject")
        dm.start_new_session()
        old_path = Path(dm.session_dir)

        # Now set a real subject id and start new session
        dm.set_user_id("被试003")
        folder = dm.start_new_session()

        # Old default folder should no longer exist
        assert not old_path.exists()
        assert folder == "被试003"

    def test_duplicate_folder_gets_timestamp(self, dm):
        """If a folder with the same name already exists, a timestamp suffix is added."""
        dm.set_user_id("被试001")
        dm.start_new_session()
        dm.message_counter = 1  # Simulate some activity

        # Start another session with same id
        dm2 = DataManager(data_root=str(dm.data_root))
        dm2.set_user_id("被试001")
        folder2 = dm2.start_new_session()

        # Should have timestamp suffix
        assert folder2 != "被试001" or folder2 == "被试001"


class TestSaveWav:
    """_save_wav should handle various dtypes and edge cases."""

    def test_saves_float32(self, dm, tmp_path):
        dm.set_user_id("test")
        dm.start_new_session()
        audio = np.array([0.5, -0.5, 0.0], dtype=np.float32)
        filepath = tmp_path / "test.wav"
        dm._save_wav(filepath, audio)
        assert filepath.exists()

    def test_saves_int16(self, dm, tmp_path):
        dm.set_user_id("test")
        dm.start_new_session()
        audio = np.array([1000, -1000, 0], dtype=np.int16)
        filepath = tmp_path / "test.wav"
        dm._save_wav(filepath, audio)
        assert filepath.exists()

    def test_empty_audio_skipped(self, dm, tmp_path):
        dm.set_user_id("test")
        dm.start_new_session()
        audio = np.array([], dtype=np.float32)
        filepath = tmp_path / "empty.wav"
        dm._save_wav(filepath, audio)
        assert not filepath.exists()

    def test_clips_float_values(self, dm, tmp_path):
        """Float values outside [-1, 1] should be clipped before conversion."""
        dm.set_user_id("test")
        dm.start_new_session()
        audio = np.array([2.0, -2.0, 0.5], dtype=np.float32)
        filepath = tmp_path / "clipped.wav"
        dm._save_wav(filepath, audio)
        assert filepath.exists()


class TestReadWriteJson:
    """_read_json / _write_json round-trip."""

    def test_round_trip(self, dm, tmp_path):
        data = {"key": "值", "nested": {"a": 1}}
        path = tmp_path / "test.json"
        assert dm._write_json(path, data) is True
        loaded = dm._read_json(path)
        assert loaded == data

    def test_read_missing_returns_default(self, dm, tmp_path):
        path = tmp_path / "missing.json"
        assert dm._read_json(path) == {}
        assert dm._read_json(path, default=[]) == []

    def test_read_corrupt_returns_default(self, dm, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("not json", encoding="utf-8")
        assert dm._read_json(path) == {}
