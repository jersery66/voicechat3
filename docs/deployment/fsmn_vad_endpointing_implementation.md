# FSMN-VAD Endpointing Implementation

Status: implemented as the third deployment-hardening change.

## Scope and baseline

- Branch: `codex/a100-vllm-safety`
- Starting commit: `403374bbe318087a6a3b072183f04bc707d325a2`
- Change: `refactor: integrate fsmn vad for utterance endpointing`
- Production files: `services/fsmn_vad_adapter.py`, `services/stt_service.py`.
- Focused regression file: `tests/test_fsmn_vad.py`.

No Fun-ASR-Nano final recognizer, ASR dtype path, startup-failure contract,
authority layer, TTS, prompt, RAG, or model profile was changed.

## Verified FunASR API

The current development environment reports:

`funasr.__version__ == 1.3.1`

The installed package source was inspected before implementation.  Its
`AutoModel.generate(self, input, input_len=None, progress_callback=None,
**cfg)` forwards streaming `cache`, `is_final`, and `chunk_size` settings to
the registered `FsmnVADStreaming` model.  The installed name map resolves
`fsmn-vad` to the official FSMN VAD checkpoint, and the model implementation
returns the documented streaming segment forms:

- `[beg, -1]` — speech start;
- `[-1, end]` — speech end;
- `[beg, end]` — complete segment;
- `[]` — no boundary.

The adapter uses the verified constructor call
`AutoModel(model="fsmn-vad", device="cpu", disable_update=True)` (with the
device configurable for deployment) and the verified streaming arguments
`cache`, `is_final`, and `chunk_size=200`.  No material API discrepancy with
the installed FunASR package was identified.  No unsupported private API or
unverified argument was used.  The real
checkpoint was not downloaded or executed on this development machine, so
the hardware/model smoke status remains `NOT RUN / environment unavailable`.

## Runtime design

`services/fsmn_vad_adapter.py` owns the `AutoModel(model="fsmn-vad")` model,
streaming cache, event parsing, and speech state.  It defaults to CPU and
accepts `FSMN_VAD_DEVICE` for deployment configuration.  The adapter feeds
exactly 200 ms / 3200-sample mono-float chunks with one persistent cache.

`STTService` keeps the existing sounddevice callback lightweight: it only
copies accepted PCM into the recording-local queue.  The existing collector
appends every frame to `recorded_audio`, accumulates VAD chunks, and calls the
adapter outside the PortAudio callback.  A speech end is applied only to the
same current `_RecordingState` and then invokes the existing
`_request_recording_stop(..., vad_triggered=True)` sentinel/drain path.

Initial end events without a preceding speech start are ignored.  A complete
`[beg, end]` segment is treated deterministically as an endpoint.  Manual stop
does not wait for a VAD decision and continues to use the same lossless drain.
Each new recording resets the adapter cache.  A stale collector/VAD event is
discarded when its recording state is no longer current.

FSMN-VAD is attempted lazily when a loaded recognizer is about to record.  If
the optional model cannot load or later fails during inference, the service
logs the transition and uses the existing RMS detector as the sole
`RMS_FALLBACK` backend; both automatic endpoint owners never run together.

## Verification

- FunASR API/version inspection completed: `1.3.1`.
- FSMN-focused tests: `12 passed`.
- Startup-failure, shutdown, and voice protocol slice: `23 passed`.
- Full regression: `535 passed, 1 skipped, 0 failed`.
- The single skip remains the known local PySide6/QtWidgets DLL limitation.
- `git diff --check`: passed.
- Real microphone/FunASR GPU/A100 validation: `NOT RUN / environment unavailable`.

The next independent hardening item is the later full STT recording lifecycle
regression suite; hotwords, ASR AutoModel migration, and TTS hardening are not
included here.
