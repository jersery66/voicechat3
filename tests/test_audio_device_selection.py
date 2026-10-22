"""Input-device selection must never prefer a loopback endpoint.

On Windows machines whose default input device is unset, the stereo-mix
endpoint is often enumerated first and carries the audio vendor's name, so a
plain keyword scan records the speakers instead of the participant.
"""

from __future__ import annotations

from services.stt_service import select_input_device

REALTEK_WINDOWS = [
    {"name": "Microsoft 声音映射器 - Input", "max_input_channels": 2},
    {"name": "立体声混音 (Realtek HD Audio Stereo input)", "max_input_channels": 2},
    {"name": "线路输入 (Realtek HD Audio Line input)", "max_input_channels": 2},
    {"name": "麦克风 (Realtek HD Audio Mic input)", "max_input_channels": 2},
]


def test_microphone_wins_over_stereo_mix_even_when_enumerated_first():
    index, reason = select_input_device(REALTEK_WINDOWS, default_index=None)

    assert index == 3
    assert "麦克风" in reason


def test_stereo_mix_is_never_chosen_while_a_real_capture_endpoint_exists():
    for default_index in (None, 0, 1):
        index, _reason = select_input_device(REALTEK_WINDOWS, default_index=default_index)
        assert "立体声" not in REALTEK_WINDOWS[index]["name"]


def test_loopback_is_used_only_when_nothing_else_is_available():
    only_loopback = [
        {"name": "立体声混音 (Realtek HD Audio Stereo input)", "max_input_channels": 2},
    ]

    index, reason = select_input_device(only_loopback, default_index=None)

    assert index == 0
    assert "loopback" in reason


def test_system_default_is_used_when_no_device_is_named_like_a_microphone():
    generic = [
        {"name": "Audio Capture 1", "max_input_channels": 2},
        {"name": "Audio Capture 2", "max_input_channels": 2},
    ]

    index, reason = select_input_device(generic, default_index=1)

    assert index == 1
    assert "default" in reason


def test_output_only_devices_and_missing_default_are_tolerated():
    devices = [
        {"name": "扬声器 (Realtek HD Audio output)", "max_input_channels": 0},
        {"name": "麦克风 (USB Audio)", "max_input_channels": 1},
    ]

    index, reason = select_input_device(devices, default_index=-1)

    assert index == 1
    assert "microphone" in reason


def test_no_input_device_reports_none_instead_of_raising():
    assert select_input_device([], default_index=None) == (None, "no input device")
