# STT Service - FunASR Integration

import os
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
import torch
import tempfile
import soundfile as sf

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    FUNASR_MODEL_PATH, SAMPLE_RATE, CHANNELS,
    USE_VAD_AUTO_STOP, VAD_SILENCE_THRESHOLD,
    VAD_SILENCE_DURATION, VAD_SPEECH_MIN_DURATION
)
from services.logger import get_logger

logger = get_logger(__name__)


_RECORDING_SENTINEL = object()


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
    sentinel_enqueued: bool = False
    vad_triggered: bool = False
    vad_silence_frames: int = 0
    vad_speech_frames: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    collector_thread: threading.Thread | None = field(default=None, repr=False)


class STTService:
    """Speech-to-Text service using FunASR (Fun-ASR-Nano-2512)."""
    
    def __init__(self, model_path: str = FUNASR_MODEL_PATH, device: str = None):
        self.model_path = model_path
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.model_kwargs = {}
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.recorded_audio = []
        self.stream = None
        self._recording_state: _RecordingState | None = None
        self._recording_state_lock = threading.RLock()

        # VAD state
        self._vad_triggered = False
        self._vad_silence_frames = 0
        self._vad_speech_frames = 0
        self._vad_enabled = USE_VAD_AUTO_STOP
        
    def load_model(self, progress_callback=None):
        """Load the FunASR model."""
        if progress_callback:
            progress_callback("Loading STT model...")
            
        try:
            if self.model_path not in sys.path:
                sys.path.insert(0, self.model_path)

            try:
                from model import FunASRNano
            except ImportError:
                logger.warning(f"Could not import FunASRNano from {self.model_path}, checking path...")
                raise

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

            torch.nn.Module.to = _patched_module_to
            try:
                self.model, self.model_kwargs = FunASRNano.from_pretrained(
                    model=self.model_path,
                    device=self.device
                )
            finally:
                torch.nn.Module.to = _orig_module_to

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
    
    def start_recording(self):
        """Start recording audio from microphone."""
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
        
        def audio_callback(indata, frames, time, status):
            if status:
                logger.debug(f"Audio status: {status}")
            should_stop = False
            with recording_state.lock:
                if not recording_state.accepting_frames:
                    return

                # Put the accepted frame before requesting stop.  The same
                # recording lock protects this ordering against manual stop.
                recording_state.audio_queue.put(indata.copy())

                # VAD: energy-based silence detection.  This policy remains
                # unchanged in this hardening step; only shutdown mechanics
                # are changing.
                if self._vad_enabled:
                    rms = np.sqrt(np.mean(indata ** 2))
                    if rms < VAD_SILENCE_THRESHOLD:
                        recording_state.vad_silence_frames += frames
                    else:
                        recording_state.vad_silence_frames = 0
                        recording_state.vad_speech_frames += frames

                    silence_sec = recording_state.vad_silence_frames / SAMPLE_RATE
                    speech_sec = recording_state.vad_speech_frames / SAMPLE_RATE
                    should_stop = (
                        silence_sec >= VAD_SILENCE_DURATION
                        and speech_sec >= VAD_SPEECH_MIN_DURATION
                    )

                with self._recording_state_lock:
                    if self._recording_state is recording_state:
                        self._vad_silence_frames = recording_state.vad_silence_frames
                        self._vad_speech_frames = recording_state.vad_speech_frames

            if should_stop:
                self._request_recording_stop(recording_state, vad_triggered=True)
        
        try:
            device_id = None
            # Find the best input device
            devices = sd.query_devices()
            best_device_id = None

            # Default: prefer real microphone
            mic_keywords = ["mic", "microphone", "麦克风", "array", "realtek"]
            virtual_keywords = ["cable", "virtual", "stereo mix"]

            # Priority 1: Real microphone
            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    name = dev['name'].lower()
                    if any(k in name for k in mic_keywords):
                        best_device_id = i
                        logger.info(f"[ASRDebug] Selected microphone: {dev['name']} (Index {i})")
                        break

            # Priority 2: Virtual/cable (only if no mic found)
            if best_device_id is None:
                for i, dev in enumerate(devices):
                    if dev['max_input_channels'] > 0:
                        name = dev['name'].lower()
                        if any(k in name for k in virtual_keywords):
                            best_device_id = i
                            logger.info(f"[ASRDebug] Selected virtual input: {dev['name']} (Index {i})")
                            break

            # Priority 3: Default input device
            if best_device_id is None:
                try:
                    default_device = sd.query_devices(kind='input')
                    best_device_id = default_device['index']
                    logger.info(f"[ASRDebug] Using default input: {default_device['name']}")
                except Exception:
                    pass

            # Priority 4: ANY input device
            if best_device_id is None:
                for i, dev in enumerate(devices):
                    if dev['max_input_channels'] > 0:
                        best_device_id = i
                        logger.info(f"[ASRDebug] Fallback device: {dev.get('name')} (Index {i})")
                        break
            
            if best_device_id is None:
                raise RuntimeError("No microphone found! Please check your audio settings.")
            
            device_id = best_device_id

            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.float32,
                callback=audio_callback,
                blocksize=1024,
                device=device_id
            )
            recording_state.stream = self.stream

            # Start the collector before opening the stream.  A device may
            # deliver a callback immediately from ``start()``; the collector
            # must already be waiting so a stop request can always drain the
            # accepted frame followed by the sentinel.
            def collect_audio():
                while True:
                    audio_chunk = recording_state.audio_queue.get()
                    try:
                        if audio_chunk is _RECORDING_SENTINEL:
                            break
                        recording_state.recorded_audio.append(audio_chunk)
                    finally:
                        recording_state.audio_queue.task_done()

                # VAD auto-stop (or manual stop) reached: close the recording
                # stream from this collector thread, not from the audio
                # callback.  The sentinel proves all accepted frames were
                # drained before this point.
                self._close_stream_handle(recording_state.stream)
                with self._recording_state_lock:
                    if self._recording_state is recording_state:
                        self.stream = None
                        self.is_recording = False

            recording_state.collector_thread = threading.Thread(
                target=collect_audio,
                daemon=True,
            )
            self.collect_thread = recording_state.collector_thread
            recording_state.collector_thread.start()

            # Open the stream only after the collector is ready.
            self.stream.start()

        except Exception as e:
            logger.warning(f"Error starting recording stream: {e}")
            self._request_recording_stop(recording_state)
            collector = recording_state.collector_thread
            if collector is not None and collector is not threading.current_thread():
                collector.join()
            with self._recording_state_lock:
                if self._recording_state is recording_state:
                    self.is_recording = False
                    self.stream = None
            # We can't easily propagate error to UI thread from here without the queue
            # But main.py checks self.is_recording state or we could print it.
            # Ideally the UI should know.
            logger.exception("Exception occurred")

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

    def stop_recording(self) -> np.ndarray:
        """Stop recording and return the audio data."""
        recording_state = self._recording_state
        if recording_state is None:
            self.is_recording = False
            self._stop_stream()
            chunks = self.recorded_audio
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

            chunks = recording_state.recorded_audio
            with self._recording_state_lock:
                if self._recording_state is recording_state:
                    self._recording_state = None
                    self.stream = None
                    self.is_recording = False

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
                
                logger.debug(f"Raw result: {res}")
                
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
                        logger.warning(f"Non-Chinese text detected: {text}, retrying with forced Chinese...")
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
                        logger.info(f"Retry result: {text}")
                    
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
            logger.info(f"STT warmup complete. Test result: '{result}'")
            return True
        except Exception as e:
            logger.warning(f"STT Warmup had issue (non-fatal): {e}")
            # Don't fail completely - the model may still work
            return True

    def cleanup(self):
        """Stop capture and release the loaded ASR model before application exit."""
        self.stop_recording()
        self.model_kwargs = {}
        if self.model is not None:
            try:
                del self.model
            except Exception as exc:
                logger.warning(f"Error releasing FunASR model: {exc}")
            self.model = None
        gc.collect()
        if torch.cuda.is_available():
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
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
