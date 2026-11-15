"""Input-device selection must never prefer a loopback endpoint.

On Windows machines whose default input device is unset, the stereo-mix
endpoint is often enumerated first and carries the audio vendor's name, so a
plain keyword scan records the speakers instead of the participant.
"""

from __future__ import annotations

from services.stt_service import order_input_candidates, select_input_device

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


def test_a_microphone_is_opened_before_the_virtual_cable():
    """Naming the right device is not enough: it must also be opened first.

    With VB-Cable installed, WASAPI exposes "CABLE Output", while this machine's
    Realtek microphone only appears under WDM-KS.  Ranking by backend alone put
    the cable first, so the recording captured the system's own playback even
    though the microphone had already been selected.
    """
    devices = [
        {"name": "CABLE Output (VB-Audio Virtual Cable)", "max_input_channels": 2},
        {"name": "麦克风 (Realtek HD Audio Mic input)", "max_input_channels": 2},
    ]

    order = order_input_candidates(devices, preferred=1)

    assert order[0] == 1, "the microphone must be tried before the cable"
    assert order == [1, 0]


def test_loopback_survives_as_the_only_candidate():
    devices = [
        {"name": "立体声混音 (Realtek HD Audio Stereo input)", "max_input_channels": 2},
        {"name": "扬声器 (Realtek HD Audio output)", "max_input_channels": 0},
    ]

    assert order_input_candidates(devices, preferred=0) == [0]


# --- playback side -------------------------------------------------------
# The same machine also exposes VB-Cable as the *default playback* device, so
# the avatar's voice went into the virtual cable instead of the loudspeaker.

CABLE_DEFAULT_WINDOWS = [
    {"name": "Microsoft Sound Mapper - Output", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "CABLE Input (VB-Audio Virtual Cable)", "max_input_channels": 0, "max_output_channels": 16},
    {"name": "主声音驱动程序", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Headphones (Realtek HD Audio 2nd output)", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Speakers (Realtek HD Audio output)", "max_input_channels": 0, "max_output_channels": 8},
]


def test_playback_skips_the_virtual_cable_that_windows_made_default():
    from services.tts_service_voxcpm import resolve_output_device

    index, reason = resolve_output_device(
        CABLE_DEFAULT_WINDOWS, default_index=1, samplerate=None
    )

    assert "CABLE" not in CABLE_DEFAULT_WINDOWS[index]["name"]
    assert "Headphones" in CABLE_DEFAULT_WINDOWS[index]["name"]
    assert "real output" in reason


def test_playback_honours_a_real_system_default():
    from services.tts_service_voxcpm import resolve_output_device

    index, reason = resolve_output_device(
        CABLE_DEFAULT_WINDOWS, default_index=4, samplerate=None
    )

    assert index == 4
    assert "system default" in reason


def test_playback_falls_back_to_the_system_default_when_everything_is_virtual():
    from services.tts_service_voxcpm import resolve_output_device

    virtual_only = [CABLE_DEFAULT_WINDOWS[1], CABLE_DEFAULT_WINDOWS[2]]
    index, reason = resolve_output_device(virtual_only, default_index=0, samplerate=None)

    assert index == 0
    assert "only virtual" in reason


def test_playback_device_can_be_forced_by_index_or_name(monkeypatch):
    from services.tts_service_voxcpm import resolve_output_device

    monkeypatch.setenv("VOICECHAT_OUTPUT_DEVICE", "4")
    assert resolve_output_device(
        CABLE_DEFAULT_WINDOWS, default_index=1, samplerate=None
    )[0] == 4

    monkeypatch.setenv("VOICECHAT_OUTPUT_DEVICE", "speakers")
    index, reason = resolve_output_device(
        CABLE_DEFAULT_WINDOWS, default_index=1, samplerate=None
    )
    assert index == 4
    assert "VOICECHAT_OUTPUT_DEVICE" in reason


def test_playback_reports_none_when_no_output_endpoint_exists():
    from services.tts_service_voxcpm import resolve_output_device

    index, reason = resolve_output_device([], default_index=None, samplerate=None)

    assert index is None
    assert reason == "no output device"


def test_input_device_can_be_forced_to_a_loopback_endpoint(monkeypatch):
    """A machine without a physical microphone runs the TTS->CABLE->ASR
    self-test by naming the cable explicitly; the automatic rule must not
    override that."""
    from services.stt_service import order_input_candidates, select_input_device

    devices = [
        {"name": "麦克风 (Realtek HD Audio Mic input)", "max_input_channels": 2},
        {"name": "CABLE Output (VB-Audio Virtual Cable)", "max_input_channels": 2},
        {"name": "立体声混音 (Realtek HD Audio Stereo input)", "max_input_channels": 2},
    ]

    monkeypatch.setenv("VOICECHAT_INPUT_DEVICE", "cable output")
    index, reason = select_input_device(devices, default_index=0)
    assert index == 1
    assert "VOICECHAT_INPUT_DEVICE" in reason
    assert order_input_candidates(devices, preferred=1)[0] == 1

    monkeypatch.setenv("VOICECHAT_INPUT_DEVICE", "1")
    assert select_input_device(devices, default_index=0)[0] == 1


def test_an_unmatched_input_override_falls_back_to_the_automatic_rule(monkeypatch):
    from services.stt_service import select_input_device

    devices = [{"name": "麦克风 (Realtek HD Audio Mic input)", "max_input_channels": 2}]

    monkeypatch.setenv("VOICECHAT_INPUT_DEVICE", "nonexistent device")
    index, reason = select_input_device(devices, default_index=None)
    assert index == 0
    assert "microphone" in reason
