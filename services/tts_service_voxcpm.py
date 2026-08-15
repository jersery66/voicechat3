import os
import sys
import re
import queue
import threading
import numpy as np
import torch
import torchaudio
import sounddevice as sd
import time
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from adapters.tts_results import PlaybackResult, PlaybackStatus
from services.logger import get_logger

logger = get_logger(__name__)


VOXCPM_MIN_GPU_MEMORY_GB = 8
VOXCPM_PLAYBACK_BUFFER_SECONDS = 120


@dataclass
class _PlaybackState:
    """Request-local cancellation and resource ownership for one playback."""

    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    stream: Optional[Any] = None
    generator: Optional[Any] = None
    stream_thread: Optional[threading.Thread] = None


class TTSService:
    def __init__(self):
        self.model = None
        self.sample_rate = None
        self.prompt_cache = None
        self.is_playing = False
        self._play_lock = __import__('threading').Lock()
        self._active_playback: Optional[_PlaybackState] = None
        self._active_playback_lock = threading.RLock()
        self.temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_audio")
        os.makedirs(self.temp_dir, exist_ok=True)
        self._cleanup_old_temps(max_age_seconds=3600)

    def _cleanup_old_temps(self, max_age_seconds: int = 3600):
        try:
            now = time.time()
            for f in os.listdir(self.temp_dir):
                fp = os.path.join(self.temp_dir, f)
                if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > max_age_seconds:
                    os.remove(fp)
        except Exception as e:
            logger.warning(f"Temp cleanup failed: {e}")

    @staticmethod
    def get_load_blocker() -> str | None:
        """Return why VoxCPM2 must not be loaded on this GPU, if applicable.

        VoxCPM2's local weights occupy over 4 GB before allocator overhead and
        AudioVAE buffers.  On 6 GB cards its constructor can terminate the
        Python process with a native access violation instead of raising a
        recoverable CUDA OOM exception.
        """
        if not torch.cuda.is_available():
            return None
        total_bytes = torch.cuda.get_device_properties(0).total_memory
        total_gb = total_bytes / 1024 ** 3
        if total_gb < VOXCPM_MIN_GPU_MEMORY_GB:
            return (
                f"VoxCPM2 requires at least {VOXCPM_MIN_GPU_MEMORY_GB}GB GPU memory; "
                f"detected {total_gb:.0f}GB. TTS is disabled to prevent a native crash."
            )
        return None

    def _download_model(self) -> str:
        voxcpm_cache_dir = os.path.join(config.PROGRAM_ROOT, "models", "VoxCPM2")
        if os.path.isdir(voxcpm_cache_dir) and os.path.exists(os.path.join(voxcpm_cache_dir, "config.json")):
            logger.info(f"Found cached VoxCPM2 at: {voxcpm_cache_dir}")
            return voxcpm_cache_dir

        logger.info("Downloading VoxCPM2 from ModelScope...")
        try:
            from modelscope import snapshot_download
            voxcpm_cache_dir = snapshot_download(
                "OpenBMB/VoxCPM2",
                local_dir=voxcpm_cache_dir,
            )
            logger.info(f"VoxCPM2 downloaded to: {voxcpm_cache_dir}")
            return voxcpm_cache_dir
        except Exception as e:
            logger.warning(f"ModelScope download failed: {e}")

        logger.info("Trying HuggingFace Hub as fallback...")
        try:
            from huggingface_hub import snapshot_download as hf_download
            voxcpm_cache_dir = hf_download("openbmb/VoxCPM2")
            logger.info(f"VoxCPM2 downloaded from HF to: {voxcpm_cache_dir}")
            return voxcpm_cache_dir
        except Exception as e:
            raise RuntimeError(f"Failed to download VoxCPM2 from both ModelScope and HuggingFace: {e}")

    def load_model(self, progress_callback=None, **kwargs):
        if self.model is not None:
            logger.info("VoxCPM2 model already loaded, skipping re-load.")
            return True
        blocker = self.get_load_blocker()
        if blocker:
            raise RuntimeError(blocker)
        if progress_callback:
            progress_callback("Loading VoxCPM2 model...")

        voxcpm_path = config.VOXCPM_MODEL_PATH
        prompt_wav = config.VOICE_PROMPT_PATH
        prompt_text = config.VOICE_PROMPT_TEXT

        logger.info(f"Loading VoxCPM2 from: {voxcpm_path or 'will auto-download'}")

        from voxcpm import VoxCPM

        try:
            if voxcpm_path and os.path.isdir(voxcpm_path):
                self.model = VoxCPM(
                    voxcpm_model_path=voxcpm_path,
                    zipenhancer_model_path=None,
                    enable_denoiser=False,
                    optimize=False,
                )
            else:
                voxcpm_path = self._download_model()
                self.model = VoxCPM(
                    voxcpm_model_path=voxcpm_path,
                    zipenhancer_model_path=None,
                    enable_denoiser=False,
                    optimize=False,
                )

            self.sample_rate = self.model.tts_model.sample_rate
        except Exception:
            logger.error("VoxCPM2 model load failed, rolling back partial resources.")
            self.unload_model()
            raise

        if prompt_wav and os.path.exists(prompt_wav):
            logger.info(f"Voice prompt: {prompt_wav}")
            try:
                self.prompt_cache = self.model.tts_model.build_prompt_cache(
                    prompt_text=prompt_text or "",
                    prompt_wav_path=prompt_wav,
                    reference_wav_path=prompt_wav,
                )
                logger.info("Voice prompt cache built for VoxCPM2 ultimate cloning")
            except Exception as e:
                logger.warning(f"Failed to build prompt cache: {e}")
                self.prompt_cache = None
        else:
            logger.warning(f"Voice prompt not found: {prompt_wav}")
            self.prompt_cache = None

        if progress_callback:
            progress_callback("VoxCPM2 loaded!")

        return True

    def unload_model(self):
        """Release the VoxCPM2 model and free GPU memory."""
        if self.model is not None:
            logger.info("Unloading VoxCPM2 model...")
            try:
                del self.model
            except Exception as e:
                logger.warning(f"Error deleting VoxCPM2 model: {e}")
            self.model = None
        self.prompt_cache = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return True

    def warmup(self):
        logger.info("Warming up VoxCPM2...")
        try:
            audio = self.generate("你好，很高兴认识你。")
            try:
                sample_count = int(np.asarray(audio).size) if audio is not None else 0
            except Exception:
                sample_count = 0
            if sample_count <= 0:
                logger.warning("VoxCPM2 warmup produced no usable audio; TTS is unavailable.")
                return False
            logger.info(f"VoxCPM2 warmup successful. Generated {sample_count} samples.")
            return True
        except Exception as e:
            logger.error(f"VoxCPM2 warmup failed: {e}")
            logger.exception("Exception occurred")
            return False

    def _preprocess_text(self, text: str) -> str:
        if not text:
            return ""

        text = re.sub(r'(\d+)\s*[-~—–]\s*(\d+)', r'\1到\2', text)

        text = re.sub(r'<\|emotion_\w+\|>', '', text)

        text = re.sub(r'\[(?:breath|laughter)\]', '', text)
        text = re.sub(r'<\|breath\|>', '', text)
        text = re.sub(r'<\|quick_breath\|>', '', text)
        text = re.sub(r'<\|sigh\|>', '', text)
        text = re.sub(r'<\|hem\|>', '', text)
        text = re.sub(r'<\|laugh_speak\|>(.*?)<\|/laugh_speak\|>', r'\1', text)

        text = re.sub(r'<\|[^>]+\|>', '', text)

        text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
        text = re.sub(r'\[END_[A-Z_]+\]', '', text)
        text = re.sub(r'【.*?】', '', text)

        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def _clear_active_playback(self, state: _PlaybackState) -> None:
        """Clear the active reference only when it still points to ``state``."""

        with self._active_playback_lock:
            if self._active_playback is state:
                self._active_playback = None

    def _publish_stream(self, state: _PlaybackState, stream: Any) -> bool:
        """Publish an output stream or abort/close it if cancellation won the race."""

        with self._active_playback_lock:
            accepted = (
                self._active_playback is state
                and not state.cancel_event.is_set()
            )
            if accepted:
                state.stream = stream
        if accepted:
            return True

        self._abort_stream(stream)
        self._close_stream(stream)
        return False

    def _clear_stream(self, state: _PlaybackState, stream: Any) -> None:
        with self._active_playback_lock:
            if state.stream is stream:
                state.stream = None

    def _publish_generator(self, state: _PlaybackState, generator: Any) -> bool:
        """Publish a provider generator unless this request is already stale."""

        with self._active_playback_lock:
            accepted = (
                self._active_playback is state
                and not state.cancel_event.is_set()
            )
            if accepted:
                state.generator = generator
        if accepted:
            return True

        self._close_generator(generator)
        return False

    def _clear_generator(self, state: _PlaybackState, generator: Any) -> None:
        with self._active_playback_lock:
            if state.generator is generator:
                state.generator = None

    @staticmethod
    def _abort_stream(stream: Any) -> None:
        abort = getattr(stream, "abort", None)
        if not callable(abort):
            return
        try:
            abort()
        except Exception as exc:
            logger.warning("VoxCPM2 stream abort failed: %s", exc)

    @staticmethod
    def _close_stream(stream: Any) -> None:
        close = getattr(stream, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as exc:
            logger.warning("VoxCPM2 stream close failed: %s", exc)

    @staticmethod
    def _close_generator(generator: Any) -> None:
        close = getattr(generator, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as exc:
            logger.warning("VoxCPM2 generator close failed: %s", exc)

    def generate_and_play(self, text: str, **kwargs) -> PlaybackResult:
        # Cancel any currently active request before waiting on the serial
        # playback lock. ``stop_playing`` intentionally never acquires this
        # lock, so delivery cancellation cannot deadlock a producer.
        self.stop_playing()
        with self._play_lock:
            return self._generate_and_play_inner(text, **kwargs)

    def _generate_and_play_inner(self, text: str, **kwargs) -> PlaybackResult:
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        logger.debug(f"generate_and_play called with text length: {len(text)}")
        self.is_playing = True
        state = _PlaybackState()
        with self._active_playback_lock:
            self._active_playback = state

        clean_text = self._preprocess_text(text)
        if not clean_text:
            logger.warning("Empty text after preprocessing")
            with self._active_playback_lock:
                if self._active_playback is state:
                    self._active_playback = None
                    self.is_playing = False
                state.done_event.set()
            return PlaybackResult(PlaybackStatus.FAILED, "empty_text")

        cfg_value = kwargs.get("cfg_value", config.VOXCPM_CFG_VALUE)
        inference_timesteps = kwargs.get("inference_timesteps", config.VOXCPM_INFERENCE_TIMESTEPS)

        # Streaming playback via sd.OutputStream callback.
        # A single PortAudio stream stays open; the audio callback reads
        # continuously from a shared buffer. The producer appends chunks
        # as they are generated. No open/close overhead between batches.
        import threading as _threading

        buf_len = int(self.sample_rate * VOXCPM_PLAYBACK_BUFFER_SECONDS)
        buf = np.zeros(buf_len, dtype=np.float32)
        write_pos = 0
        read_pos = 0
        lock = _threading.Lock()
        buffer_condition = _threading.Condition(lock)
        done_flag = _threading.Event()
        worker_failed_event = _threading.Event()
        started = _threading.Event()
        min_prebuffer = int(self.sample_rate * 0.8)
        generated_samples = 0
        generation_error = None
        worker_error = None

        def _callback(outdata, frames, time_info, status):
            nonlocal read_pos
            if status:
                logger.debug(f"OutputStream: {status}")
            if state.cancel_event.is_set():
                outdata[:, 0] = 0
                raise sd.CallbackStop
            with buffer_condition:
                avail = write_pos - read_pos
                if avail <= 0:
                    if done_flag.is_set():
                        raise sd.CallbackStop
                    outdata[:, 0] = 0
                    return
                n = min(frames, avail)
                start = read_pos % buf_len
                if start + n <= buf_len:
                    outdata[:n, 0] = buf[start:start + n]
                else:
                    first = buf_len - start
                    outdata[:first, 0] = buf[start:]
                    outdata[first:n, 0] = buf[:n - first]
                if n < frames:
                    outdata[n:, 0] = 0
                read_pos += n
                buffer_condition.notify_all()

        def _append_audio_bounded(audio_np) -> bool:
            """Append generated samples without overwriting unread audio."""

            nonlocal write_pos, generated_samples
            offset = 0
            total = len(audio_np)
            while offset < total:
                if state.cancel_event.is_set() or worker_failed_event.is_set():
                    return False
                with buffer_condition:
                    while True:
                        if state.cancel_event.is_set() or worker_failed_event.is_set():
                            return False
                        used = write_pos - read_pos
                        free = buf_len - used
                        if free > 0:
                            break
                        # Cancellation is request-local and stop_playing cannot
                        # safely acquire this lock. A bounded wait keeps the
                        # producer responsive while the callback makes space.
                        buffer_condition.wait(timeout=0.05)
                    n = min(total - offset, free)
                    start = write_pos % buf_len
                    if start + n <= buf_len:
                        buf[start:start + n] = audio_np[offset:offset + n]
                    else:
                        first = buf_len - start
                        buf[start:] = audio_np[offset:offset + first]
                        buf[:n - first] = audio_np[offset + first:offset + n]
                    write_pos += n
                    generated_samples += n
                    offset += n
            return True

        def _stream_worker():
            nonlocal worker_error
            try:
                # Wait for minimum pre-buffer before opening the stream
                while not state.cancel_event.is_set():
                    with buffer_condition:
                        if write_pos >= min_prebuffer:
                            break
                    if done_flag.is_set():
                        break
                    state.cancel_event.wait(0.01)
                if state.cancel_event.is_set():
                    return
                started.set()
                stream = sd.OutputStream(
                    samplerate=self.sample_rate, channels=1,
                    dtype='float32', blocksize=0, callback=_callback,
                )
                if not self._publish_stream(state, stream):
                    return
                entered = False
                try:
                    with stream:
                        entered = True
                        while not state.cancel_event.is_set():
                            with lock:
                                if done_flag.is_set() and write_pos <= read_pos:
                                    break
                            state.cancel_event.wait(0.1)
                finally:
                    self._clear_stream(state, stream)
                    if not entered:
                        self._close_stream(stream)
            except Exception as exc:
                worker_error = exc
                worker_failed_event.set()
                with buffer_condition:
                    buffer_condition.notify_all()
                logger.error("VoxCPM2 output worker failed: %s", exc)
                logger.exception("VoxCPM2 output worker exception")

        stream_thread = _threading.Thread(target=_stream_worker, daemon=True)
        state.stream_thread = stream_thread
        stream_thread.start()

        gen = None
        try:
            if self.prompt_cache is not None:
                gen = self.model.tts_model._generate_with_prompt_cache(
                    target_text=clean_text,
                    prompt_cache=self.prompt_cache,
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                    streaming=True,
                )
                if self._publish_generator(state, gen):
                    for wav, _, _ in gen:
                        if state.cancel_event.is_set():
                            break
                        audio_np = wav.squeeze(0).cpu().numpy().astype(np.float32)
                        if audio_np.ndim == 0 or len(audio_np) == 0:
                            continue
                        if not _append_audio_bounded(audio_np):
                            break
                else:
                    # _publish_generator already closed a stale generator;
                    # do not close the same object again in the finalizer.
                    gen = None
            else:
                gen = self.model.generate_streaming(
                    text=clean_text,
                    reference_wav_path=config.VOICE_PROMPT_PATH,
                    prompt_wav_path=config.VOICE_PROMPT_PATH,
                    prompt_text=config.VOICE_PROMPT_TEXT or "",
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                )
                if self._publish_generator(state, gen):
                    for chunk in gen:
                        if state.cancel_event.is_set():
                            break
                        audio_np = chunk.astype(np.float32)
                        if audio_np.ndim == 0 or len(audio_np) == 0:
                            continue
                        if not _append_audio_bounded(audio_np):
                            break
                else:
                    gen = None
        except Exception as e:
            generation_error = e
            logger.error(f"generate_and_play generation error: {e}")
            logger.exception("VoxCPM2 generation exception")
        finally:
            done_flag.set()
            stream_thread.join(timeout=60)
            worker_timed_out = stream_thread.is_alive()
            if gen is not None:
                self._close_generator(gen)
                self._clear_generator(state, gen)
            with self._active_playback_lock:
                cancelled = state.cancel_event.is_set()
                if self._active_playback is state:
                    self._active_playback = None
                    self.is_playing = False
                state.done_event.set()

        if cancelled:
            return PlaybackResult(PlaybackStatus.CANCELLED, "stopped")
        if generation_error is not None:
            return PlaybackResult(
                PlaybackStatus.FAILED,
                f"generation_error:{type(generation_error).__name__}",
            )
        if worker_error is not None:
            return PlaybackResult(
                PlaybackStatus.FAILED,
                f"output_worker_error:{type(worker_error).__name__}",
            )
        if worker_timed_out:
            return PlaybackResult(PlaybackStatus.FAILED, "output_worker_timeout")
        if generated_samples <= 0:
            return PlaybackResult(PlaybackStatus.FAILED, "no_audio")
        return PlaybackResult(PlaybackStatus.COMPLETED)

    def generate(self, text: str, **kwargs) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        clean_text = self._preprocess_text(text)
        if not clean_text:
            return np.array([])

        cfg_value = kwargs.get("cfg_value", config.VOXCPM_CFG_VALUE)
        inference_timesteps = kwargs.get("inference_timesteps", config.VOXCPM_INFERENCE_TIMESTEPS)

        try:
            if self.prompt_cache is not None:
                gen_result = self.model.tts_model._generate_with_prompt_cache(
                    target_text=clean_text,
                    prompt_cache=self.prompt_cache,
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                    streaming=False,
                )
                wav, _, _ = next(gen_result)
                gen_result.close()
                return wav.squeeze(0).cpu().numpy().astype(np.float32)
            else:
                return self.model.generate(
                    text=clean_text,
                    reference_wav_path=config.VOICE_PROMPT_PATH,
                    prompt_wav_path=config.VOICE_PROMPT_PATH,
                    prompt_text=config.VOICE_PROMPT_TEXT or "",
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                )

        except Exception as e:
            logger.error(f"VoxCPM2 generate failed: {e}")
            logger.exception("Exception occurred")
            return np.array([])

    def stop_playing(self):
        """Cancel the active explicit OutputStream without taking _play_lock."""

        with self._active_playback_lock:
            state = self._active_playback
            if state is None:
                self.is_playing = False
                legacy_stream_only = True
            else:
                legacy_stream_only = False
            if state is not None and state.cancel_event.is_set():
                self.is_playing = False
                return
            if state is not None:
                state.cancel_event.set()
                stream = state.stream
                generator = state.generator
                self.is_playing = False
            else:
                stream = None
                generator = None

        # PortAudio calls are deliberately outside the state lock.  The
        # explicit OutputStream, not module-level sd.stop(), owns playback.
        if stream is not None:
            self._abort_stream(stream)
        if generator is not None:
            self._close_generator(generator)
        if legacy_stream_only:
            # Preserve the legacy ``sd.play`` convenience path.  This branch
            # is unreachable while an explicit request-local stream exists.
            try:
                sd.stop()
            except Exception as exc:
                logger.warning("Legacy VoxCPM stream stop failed: %s", exc)

    def play_audio(self, audio: np.ndarray):
        with self._play_lock:
            self.stop_playing()
            return self._play_audio_inner(audio)

    def _play_audio_inner(self, audio: np.ndarray):
        if audio is None or len(audio) == 0:
            logger.warning("play_audio called with empty audio data")
            return

        logger.debug(f"play_audio called with {len(audio)} samples")
        self.is_playing = True

        try:
            # Non-blocking: do NOT hold the calling (possibly GUI) thread.
            # This is the legacy convenience-stream path; it is intentionally
            # separate from explicit OutputStream cancellation above.
            sd.stop()
            sd.play(audio, samplerate=self.sample_rate)
        except Exception as e:
            logger.error(f"play_audio error: {e}")
            logger.exception("Exception occurred")
        finally:
            self.is_playing = False

    def save_audio(self, audio: np.ndarray, filepath: str):
        if audio.size == 0:
            return
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)
        torchaudio.save(filepath, audio_tensor, self.sample_rate)

    def cleanup(self):
        self.stop_playing()
        self.unload_model()


_tts_service = None


def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
