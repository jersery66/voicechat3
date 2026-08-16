# TTS Service - CosyVoice3 Streaming Integration

import os
import sys
import re
import queue
import threading
import numpy as np
import torch
import torchaudio
from dataclasses import dataclass
from typing import Any
try:
    import pyaudio
except ImportError:
    pyaudio = None
import time

# Add CosyVoice to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from services.logger import get_logger

logger = get_logger(__name__)


@dataclass
class _CosyPlaybackState:
    """Request-local cancellation and worker ownership for compatibility TTS."""

    stop_event: threading.Event
    done_event: threading.Event
    playback_thread: threading.Thread | None = None
    stream: Any = None


class TTSService:
    """Text-to-Speech service using CosyVoice3 with streaming voice cloning."""

    def __init__(self):
        self.model = None
        self.sample_rate = None
        self.prompt_wav = None
        self.prompt_text = None
        self.is_playing = False
        self.pyaudio = None
        self.stream = None
        self._play_lock = threading.Lock()
        self._active_playback: _CosyPlaybackState | None = None
        self._active_playback_lock = threading.RLock()
        self.temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_audio")
        os.makedirs(self.temp_dir, exist_ok=True)
        self._cleanup_old_temps(max_age_seconds=3600)

    def _cleanup_old_temps(self, max_age_seconds: int = 3600):
        """Remove temp WAV files older than max_age_seconds."""
        try:
            now = time.time()
            for f in os.listdir(self.temp_dir):
                fp = os.path.join(self.temp_dir, f)
                if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > max_age_seconds:
                    os.remove(fp)
        except Exception as e:
            logger.warning(f"Temp cleanup failed: {e}")

    def load_model(self, progress_callback=None, **kwargs):
        """Load CosyVoice3 model and prepare voice cloning prompt."""
        if self.model is not None:
            logger.info("CosyVoice3 model already loaded, skipping re-load.")
            return True
        if progress_callback:
            progress_callback("Loading CosyVoice3 model...")

        cosyvoice_dir = config.COSYVOICE_BASE_DIR
        model_dir = config.COSYVOICE_MODEL_PATH

        logger.info(f"Loading CosyVoice3 from: {model_dir}")
        sys.path.insert(0, cosyvoice_dir)
        matcha_path = os.path.join(cosyvoice_dir, 'third_party', 'Matcha-TTS')
        if os.path.isdir(matcha_path) and matcha_path not in sys.path:
            sys.path.insert(0, matcha_path)

        from cosyvoice.cli.cosyvoice import AutoModel
        try:
            self.model = AutoModel(model_dir=model_dir)
            self.sample_rate = self.model.sample_rate
        except Exception:
            logger.error("CosyVoice3 model load failed, rolling back partial resources.")
            self.unload_model()
            raise

        # Prepare voice cloning prompt
        self.prompt_wav = config.VOICE_PROMPT_PATH
        self.prompt_text = config.VOICE_PROMPT_TEXT

        if self.prompt_wav and os.path.exists(self.prompt_wav):
            logger.info(f"Voice prompt: {self.prompt_wav}")
            # Cache speaker embedding for faster subsequent calls
            try:
                self.model.add_zero_shot_spk(
                    self.prompt_text, self.prompt_wav, 'default_speaker'
                )
                self._use_cached_speaker = True
                logger.info("Speaker embedding cached as 'default_speaker'")
            except Exception as e:
                logger.warning(f"Failed to cache speaker: {e}")
                self._use_cached_speaker = False
        else:
            logger.warning(f"Voice prompt not found: {self.prompt_wav}")
            self._use_cached_speaker = False

        # Initialize PyAudio
        if pyaudio:
            self.pyaudio = pyaudio.PyAudio()

        if progress_callback:
            progress_callback("CosyVoice3 loaded!")

        return True

    def unload_model(self):
        """Release the CosyVoice3 model and free GPU memory."""
        if self.model is not None:
            logger.info("Unloading CosyVoice3 model...")
            try:
                del self.model
            except Exception as e:
                logger.warning(f"Error deleting CosyVoice3 model: {e}")
            self.model = None
        self._use_cached_speaker = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return True

    def _ensure_playback_state(self) -> None:
        """Initialize request state for legacy ``__new__`` test doubles."""
        if not hasattr(self, "_active_playback"):
            self._active_playback = None
        if not hasattr(self, "_active_playback_lock"):
            self._active_playback_lock = threading.RLock()

    def _new_playback_state(self) -> _CosyPlaybackState:
        self._ensure_playback_state()
        return _CosyPlaybackState(threading.Event(), threading.Event())

    def _set_active_playback(self, state: _CosyPlaybackState) -> None:
        self._ensure_playback_state()
        with self._active_playback_lock:
            self._active_playback = state

    def _clear_active_playback(self, state: _CosyPlaybackState) -> bool:
        self._ensure_playback_state()
        with self._active_playback_lock:
            if self._active_playback is not state:
                return False
            self._active_playback = None
            return True

    def _close_stream(self, stream=None, *, state: _CosyPlaybackState | None = None):
        """Safely close one request's stream without touching a replacement."""
        self._ensure_playback_state()
        with self._active_playback_lock:
            if stream is None:
                if state is not None:
                    stream = state.stream
                else:
                    stream = getattr(self, "stream", None)
            if stream is None:
                return
            if state is not None and state.stream is stream:
                state.stream = None
            if getattr(self, "stream", None) is stream:
                self.stream = None
        if stream is not None:
            try:
                if getattr(stream, "is_active", lambda: False)():
                    stream.stop_stream()
                stream.close()
            except Exception as e:
                logger.warning(f"Error closing playback stream: {e}")
            self.stream = None

    def warmup(self):
        """Warmup the model with a short generation."""
        logger.info("Warming up CosyVoice3...")
        try:
            audio = self.generate("你好，很高兴认识你。")
            if audio is not None and len(audio) > 0:
                logger.info(f"CosyVoice3 warmup successful. Generated {len(audio)} samples.")
            else:
                logger.warning("Warmup generated empty audio, continuing...")
            return True
        except Exception as e:
            logger.error(f"CosyVoice3 warmup failed: {e}")
            logger.exception("Exception occurred")
            return False

    # ==================== Text Preprocessing ====================

    def _preprocess_text(self, text: str) -> str:
        """Map FireRedTTS2-style tags to CosyVoice native tags and clean up."""
        if not text:
            return ""

        # 0. Number range normalization: "8-10" → "8到10", "3~5" → "3到5"
        text = re.sub(r'(\d+)\s*[-~—–]\s*(\d+)', r'\1到\2', text)

        # 1. Remove emotion tags (not supported in zero-shot mode)
        text = re.sub(r'<\|emotion_\w+\|>', '', text)

        # 2. Map paralanguage tags to CosyVoice native [breath] / [laughter]
        text = text.replace('<|breath|>', '[breath]')
        text = text.replace('<|quick_breath|>', '[breath]')
        text = text.replace('<|sigh|>', '[breath]')
        text = text.replace('<|hem|>', '[breath]')
        text = re.sub(r'<\|laugh_speak\|>(.*?)<\|/laugh_speak\|>', r'[laughter]\1', text)

        # 3. Strip any remaining <|...|> tags
        text = re.sub(r'<\|[^>]+\|>', '', text)

        # 4. Strip LLM control tags
        text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
        text = re.sub(r'\[END_[A-Z_]+\]', '', text)
        text = re.sub(r'【.*?】', '', text)

        # 5. Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    # ==================== Streaming Playback ====================

    def _playback_worker(
        self,
        playback_queue,
        stream,
        stop_event,
        pre_buffer=5,
        state: _CosyPlaybackState | None = None,
    ):
        """Play queued chunks, flushing pre-buffer only on normal EOF."""
        buffered_chunks = []
        first_chunk = True
        normal_end = False
        try:
            while True:
                if stop_event.is_set():
                    break
                try:
                    chunk = playback_queue.get(timeout=0.1)
                    if chunk is None:
                        normal_end = True
                        break

                    if stop_event.is_set():
                        break
                    if first_chunk:
                        buffered_chunks.append(chunk)
                        if len(buffered_chunks) >= pre_buffer:
                            for c in buffered_chunks:
                                if stop_event.is_set():
                                    break
                                if stream and stream.is_active():
                                    stream.write(c.tobytes())
                            if stop_event.is_set():
                                break
                            buffered_chunks = []
                            first_chunk = False
                        continue

                    if not stop_event.is_set() and stream and stream.is_active():
                        stream.write(chunk.tobytes())

                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Playback worker error: {e}")
                    stop_event.set()
                    break

            # Only a provider EOF permits the unread pre-buffer to be played.
            if normal_end and not stop_event.is_set() and buffered_chunks:
                for c in buffered_chunks:
                    if stop_event.is_set():
                        break
                    if stream and stream.is_active():
                        stream.write(c.tobytes())

        finally:
            buffered_chunks.clear()
            stop_event.set()
            if state is not None:
                state.done_event.set()
            logger.debug("Playback worker finished")

    def _put_playback_chunk(self, playback_queue, chunk, stop_event, state=None) -> bool:
        """Admit one compatibility chunk without an uninterruptible put."""
        while not stop_event.is_set():
            if state is not None and state.done_event.is_set():
                return False
            try:
                playback_queue.put(chunk, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _build_synthesis_kwargs(self, clean_text: str, stream: bool) -> dict | None:
        """Build kwargs for model.inference_zero_shot. Returns None if no voice prompt available."""
        if self._use_cached_speaker and hasattr(self.model, 'inference_zero_shot'):
            return dict(
                tts_text=clean_text,
                prompt_text=self.prompt_text or '',
                prompt_wav=self.prompt_wav or '',
                stream=stream,
                zero_shot_spk_id='default_speaker',
            )
        if self.prompt_wav and os.path.exists(self.prompt_wav):
            return dict(
                tts_text=clean_text,
                prompt_text=self.prompt_text or '',
                prompt_wav=self.prompt_wav,
                stream=stream,
            )
        return None

    def generate_and_play(self, text: str, **kwargs):
        """Generate speech with CosyVoice3 and play in real-time (streaming)."""
        with self._play_lock:
            return self._generate_and_play_inner(text, **kwargs)

    def _generate_and_play_inner(self, text: str, **kwargs):
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        self._ensure_playback_state()
        logger.debug(f"generate_and_play called with text length: {len(text)}")
        self.is_playing = True

        clean_text = self._preprocess_text(text)
        if not clean_text:
            logger.warning("Empty text after preprocessing")
            self.is_playing = False
            return

        # Validate the prompt before opening audio resources or starting a
        # worker.  CosyVoice remains compatibility-only, so invalid requests
        # must fail closed without leaving a request-local worker behind.
        kwargs_gen = self._build_synthesis_kwargs(clean_text, stream=True)
        if kwargs_gen is None:
            logger.warning("No prompt_wav available, skipping TTS generation")
            self.is_playing = False
            return

        playback_queue = queue.Queue(maxsize=200)
        state = self._new_playback_state()
        self._set_active_playback(state)
        stream = None
        playback_thread = None
        normal_completion = False

        try:
            if not self.pyaudio:
                raise RuntimeError("PyAudio not initialized.")
            stream = self.pyaudio.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=2048
            )
            with self._active_playback_lock:
                if self._active_playback is state:
                    state.stream = stream
                    self.stream = stream

            if state.stop_event.is_set():
                return

            # Start playback thread
            playback_thread = threading.Thread(
                target=self._playback_worker,
                args=(playback_queue, stream, state.stop_event),
                kwargs={"state": state},
                daemon=True
            )
            state.playback_thread = playback_thread
            playback_thread.start()

            # CosyVoice streaming synthesis
            for chunk in self.model.inference_zero_shot(**kwargs_gen):
                if state.stop_event.is_set():
                    logger.debug("Playback interrupted.")
                    break
                audio_np = chunk['tts_speech'].squeeze().float().cpu().numpy().astype(np.float32)
                if audio_np.ndim == 0 or len(audio_np) == 0:
                    continue
                if not self._put_playback_chunk(
                    playback_queue,
                    audio_np,
                    state.stop_event,
                    state,
                ):
                    logger.warning("Playback queue admission stopped.")
                    break
            else:
                normal_completion = True

            if normal_completion and not state.stop_event.is_set():
                self._put_playback_chunk(
                    playback_queue,
                    None,
                    state.stop_event,
                    state,
                )
            else:
                state.stop_event.set()

        except Exception as e:
            logger.error(f"generate_and_play failed: {e}")
            logger.exception("Exception occurred")

        finally:
            if not normal_completion:
                state.stop_event.set()
            if playback_thread is not None and playback_thread is not threading.current_thread():
                playback_thread.join(timeout=2.0)
                if playback_thread.is_alive():
                    logger.warning("CosyVoice playback worker did not stop within timeout")
            self._close_stream(stream, state=state)
            self.is_playing = False
            self._clear_active_playback(state)

    def generate(self, text: str, **kwargs) -> np.ndarray:
        """Generate speech without playing (for saving)."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        clean_text = self._preprocess_text(text)
        if not clean_text:
            return np.array([])

        try:
            kwargs_gen = self._build_synthesis_kwargs(clean_text, stream=False)
            if kwargs_gen is None:
                logger.warning("No prompt_wav available for generate, returning empty audio")
                return np.array([])

            audio_chunks = []
            for chunk in self.model.inference_zero_shot(**kwargs_gen):
                audio_np = chunk['tts_speech'].squeeze().float().cpu().numpy().astype(np.float32)
                if audio_np.ndim > 0 and len(audio_np) > 0:
                    audio_chunks.append(audio_np)

            if audio_chunks:
                return np.concatenate(audio_chunks)
            return np.array([])

        except Exception as e:
            logger.error(f"CosyVoice3 generate failed: {e}")
            logger.exception("Exception occurred")
            return np.array([])

    def stop_playing(self):
        """Stop audio playback."""
        self.is_playing = False
        self._ensure_playback_state()
        with self._active_playback_lock:
            state = self._active_playback
            stream = state.stream if state is not None else None
            if state is not None:
                state.stop_event.set()
        if state is not None and stream is not None:
            self._close_stream(stream, state=state)

    def play_audio(self, audio: np.ndarray):
        """Play pre-generated audio data synchronously."""
        with self._play_lock:
            if audio is None or len(audio) == 0:
                logger.warning("play_audio called with empty audio data")
                return

            logger.debug(f"play_audio called with {len(audio)} samples")
            self.is_playing = True

            stream = None

            try:
                if not self.pyaudio:
                    raise RuntimeError("PyAudio not initialized.")
                stream = self.pyaudio.open(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=self.sample_rate,
                    output=True,
                    frames_per_buffer=2048
                )
                self.stream = stream

                chunk_size = self.sample_rate  # 1-second chunks
                for i in range(0, len(audio), chunk_size):
                    if not self.is_playing:
                        break
                    chunk = audio[i:i + chunk_size]
                    stream.write(chunk.astype(np.float32).tobytes())

            except Exception as e:
                logger.error(f"play_audio error: {e}")
                logger.exception("Exception occurred")

            finally:
                self._close_stream()
                self.is_playing = False

    def save_audio(self, audio: np.ndarray, filepath: str):
        """Save audio to file."""
        if audio.size == 0:
            return
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)
        torchaudio.save(filepath, audio_tensor, self.sample_rate)

    def cleanup(self):
        """Clean up resources."""
        self.stop_playing()
        self._ensure_playback_state()
        with self._active_playback_lock:
            state = self._active_playback
            playback_thread = state.playback_thread if state is not None else None
            stream = state.stream if state is not None else getattr(self, "stream", None)
        if playback_thread is not None and playback_thread is not threading.current_thread():
            playback_thread.join(timeout=2.0)
            if playback_thread.is_alive():
                logger.warning("CosyVoice cleanup found a live playback worker")
        self._close_stream(stream, state=state)
        if state is not None:
            self._clear_active_playback(state)
        if self.pyaudio:
            try:
                self.pyaudio.terminate()
            except Exception as e:
                logger.warning(f"Error terminating PyAudio: {e}")
            self.pyaudio = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


# Singleton
_tts_service = None


def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
