# STT Service - FunASR Integration

import os
import re
import sys
import gc
from dataclasses import dataclass, field

# Monkey-patch torchaudio.load to use soundfile/miniaudio (avoids torchcodec/ffmpeg dependency).
# Must happen before funasr imports torchaudio.
def _torchaudio_load_soundfile(filepath, **kwargs):
    import torch as _torch
    import numpy as _np
    try:
        import soundfile as _sf
        data, sr = _sf.read(filepath, dtype='float32')
        if data.ndim == 1:
            data = data[:, _np.newaxis]
        tensor = _torch.from_numpy(data).T  # (channels, samples)
        return tensor, sr
    except Exception:
        pass
    # Fallback: miniaudio handles flac/mp3/ogg without ffmpeg
    import miniaudio
    decoded = miniaudio.decode_file(str(filepath), output_format=miniaudio.SampleFormat.FLOAT32)
    audio = _np.array(decoded.samples, dtype=_np.float32)
    if decoded.nchannels > 1:
        audio = audio.reshape(-1, decoded.nchannels)
    else:
        audio = audio[:, _np.newaxis]
    return _torch.from_numpy(audio).T, decoded.sample_rate

try:
    import torchaudio
    torchaudio.load = _torchaudio_load_soundfile
except ImportError:
    pass

import queue
import threading
import numpy as np
import sounddevice as sd
try:
    import torch
except ImportError:  # lightweight install: text chat works, STT stays disabled
    torch = None
import tempfile
import soundfile as sf

try:  # optional: only used when a capture device refuses 16 kHz
    import soxr as _soxr
except ImportError:  # pragma: no cover - depends on the runtime environment
    _soxr = None

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    FUNASR_MODEL_PATH, SAMPLE_RATE, CHANNELS,
    USE_VAD_AUTO_STOP, VAD_SILENCE_THRESHOLD,
    VAD_SILENCE_DURATION, VAD_SPEECH_MIN_DURATION,
    VAD_MODEL_PATH,
)
from services.fsmn_vad_adapter import FSMNVADAdapter, VadEvent
from services.logger import get_logger, text_meta

logger = get_logger(__name__)


_RECORDING_SENTINEL = object()


# Device names that capture the sound card's own output rather than a room
# microphone.  They must never win over a real capture endpoint: on a Windows
# machine whose default input device is unset, "立体声混音" (Stereo Mix) is
# often the only device carrying the audio vendor's name, so a plain keyword
# scan records the speakers instead of the participant.
_LOOPBACK_DEVICE_MARKERS = (
    "stereo mix", "立体声混音", "what u hear", "loopback", "wave out mix",
    "cable output", "virtual", "vb-audio", "线路输入", "line input",
)
_MICROPHONE_PATTERN = re.compile(
    r"(microphone|\bmic\b|麦克风|话筒|headset|\barray\b)", re.IGNORECASE
)
_VENDOR_MARKERS = ("realtek", "nvidia", "amd", "intel", "usb audio")
_INPUT_DEVICE_ENV = "VOICECHAT_INPUT_DEVICE"


def _match_input_by_spec(devices, spec: str):
    """Resolve ``VOICECHAT_INPUT_DEVICE`` given a device index or name fragment."""
    spec = str(spec).strip()
    if not spec:
        return None
    if spec.isdigit():
        index = int(spec)
        if 0 <= index < len(devices) and devices[index].get("max_input_channels", 0) > 0:
            return index
        return None
    low = spec.lower()
    for index, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        if low in str(device.get("name", "")).lower():
            return index
    return None


def select_input_device(devices, *, default_index=None):
    """Return ``(index, reason)`` for the most plausible microphone.

    Order: a real microphone name, then a vendor capture endpoint, then the
    system default, then any remaining capture endpoint.  Loopback devices are
    only used when nothing else exists, because they record playback rather
    than the participant's voice.

    ``VOICECHAT_INPUT_DEVICE`` overrides the choice by index or name fragment.
    That is how a machine with no physical microphone runs the loopback
    self-test (TTS -> CABLE -> ASR) without the automatic rule refusing the
    virtual endpoint.
    """
    forced = os.environ.get(_INPUT_DEVICE_ENV, "").strip()
    if forced:
        matched = _match_input_by_spec(devices, forced)
        if matched is not None:
            return matched, f"{_INPUT_DEVICE_ENV}={forced}"

    inputs = [
        (index, device)
        for index, device in enumerate(devices)
        if device.get("max_input_channels", 0) > 0
    ]

    def name_of(device):
        return str(device.get("name", "")).lower()

    def is_loopback(name):
        return any(marker in name for marker in _LOOPBACK_DEVICE_MARKERS)

    capture = [(index, device) for index, device in inputs if not is_loopback(name_of(device))]
    loopback = [(index, device) for index, device in inputs if is_loopback(name_of(device))]

    for index, device in capture:
        if _MICROPHONE_PATTERN.search(name_of(device)):
            return index, f"microphone: {device.get('name')}"
    for index, device in capture:
        if any(marker in name_of(device) for marker in _VENDOR_MARKERS):
            return index, f"vendor capture: {device.get('name')}"

    if default_index is not None and default_index >= 0:
        return default_index, f"system default input (index {default_index})"

    for index, device in capture:
        return index, f"first capture input: {device.get('name')}"
    for index, device in loopback:
        return index, f"loopback input: {device.get('name')}"
    return None, "no input device"


class RecordingStartError(RuntimeError):
    """Raised when a microphone recording cannot be started."""


def load_checkpoint_entry(model_path: str):
    """Import ``FunASRNano`` from the checkpoint's ``model.py`` by file path.

    The historical ``from model import FunASRNano`` is a bare module name: once
    anything else in the process has imported a module called ``model``, the
    checkpoint's entry point is silently skipped (inserting ``model_path`` into
    ``sys.path`` cannot displace an entry that already exists in
    ``sys.modules``). The service then falls back to ``funasr.AutoModel``, which
    on the bundled Fun-ASR-Nano checkpoint fails with
    ``'NoneType' object has no attribute 'from_pretrained'`` — and the user sees
    a record button that does nothing.

    Loading through ``spec_from_file_location`` with a private module name makes
    the lookup independent of whatever else is in ``sys.modules``.

    Returns the class, or ``None`` when the checkpoint has no usable entry.
    """
    entry = os.path.join(str(model_path), "model.py")
    if not os.path.isfile(entry):
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_vc_local_funasr_entry", entry)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any failure means "no entry point"
        logger.warning("Loading %s failed (%s); falling back to funasr.AutoModel", entry, exc)
        return None
    return getattr(module, "FunASRNano", None)




# Capture is requested in 64 ms blocks because that is what 1024 frames at
# 16 kHz already was; keeping the period constant means the VAD chunker and the
# silence timers see the same cadence whichever native rate the device runs at.
_CAPTURE_BLOCK_MS = 64
_CAPTURE_RATE_ENV = "VOICECHAT_CAPTURE_RATE"
_CAPTURE_RATE_FALLBACKS = (SAMPLE_RATE, 48000, 44100)


class _CaptureResampler:
    """Block resampler from a device's native rate down to ``SAMPLE_RATE``.

    Stateful on purpose.  Calling ``resample_poly`` on every 64 ms block
    restarts the FIR filter at each boundary, which leaves an audible click
    roughly fifteen times per second in the saved utterance and feeds the VAD
    a discontinuous signal.  ``soxr.ResampleStream`` carries its filter state
    across blocks, so the 16 kHz stream is continuous end to end.

    When the device already runs at 16 kHz this object is a passthrough and no
    resampling dependency is touched.
    """

    def __init__(self, in_rate: int) -> None:
        self.in_rate = int(in_rate)
        self.enabled = self.in_rate != SAMPLE_RATE
        self._stream = None
        if not self.enabled:
            return
        if _soxr is None:
            raise RuntimeError(
                f"input device runs at {self.in_rate} Hz and the 'soxr' package "
                f"is missing, so it cannot be resampled to {SAMPLE_RATE} Hz"
            )
        self._stream = _soxr.ResampleStream(
            self.in_rate,
            SAMPLE_RATE,
            CHANNELS,
            dtype="float32",
            quality="HQ",
        )

    @property
    def blocksize(self) -> int:
        """Frames per callback that keep a constant 64 ms capture period."""
        return max(1, int(round(self.in_rate * _CAPTURE_BLOCK_MS / 1000)))

    def process(self, block, *, last: bool = False):
        """Return the block at ``SAMPLE_RATE``, or ``None`` when it yields nothing."""
        if not self.enabled:
            return block
        out = self._stream.resample_chunk(
            np.ascontiguousarray(block, dtype=np.float32),
            last=last,
        )
        return out if out.size else None


def _hostapi_rank(devices, device_index: int) -> int:
    """Rank a capture backend, lowest first.

    PortAudio's WDM-KS path is the one backend that can open a device and then
    deliver no frames at all (seen on Windows 10 + Realtek: callbacks never
    fire, and a blocking read returns uninitialised memory).  Because "the
    stream opened" is not proof of capture, the backends that go through the
    Windows audio engine are tried first and WDM-KS is demoted to last resort.
    Unknown shapes stay in the middle so nothing is ever dropped.
    """
    try:
        api_index = int(devices[device_index].get("hostapi"))
        name = str(sd.query_hostapis(api_index).get("name", "")).lower()
    except Exception:
        return 3
    if "wasapi" in name:
        return 0
    if "directsound" in name:
        return 1
    if "mme" in name:
        return 2
    if "wdm" in name:
        return 9
    return 3


def _device_is_loopback(device) -> bool:
    """True for endpoints that carry the system's own playback."""
    try:
        name = str(device.get("name", "")).lower()
    except Exception:
        return False
    return any(marker in name for marker in _LOOPBACK_DEVICE_MARKERS)


def order_input_candidates(devices, preferred: int | None = None) -> list[int]:
    """Capture endpoints in the order they should be opened.

    A real microphone always precedes a loopback endpoint.  The host-API rank
    alone is not enough on a machine with VB-Cable installed: WASAPI exposes
    "CABLE Output" (rank 0) and a working Realtek microphone appears only under
    WDM-KS (rank 9), so backend-first ordering would hand the recording to the
    virtual cable and capture the system's own playback instead of the person.
    Loopback devices stay in the list, ranked after real ones, so a machine
    with no usable microphone can still record by playing the voice aloud.
    """
    candidates = [
        index
        for index, device in enumerate(devices)
        if device.get("max_input_channels", 0) > 0
    ]
    return sorted(
        candidates,
        key=lambda i: (
            # An explicitly preferred endpoint goes first even when it is a
            # loopback one -- that is the whole point of
            # ``VOICECHAT_INPUT_DEVICE`` on a machine without a microphone.
            0 if i == preferred else _device_is_loopback(devices[i]),
            0 if i == preferred else 1,
            _hostapi_rank(devices, i),
            i,
        ),
    )


def _resolve_capture_rate(device_id: int, devices, *, log=None) -> int:
    """Return the rate to open ``device_id`` at.

    16 kHz is tried first so the common case stays a passthrough and behaves
    exactly as before.  Only when the driver refuses it (many Realtek capture
    endpoints expose 44.1/48 kHz only) do we fall back to the device's own
    default and let :class:`_CaptureResampler` bring it down to 16 kHz.

    ``VOICECHAT_CAPTURE_RATE`` forces a specific rate.  If nothing can be
    verified the configured ``SAMPLE_RATE`` is returned unchanged, so opening
    the stream still fails with the driver's own error rather than a guess.
    """
    forced = os.environ.get(_CAPTURE_RATE_ENV)
    candidates: list[int] = []
    if forced:
        try:
            candidates.append(int(forced))
        except ValueError:
            if log is not None:
                log.warning("ignoring %s=%r: not an integer", _CAPTURE_RATE_ENV, forced)
    try:
        native = int(devices[device_id].get("default_samplerate") or 0)
    except Exception:
        native = 0
    candidates.extend(_CAPTURE_RATE_FALLBACKS)
    if native:
        candidates.append(native)

    seen: set[int] = set()
    for rate in candidates:
        if rate <= 0 or rate in seen:
            continue
        seen.add(rate)
        try:
            sd.check_input_settings(
                device=device_id,
                samplerate=rate,
                channels=CHANNELS,
                dtype="float32",
            )
        except Exception:
            continue
        return rate
    return SAMPLE_RATE


class _AutoModelInference:
    """Adapt ``funasr.AutoModel`` to the ``inference(data_in=[...])`` contract.

    Some Fun-ASR-Nano checkpoints ship without the ``model.py`` entry point
    that ``FunASRNano.from_pretrained`` imports, even though the same
    ``model.pt`` loads fine through funasr's own AutoModel.  The service only
    ever calls ``inference(data_in=[path], language=...)`` and reads back a
    list of ``{"text": ...}`` segments, so mapping that signature is enough.
    """

    def __init__(self, model) -> None:
        self._model = model

    def inference(self, data_in=None, **kwargs):
        language = kwargs.get("language") or "zh"
        results = []
        for path in data_in or []:
            try:
                out = self._model.generate(
                    input=path,
                    cache={},
                    batch_size_s=60,
                    language=language,
                    use_itn=True,
                )
            except Exception as exc:
                logger.warning("AutoModel inference failed on %s: %s", path, exc)
                out = []
            results.append(out if out else [{"text": ""}])
        return results

    # load_model() normalizes dtypes and calls eval() on the returned object.
    # AutoModel owns its own module graph and already applied the checkpoint's
    # own loading path, so these are deliberately no-ops.
    def parameters(self):
        return iter(())

    def float(self):
        return self

    def eval(self):
        return self


@dataclass
class _RecordingState:
    """Ownership bundle for one microphone recording.

    The callback and collector close over this object instead of looking up
    mutable service-level queue/list fields.  That keeps a completed
    recording isolated from a later recording and gives shutdown one place to
    order accepted frames before the sentinel.
    """

    audio_queue: queue.Queue
    recorded_audio: list
    stream: object | None = None
    accepting_frames: bool = False
    stop_requested: bool = False
    cancelled: bool = False
    sentinel_enqueued: bool = False
    vad_triggered: bool = False
    vad_silence_frames: int = 0
    vad_speech_frames: int = 0
    vad_buffer: list = field(default_factory=list, repr=False)
    vad_buffer_samples: int = 0
    vad_speech_started: bool = False
    capture_rate: int = SAMPLE_RATE
    resampler: object | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    collector_thread: threading.Thread | None = field(default=None, repr=False)


class STTService:
    """Speech-to-Text service using FunASR (Fun-ASR-Nano-2512)."""
    
    def __init__(self, model_path: str = FUNASR_MODEL_PATH, device: str = None):
        self.model_path = model_path
        self.device = device or (
            "cuda" if torch is not None and torch.cuda.is_available() else "cpu"
        )
        self.model = None
        self.model_kwargs = {}
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.recorded_audio = []
        self.stream = None
        self._recording_state: _RecordingState | None = None
        self._recording_state_lock = threading.RLock()
        self.vad_adapter: FSMNVADAdapter | None = None
        self._vad_backend = "RMS_FALLBACK"
        self._vad_device = os.getenv("FSMN_VAD_DEVICE", "cpu")
        self._vad_adapter_attempted = False
        self._vad_lock = threading.Lock()

        # VAD state
        self._vad_triggered = False
        self._vad_silence_frames = 0
        self._vad_speech_frames = 0
        self._vad_enabled = USE_VAD_AUTO_STOP
        
    def load_model(self, progress_callback=None):
        """Load the FunASR model."""
        if progress_callback:
            progress_callback("Loading STT model...")

        if torch is None:
            raise RuntimeError(
                "STT unavailable: torch is not installed in this environment "
                "(use the launcher's -FullRepair, or pip install torch torchaudio)"
            )

        try:
            if self.model_path not in sys.path:
                sys.path.insert(0, self.model_path)

            FunASRNano = load_checkpoint_entry(self.model_path)
            if FunASRNano is None:
                logger.warning(
                    "No usable model.py beside %s (the FunASRNano entry point is absent); "
                    "loading the same weights through funasr.AutoModel instead.",
                    self.model_path,
                )

            _orig_module_to = torch.nn.Module.to

            def _patched_module_to(self_mod, *args, **kwargs):
                try:
                    return _orig_module_to(self_mod, *args, **kwargs)
                except Exception as exc:
                    if "meta tensor" in str(exc).lower() or "Cannot copy out of meta" in str(exc):
                        device_arg = None
                        for a in args:
                            if isinstance(a, (torch.device, str)) and str(a) != 'cpu':
                                device_arg = a
                                break
                        for k, v in kwargs.items():
                            if k == 'device' and v is not None:
                                device_arg = v
                                break
                        if device_arg is None:
                            device_arg = self.device if hasattr(self, 'device') else 'cuda'
                        tgt_device = torch.device(device_arg)
                        self_mod = self_mod.to_empty(device=tgt_device)
                        for name, param in self_mod.named_parameters():
                            if param.is_meta:
                                with torch.no_grad():
                                    self_mod.register_parameter(
                                        name,
                                        torch.nn.Parameter(
                                            torch.zeros_like(param, device=tgt_device)
                                        ),
                                    )
                        for name, buf in self_mod.named_buffers():
                            if buf.is_meta:
                                self_mod.register_buffer(
                                    name,
                                    torch.zeros_like(buf, device=tgt_device),
                                )
                        return self_mod
                    raise

            if FunASRNano is not None:
                torch.nn.Module.to = _patched_module_to
                try:
                    self.model, self.model_kwargs = FunASRNano.from_pretrained(
                        model=self.model_path,
                        device=self.device
                    )
                finally:
                    torch.nn.Module.to = _orig_module_to
            else:
                from funasr import AutoModel

                auto_device = self.device or "cpu"
                if auto_device == "cuda":
                    auto_device = "cuda:0"
                self.model = _AutoModelInference(
                    AutoModel(
                        model=self.model_path,
                        device=auto_device,
                        vad_model=None,
                        punc_model=None,
                        disable_update=True,
                        disable_pbar=True,
                        disable_log=True,
                    )
                )
                self.model_kwargs = {}

            self.model_kwargs['language'] = 'zh'
            # The bundled Fun-ASR-Nano checkpoint mixes bf16 LLM weights with
            # fp32 audio-encoder/adaptor weights.  Its inference path does
            # not enter a single autocast region before the audio adaptor,
            # so the mixed modules otherwise fail at the first Linear layer
            # (Float/BFloat16 mismatch).  Use a uniform dtype for this local
            # application service; accuracy is preferable to the small memory
            # saving on the development GPU, and the actual A100 host has
            # ample capacity for the same safe path.
            parameter_dtypes = {parameter.dtype for parameter in self.model.parameters()}
            if len(parameter_dtypes) > 1:
                logger.warning(
                    "FunASR checkpoint has mixed parameter dtypes %s; "
                    "normalizing to float32 for compatible inference.",
                    sorted(str(dtype) for dtype in parameter_dtypes),
                )
                self.model = self.model.float()
            self.model.eval()
            
        except Exception as e:
            logger.warning(f"Error loading FunASR model: {e}")
            raise e
        
        if progress_callback:
            progress_callback("STT model loaded!")
            
        return True

    def _ensure_fsmn_vad(self) -> None:
        """Load FSMN-VAD once when a real recording is about to begin.

        VAD loading is kept separate from the Fun-ASR-Nano recognizer.  If
        the optional endpoint model is unavailable, the already-supported RMS
        endpoint detector remains the explicitly logged fallback.
        """
        if not self._vad_enabled or self.vad_adapter is not None:
            return
        if self._vad_adapter_attempted:
            return
        # Unit/test services can exercise recording mechanics without loading
        # a real ASR model; do not trigger a network model download there.
        if self.model is None:
            return

        self._vad_adapter_attempted = True
        adapter = FSMNVADAdapter(device=self._vad_device, model_id=VAD_MODEL_PATH)
        try:
            adapter.load()
        except Exception as exc:
            self._vad_backend = "RMS_FALLBACK"
            logger.warning(
                "FSMN_VAD unavailable; using RMS_FALLBACK: %s",
                exc,
                exc_info=True,
            )
            try:
                adapter.close()
            except Exception:
                pass
            return

        self.vad_adapter = adapter
        self._vad_backend = "FSMN_VAD"
        logger.info(
            "VAD backend active: FSMN_VAD device=%s chunk_samples=%s",
            self._vad_device,
            FSMNVADAdapter.CHUNK_SAMPLES,
        )

    def _startup_attempt_is_current(self, recording_state: _RecordingState) -> bool:
        """Check startup ownership without holding locks across provider calls."""
        # Keep the same state-lock -> service-lock ordering used by the stop
        # path.  The locks are held only for the in-memory check.
        with recording_state.lock:
            if recording_state.stop_requested:
                return False
            with self._recording_state_lock:
                return self._recording_state is recording_state

    def _discard_startup_state(self, recording_state: _RecordingState) -> None:
        """Retire a startup attempt without publishing a stale stream."""
        self._request_recording_stop(recording_state)
        with self._recording_state_lock:
            if self._recording_state is not recording_state:
                return
            self._recording_state = None
            self.is_recording = False
            self.stream = None
            if self.recorded_audio is recording_state.recorded_audio:
                self.recorded_audio = []
            if self.audio_queue is recording_state.audio_queue:
                self.audio_queue = queue.Queue()
    
    def start_recording(self):
        """Start recording audio from microphone."""
        self._ensure_fsmn_vad()
        recording_state = _RecordingState(
            audio_queue=queue.Queue(),
            recorded_audio=[],
            accepting_frames=True,
        )
        with self._recording_state_lock:
            self._recording_state = recording_state
            # Keep the historical public attributes as compatibility aliases;
            # worker code below uses the recording-local state object.
            self.audio_queue = recording_state.audio_queue
            self.recorded_audio = recording_state.recorded_audio
            self.is_recording = True

        self._vad_triggered = False
        self._vad_silence_frames = 0
        self._vad_speech_frames = 0
        if self.vad_adapter is not None and self._vad_backend == "FSMN_VAD":
            try:
                with self._vad_lock:
                    self.vad_adapter.reset()
            except Exception as exc:
                logger.warning(
                    "FSMN_VAD reset failed; using RMS_FALLBACK: %s",
                    exc,
                    exc_info=True,
                )
                self.vad_adapter = None
                self._vad_backend = "RMS_FALLBACK"
        
        def _make_audio_callback(resampler: _CaptureResampler):
            """Build the stream callback for one already-resolved device.

            The device may deliver 44.1/48 kHz blocks; converting here keeps
            every consumer below (VAD chunk accounting, silence timers, the
            saved wav, ASR) receiving exactly the 16 kHz stream it expects.
            """

            def audio_callback(indata, frames, time, status):
                if status:
                    logger.debug(f"Audio status: {status}")
                with recording_state.lock:
                    if not recording_state.accepting_frames:
                        return

                    # Put the accepted frame before requesting stop.  The same
                    # recording lock protects this ordering against manual stop.
                    block = resampler.process(indata)
                    if block is not None and len(block):
                        recording_state.audio_queue.put(block)

            return audio_callback
        
        try:
            device_id = None
            # Find the best input device without ever preferring a loopback
            # endpoint that records playback instead of the participant.
            devices = sd.query_devices()
            try:
                default_index = sd.query_devices(kind="input")["index"]
            except Exception:
                default_index = None

            best_device_id, reason = select_input_device(devices, default_index=default_index)
            if best_device_id is None:
                raise RuntimeError("No microphone found! Please check your audio settings.")
            logger.info(f"[ASRDebug] Selected input device: {reason} (Index {best_device_id})")

            # Open order comes from a helper so the rule stays testable without
            # a sound device: real capture endpoints first, loopback last.
            candidates = order_input_candidates(devices, preferred=best_device_id)

            # Keep a newly constructed stream local until the complete startup
            # attempt has passed its stale/cancellation checks.  Publishing it
            # early would let a late start thread overwrite a newer recording.
            candidate_stream = None
            candidate_resampler = None
            candidate_rate = SAMPLE_RATE
            last_open_error: Optional[Exception] = None
            for candidate_id in candidates:
                try:
                    capture_rate = _resolve_capture_rate(candidate_id, devices, log=logger)
                    resampler = _CaptureResampler(capture_rate)
                    candidate_stream = sd.InputStream(
                        samplerate=capture_rate,
                        channels=CHANNELS,
                        dtype=np.float32,
                        callback=_make_audio_callback(resampler),
                        blocksize=resampler.blocksize,
                        device=candidate_id
                    )
                except Exception as exc:
                    last_open_error = exc
                    self._close_stream_handle(candidate_stream)
                    candidate_stream = None
                    logger.warning(
                        "[ASRDebug] input device %s failed to open: %s; trying next candidate",
                        candidate_id, exc,
                    )
                    continue
                device_id = candidate_id
                candidate_resampler = resampler
                candidate_rate = capture_rate
                try:
                    api_name = sd.query_hostapis(int(devices[candidate_id].get("hostapi")))["name"]
                except Exception:
                    api_name = "unknown"
                if resampler.enabled:
                    logger.info(
                        "[ASRDebug] device %s via %s captures at %d Hz; resampling to "
                        "%d Hz (soxr stream, %d-frame blocks)",
                        candidate_id, api_name, capture_rate, SAMPLE_RATE,
                        resampler.blocksize,
                    )
                else:
                    logger.info(
                        "[ASRDebug] device %s via %s captures natively at %d Hz "
                        "(no resampling)",
                        candidate_id, api_name, capture_rate,
                    )
                if candidate_id != best_device_id:
                    if _device_is_loopback(devices[candidate_id]):
                        logger.warning(
                            "[ASRDebug] falling back to 内录/loopback device %s: the "
                            "recording captures SYSTEM PLAYBACK, not a microphone. "
                            "Play the participant's voice through the speakers.",
                            candidate_id,
                        )
                    else:
                        logger.warning(
                            "[ASRDebug] falling back to input device %s (preferred device "
                            "could not be opened)", candidate_id,
                        )
                break

            if candidate_stream is None or device_id is None:
                raise RuntimeError(
                    f"No usable input device could be opened (last error: {last_open_error}). "
                    "Check Windows sound settings: the recording device may be disabled."
                )

            if not self._startup_attempt_is_current(recording_state):
                self._close_stream_handle(candidate_stream)
                return False

            # Start only after the stale/cancellation checks so a cancelled
            # startup never activates the device.  A start failure falls into
            # the outer handler below, which closes the stream and reports
            # the error.
            candidate_stream.start()

            # A concurrent stop can still retire this attempt safely; the
            # collector below is committed atomically before startup is
            # reported successful.
            def collect_audio():
                while True:
                    audio_chunk = recording_state.audio_queue.get()
                    try:
                        if audio_chunk is _RECORDING_SENTINEL:
                            break
                        recording_state.recorded_audio.append(audio_chunk)
                    finally:
                        recording_state.audio_queue.task_done()

                    self._process_vad_audio(recording_state, audio_chunk)

                # Drain the resampler's internal delay so the tail of the last
                # syllable is not dropped.  A flush failure is harmless: it
                # costs at most a few milliseconds at the very end.
                resampler = recording_state.resampler
                if resampler is not None and resampler.enabled:
                    try:
                        tail = resampler.process(
                            np.zeros((0, CHANNELS), dtype=np.float32), last=True
                        )
                        if tail is not None and len(tail):
                            recording_state.recorded_audio.append(tail)
                    except Exception as exc:
                        logger.debug("resampler flush skipped: %s", exc)

                # VAD auto-stop (or manual stop) reached: close the recording
                # stream from this collector thread, not from the audio
                # callback.  The sentinel proves all accepted frames were
                # drained before this point.
                self._close_stream_handle(recording_state.stream)
                with self._recording_state_lock:
                    if self._recording_state is recording_state:
                        self.stream = None
                        self.is_recording = False

            collector = threading.Thread(
                target=collect_audio,
                daemon=True,
            )

            # Commit the stream and collector as one lifecycle transition.
            # No PortAudio operation is performed while these locks are held.
            committed = False
            with recording_state.lock:
                with self._recording_state_lock:
                    if (
                        not recording_state.stop_requested
                        and self._recording_state is recording_state
                    ):
                        recording_state.stream = candidate_stream
                        recording_state.collector_thread = collector
                        recording_state.resampler = candidate_resampler
                        recording_state.capture_rate = candidate_rate
                        self.stream = candidate_stream
                        self.collect_thread = collector
                        collector.start()
                        committed = True

            if not committed:
                self._close_stream_handle(candidate_stream)
                return False

            return True

        except Exception as e:
            logger.warning(f"Error starting recording stream: {e}")
            candidate = locals().get("candidate_stream")
            if candidate is not None:
                self._close_stream_handle(candidate)

            if not self._startup_attempt_is_current(recording_state):
                self._discard_startup_state(recording_state)
                return False

            self._discard_startup_state(recording_state)
            logger.exception("Exception occurred")
            raise RecordingStartError(
                f"Unable to start microphone recording: {e}"
            ) from e

    def _process_vad_audio(self, recording_state: _RecordingState, audio_chunk) -> None:
        """Run the active endpoint detector from the collector thread only."""
        if not self._vad_enabled:
            return
        if self._vad_backend == "FSMN_VAD" and self.vad_adapter is not None:
            self._process_fsmn_vad_audio(recording_state, audio_chunk)
            return
        if self._vad_backend == "RMS_FALLBACK":
            self._update_rms_vad(recording_state, audio_chunk)

    def _process_fsmn_vad_audio(
        self,
        recording_state: _RecordingState,
        audio_chunk,
    ) -> None:
        """Accumulate 200 ms PCM chunks and feed FSMN-VAD in order."""
        flat_chunk = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if flat_chunk.size == 0:
            return
        with self._vad_lock:
            with self._recording_state_lock:
                if self._recording_state is not recording_state:
                    return
                if recording_state.stop_requested:
                    return
            recording_state.vad_buffer.append(flat_chunk.copy())
            recording_state.vad_buffer_samples += int(flat_chunk.size)

            chunk_samples = FSMNVADAdapter.CHUNK_SAMPLES
            while recording_state.vad_buffer_samples >= chunk_samples:
                merged = np.concatenate(recording_state.vad_buffer)
                vad_chunk = merged[:chunk_samples]
                remaining = merged[chunk_samples:]
                recording_state.vad_buffer = (
                    [remaining] if remaining.size else []
                )
                recording_state.vad_buffer_samples = int(remaining.size)
                try:
                    event = self.vad_adapter.feed(vad_chunk, is_final=False)
                except Exception as exc:
                    logger.warning(
                        "FSMN_VAD feed failed; switching to RMS_FALLBACK: %s",
                        exc,
                        exc_info=True,
                    )
                    self.vad_adapter = None
                    self._vad_backend = "RMS_FALLBACK"
                    self._update_rms_vad(recording_state, vad_chunk)
                    return
                self._handle_vad_event(recording_state, event)
                if recording_state.stop_requested:
                    return

    def _handle_vad_event(
        self,
        recording_state: _RecordingState,
        event: VadEvent,
    ) -> None:
        """Apply one adapter event only to its still-current recording."""
        if event is VadEvent.NONE:
            return
        with self._recording_state_lock:
            if self._recording_state is not recording_state:
                return
            if recording_state.stop_requested:
                return
            if event is VadEvent.SPEECH_START:
                recording_state.vad_speech_started = True
                return
            if event is VadEvent.SPEECH_END:
                recording_state.vad_speech_started = (
                    recording_state.vad_speech_started
                    or bool(getattr(self.vad_adapter, "speech_seen", False))
                )
                if not recording_state.vad_speech_started:
                    return
        self._request_recording_stop(recording_state, vad_triggered=True)

    def _update_rms_vad(self, recording_state: _RecordingState, audio_chunk) -> None:
        """Legacy endpoint fallback used only when FSMN-VAD is unavailable."""
        flat_chunk = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        if flat_chunk.size == 0:
            return
        with self._recording_state_lock:
            if self._recording_state is not recording_state:
                return
            if recording_state.stop_requested:
                return
            rms = np.sqrt(np.mean(flat_chunk ** 2))
            if rms < VAD_SILENCE_THRESHOLD:
                recording_state.vad_silence_frames += int(flat_chunk.size)
            else:
                recording_state.vad_silence_frames = 0
                recording_state.vad_speech_frames += int(flat_chunk.size)
                recording_state.vad_speech_started = True

            self._vad_silence_frames = recording_state.vad_silence_frames
            self._vad_speech_frames = recording_state.vad_speech_frames
            silence_sec = recording_state.vad_silence_frames / SAMPLE_RATE
            speech_sec = recording_state.vad_speech_frames / SAMPLE_RATE
            should_stop = (
                silence_sec >= VAD_SILENCE_DURATION
                and speech_sec >= VAD_SPEECH_MIN_DURATION
            )
        if should_stop:
            self._request_recording_stop(recording_state, vad_triggered=True)

    def _request_recording_stop(
        self,
        recording_state: _RecordingState | None = None,
        *,
        vad_triggered: bool = False,
    ) -> bool:
        """Request one lossless recording shutdown and enqueue one sentinel.

        The caller may be the audio callback or a UI/manual-stop thread.  A
        per-recording lock makes the accepted-frame -> sentinel ordering
        deterministic without holding a lock while closing the stream.
        """
        state = recording_state or self._recording_state
        if state is None:
            self.is_recording = False
            return False

        with state.lock:
            if vad_triggered:
                state.vad_triggered = True
            if state.stop_requested:
                with self._recording_state_lock:
                    if self._recording_state is state and state.vad_triggered:
                        self._vad_triggered = True
                return False

            state.accepting_frames = False
            state.stop_requested = True
            state.sentinel_enqueued = True
            state.audio_queue.put(_RECORDING_SENTINEL)

        with self._recording_state_lock:
            if self._recording_state is state:
                self.is_recording = False
                if state.vad_triggered:
                    self._vad_triggered = True
        return True

    @staticmethod
    def _close_stream_handle(stream) -> None:
        """Close one recording stream outside the PortAudio callback."""
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            logger.warning(f"Error closing recording stream: {e}")
        
    def _stop_stream(self):
        """Close the microphone input stream if it is still open.

        Safe to call from any thread EXCEPT the stream's own audio callback
        (sounddevice forbids closing the active stream from inside it).
        """
        stream = getattr(self, "stream", None)
        self._close_stream_handle(stream)
        self.stream = None

    def cancel(self) -> None:
        """Abandon an in-flight capture; stop_recording() then yields no audio.

        Implements the unified voice cancel interface.  The lossless stop
        request still runs so the queue drains and the stream closes
        deterministically, but the captured frames are discarded at the
        return boundary instead of being transcribed.
        """
        recording_state = self._recording_state
        if recording_state is None:
            self.is_recording = False
            self._stop_stream()
            return
        with recording_state.lock:
            recording_state.cancelled = True
        self._request_recording_stop(recording_state)

    def stop_recording(self) -> np.ndarray:
        """Stop recording and return the audio data."""
        recording_state = self._recording_state
        if recording_state is None:
            self.is_recording = False
            self._stop_stream()
            # A completed recording is consumed by the first stop call.  The
            # compatibility alias must never turn historical samples into a
            # second utterance when stop_recording() is called again.
            return np.array([])
        else:
            self._request_recording_stop(recording_state)

            collector = recording_state.collector_thread
            if collector is not None and collector is not threading.current_thread():
                # The collector exits only after consuming the sentinel, so
                # joining here is the drain completion point.
                collector.join()
            elif collector is None:
                # Defensive path for a partially-started stream.  Normal
                # starts create the collector before opening the stream.
                self._close_stream_handle(recording_state.stream)

            completed_chunks = recording_state.recorded_audio
            with self._recording_state_lock:
                if self._recording_state is recording_state:
                    self._recording_state = None
                    self.stream = None
                    self.is_recording = False
                    # Detach compatibility aliases only after the collector
                    # has drained; the local completed_chunks remains the
                    # sole owner used to build this first return value.
                    if self.recorded_audio is completed_chunks:
                        self.recorded_audio = []
                    if self.audio_queue is recording_state.audio_queue:
                        self.audio_queue = queue.Queue()

            if recording_state.cancelled:
                # cancel() was requested: the drain above still ran so the
                # capture lifecycle stays deterministic, but no frames are
                # handed back for transcription.
                return np.array([])

            chunks = completed_chunks

        # Concatenate all audio chunks exactly once after the collector drain.
        if chunks:
            audio = np.concatenate(chunks, axis=0)
            return audio.flatten()
        return np.array([])

    def is_vad_triggered(self) -> bool:
        """Check if VAD auto-stop was triggered since last start_recording."""
        return self._vad_triggered

    def set_vad_enabled(self, enabled: bool):
        self._vad_enabled = enabled

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio to text."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")
            
        if len(audio) == 0:
            return ""
            
        try:
            # FunASRNano inference usually expects file paths or specific format.
            # To be safe and compatible with test_asr.py which uses file path,
            # let's save to a temporary wav file.
            
            # Debug: print audio stats
            logger.debug(f"Audio length: {len(audio)} samples ({len(audio)/SAMPLE_RATE:.2f}s)")
            logger.debug(f"Audio range: [{audio.min():.4f}, {audio.max():.4f}], mean: {audio.mean():.4f}")
            
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = tmp_file.name
                
            # Save numpy array to wav
            # audio is float32. soundfile handle this.
            sf.write(tmp_path, audio, SAMPLE_RATE)
            
            try:
                res = self.model.inference(
                    data_in=[tmp_path],
                    **self.model_kwargs
                )
                # res structure from test_asr.py: res[0][0]["text"]
                # res seems to be a list of results (one per input file)
                # each result is a list of segments?
                
                logger.debug(f"Raw result: {text_meta('raw', res, limit=1000)}")
                
                if res and len(res) > 0:
                    item = res[0]
                    if isinstance(item, list) and len(item) > 0:
                        text = item[0].get("text", "").strip()
                    elif isinstance(item, dict):
                        text = item.get("text", "").strip()
                    else:
                        text = ""
                    
                    # Check if result is primarily Chinese, if not retry with forced Chinese
                    if text and not self._is_chinese_text(text):
                        logger.warning(
                            "Non-Chinese text detected: "
                            f"{text_meta('text', text)}, retrying with forced Chinese..."
                        )
                        # Retry with explicit Chinese language setting
                        retry_kwargs = dict(self.model_kwargs)
                        retry_kwargs['language'] = 'zh'
                        retry_res = self.model.inference(
                            data_in=[tmp_path],
                            **retry_kwargs
                        )
                        if retry_res and len(retry_res) > 0:
                            retry_item = retry_res[0]
                            if isinstance(retry_item, list) and len(retry_item) > 0:
                                text = retry_item[0].get("text", "").strip()
                            elif isinstance(retry_item, dict):
                                text = retry_item.get("text", "").strip()
                        logger.info(f"Retry result: {text_meta('retry_result', text)}")
                    
                    # Post-processing: correct common misrecognitions
                    text = self._correct_common_errors(text)
                    return text
                
                return ""
            finally:
                # Cleanup temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            
        except Exception as e:
            logger.warning(f"Transcription error: {e}")
            logger.exception("Exception occurred")
            return ""
    
    def record_and_transcribe(self) -> str:
        """Convenience method to record and transcribe."""
        audio = self.stop_recording()
        return self.transcribe(audio)

    def warmup(self):
        """Warmup the STT model with test audio data."""
        logger.info("Warming up STT model...")
        try:
            # Create 1 second of white noise instead of silence
            # This helps avoid edge cases in model processing
            dummy_audio = np.random.randn(SAMPLE_RATE).astype(np.float32) * 0.01
            result = self.transcribe(dummy_audio)
            logger.info(f"STT warmup complete. {text_meta('warmup_result', result)}")
            return True
        except Exception as e:
            logger.warning(f"STT Warmup had issue (non-fatal): {e}")
            # Don't fail completely - the model may still work
            return True

    def cleanup(self):
        """Stop capture and release the loaded ASR model before application exit."""
        self.stop_recording()
        if self.vad_adapter is not None:
            try:
                with self._vad_lock:
                    self.vad_adapter.close()
            except Exception as exc:
                logger.warning("Error releasing FSMN_VAD model: %s", exc)
            self.vad_adapter = None
        self.model_kwargs = {}
        if self.model is not None:
            try:
                del self.model
            except Exception as exc:
                logger.warning(f"Error releasing FunASR model: {exc}")
            self.model = None
        gc.collect()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _correct_common_errors(self, text: str) -> str:
        """Correct common STT misrecognitions, especially drug-related terms."""
        if not text:
            return text
        
        # Common misrecognitions mapping
        corrections = {
            # Drug-related terms
            "西毒": "吸毒",
            "吸读": "吸毒",
            "吸独": "吸毒",
            "习毒": "吸毒",
            "洗毒": "吸毒",
            "细毒": "吸毒",
            "戒读": "戒毒",
            "截毒": "戒毒",
            "接毒": "戒毒",
            "冰读": "冰毒",
            "并毒": "冰毒",
            "海洛音": "海洛因",
            "海螺因": "海洛因",
            "摇头完": "摇头丸",
            "K份": "K粉",
            "K分": "K粉",
            # Other common terms
            "强制隔离戒读": "强制隔离戒毒",
            "戒读所": "戒毒所",
        }
        
        for wrong, correct in corrections.items():
            text = text.replace(wrong, correct)
        
        return text
    
    def _is_chinese_text(self, text: str) -> bool:
        """Check if text is primarily Chinese (>30% Chinese characters)."""
        if not text:
            return True  # Empty is fine
        
        # Count Chinese characters (CJK Unified Ideographs range)
        chinese_count = 0
        total_chars = 0
        
        for char in text:
            # Skip whitespace and punctuation
            if char.isspace() or char in '.,!?;:()[]{}、。，！？；：（）【】""''':
                continue
            total_chars += 1
            # Chinese character range: \u4e00-\u9fff (CJK Unified Ideographs)
            if '\u4e00' <= char <= '\u9fff':
                chinese_count += 1
        
        if total_chars == 0:
            return True  # Only punctuation/whitespace is fine
        
        ratio = chinese_count / total_chars
        logger.debug(f"Chinese ratio: {ratio:.2f} ({chinese_count}/{total_chars})")
        
        # If less than 30% Chinese, likely not Chinese
        return ratio >= 0.3


# Singleton instance
_stt_service = None


def get_stt_service() -> STTService:
    """Return the shared speech-input service for the configured provider.

    The ASR provider is selected by runtime configuration
    (config/runtime.yaml -> asr.provider).  ``funasr`` is currently the
    only implementation; an unknown provider is a hard error so a
    misconfiguration cannot silently disable speech input.
    """
    global _stt_service
    from config import ASR_PROVIDER
    from deployment.runtime_config import ASR_PROVIDERS, ASR_PROVIDER_FUNASR

    if ASR_PROVIDER != ASR_PROVIDER_FUNASR:
        raise ValueError(
            f"unknown ASR provider {ASR_PROVIDER!r}; "
            f"supported providers: {', '.join(sorted(ASR_PROVIDERS))}"
        )
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
