# TTS Service - CosyVoice3 Streaming Integration

import os
import sys
import re
import queue
import threading
import numpy as np
import torch
import torchaudio
try:
    import pyaudio
except ImportError:
    pyaudio = None
import time

# Add CosyVoice to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


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
            print(f"[WARNING] Temp cleanup failed: {e}")

    def load_model(self, progress_callback=None, **kwargs):
        """Load CosyVoice3 model and prepare voice cloning prompt."""
        if progress_callback:
            progress_callback("Loading CosyVoice3 model...")

        cosyvoice_dir = config.COSYVOICE_BASE_DIR
        model_dir = config.COSYVOICE_MODEL_PATH

        print(f"[INFO] Loading CosyVoice3 from: {model_dir}")
        sys.path.insert(0, cosyvoice_dir)

        from cosyvoice.cli.cosyvoice import AutoModel
        self.model = AutoModel(model_dir=model_dir)
        self.sample_rate = self.model.sample_rate

        # Prepare voice cloning prompt
        self.prompt_wav = config.VOICE_PROMPT_PATH
        self.prompt_text = config.VOICE_PROMPT_TEXT

        if self.prompt_wav and os.path.exists(self.prompt_wav):
            print(f"[INFO] Voice prompt: {self.prompt_wav}")
            # Cache speaker embedding for faster subsequent calls
            try:
                self.model.add_zero_shot_spk(
                    self.prompt_text, self.prompt_wav, 'default_speaker'
                )
                self._use_cached_speaker = True
                print("[INFO] Speaker embedding cached as 'default_speaker'")
            except Exception as e:
                print(f"[WARNING] Failed to cache speaker: {e}")
                self._use_cached_speaker = False
        else:
            print(f"[WARNING] Voice prompt not found: {self.prompt_wav}")
            self._use_cached_speaker = False

        # Initialize PyAudio
        if pyaudio:
            self.pyaudio = pyaudio.PyAudio()

        if progress_callback:
            progress_callback("CosyVoice3 loaded!")

        return True

    def warmup(self):
        """Warmup the model with a short generation."""
        print("[INFO] Warming up CosyVoice3...")
        try:
            audio = self.generate("你好，很高兴认识你。")
            if audio is not None and len(audio) > 0:
                print(f"[INFO] CosyVoice3 warmup successful. Generated {len(audio)} samples.")
            else:
                print("[WARNING] Warmup generated empty audio, continuing...")
            return True
        except Exception as e:
            print(f"[ERROR] CosyVoice3 warmup failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ==================== Text Preprocessing ====================

    def _preprocess_text(self, text: str) -> str:
        """Map FireRedTTS2-style tags to CosyVoice native tags and clean up."""
        if not text:
            return ""

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

    def _playback_worker(self, playback_queue, stream, stop_event, pre_buffer=5):
        """Background thread that plays audio chunks from the queue."""
        buffered_chunks = []
        first_chunk = True
        try:
            while not stop_event.is_set():
                try:
                    chunk = playback_queue.get(timeout=0.1)
                    if chunk is None:
                        break

                    if first_chunk:
                        buffered_chunks.append(chunk)
                        if len(buffered_chunks) >= pre_buffer:
                            for c in buffered_chunks:
                                if stream and stream.is_active():
                                    stream.write(c.tobytes())
                            buffered_chunks = []
                            first_chunk = False
                        continue

                    if stream and stream.is_active():
                        stream.write(chunk.tobytes())

                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[ERROR] Playback worker error: {e}")
                    break

            # Flush remaining buffered chunks
            if buffered_chunks:
                for c in buffered_chunks:
                    if stream and stream.is_active():
                        stream.write(c.tobytes())

        finally:
            print("[DEBUG] Playback worker finished")

    def generate_and_play(self, text: str, **kwargs):
        """Generate speech with CosyVoice3 and play in real-time (streaming)."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        print(f"[DEBUG] generate_and_play called with text length: {len(text)}")
        self.is_playing = True

        clean_text = self._preprocess_text(text)
        if not clean_text:
            print("[WARNING] Empty text after preprocessing")
            self.is_playing = False
            return

        playback_queue = queue.Queue(maxsize=200)
        stop_event = threading.Event()

        p = None
        stream = None

        try:
            if pyaudio is None:
                raise RuntimeError("PyAudio not installed. Cannot play audio.")
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=2048
            )

            # Start playback thread
            playback_thread = threading.Thread(
                target=self._playback_worker,
                args=(playback_queue, stream, stop_event),
                daemon=True
            )
            playback_thread.start()

            # CosyVoice streaming synthesis
            kwargs_gen = dict(
                tts_text=clean_text,
                stream=True,
            )
            if self._use_cached_speaker:
                kwargs_gen['zero_shot_spk_id'] = 'default_speaker'
            else:
                kwargs_gen['prompt_text'] = self.prompt_text or ''
                kwargs_gen['prompt_wav'] = self.prompt_wav or ''

            for chunk in self.model.inference_zero_shot(**kwargs_gen):
                if not self.is_playing:
                    print("[DEBUG] Playback interrupted.")
                    break

                audio_np = chunk['tts_speech'].squeeze().float().cpu().numpy().astype(np.float32)
                if audio_np.ndim == 0 or len(audio_np) == 0:
                    continue
                playback_queue.put(audio_np)

            # Signal end
            playback_queue.put(None)
            playback_thread.join(timeout=10)

        except Exception as e:
            print(f"[ERROR] generate_and_play failed: {e}")
            import traceback
            traceback.print_exc()

        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            if p:
                p.terminate()
            self.is_playing = False

    def generate(self, text: str, **kwargs) -> np.ndarray:
        """Generate speech without playing (for saving)."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        clean_text = self._preprocess_text(text)
        if not clean_text:
            return np.array([])

        try:
            kwargs_gen = dict(
                tts_text=clean_text,
                stream=False,
            )
            if self._use_cached_speaker:
                kwargs_gen['zero_shot_spk_id'] = 'default_speaker'
            else:
                kwargs_gen['prompt_text'] = self.prompt_text or ''
                kwargs_gen['prompt_wav'] = self.prompt_wav or ''

            audio_chunks = []
            for chunk in self.model.inference_zero_shot(**kwargs_gen):
                audio_np = chunk['tts_speech'].squeeze().float().cpu().numpy().astype(np.float32)
                if audio_np.ndim > 0 and len(audio_np) > 0:
                    audio_chunks.append(audio_np)

            if audio_chunks:
                return np.concatenate(audio_chunks)
            return np.array([])

        except Exception as e:
            print(f"[ERROR] CosyVoice3 generate failed: {e}")
            import traceback
            traceback.print_exc()
            return np.array([])

    def stop_playing(self):
        """Stop audio playback."""
        self.is_playing = False

    def play_audio(self, audio: np.ndarray):
        """Play pre-generated audio data synchronously."""
        if audio is None or len(audio) == 0:
            print("[WARNING] play_audio called with empty audio data")
            return

        print(f"[DEBUG] play_audio called with {len(audio)} samples")
        self.is_playing = True

        p = None
        stream = None

        try:
            if pyaudio is None:
                raise RuntimeError("PyAudio not installed. Cannot play audio.")
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=2048
            )

            chunk_size = self.sample_rate  # 1-second chunks
            for i in range(0, len(audio), chunk_size):
                if not self.is_playing:
                    break
                chunk = audio[i:i + chunk_size]
                stream.write(chunk.astype(np.float32).tobytes())

        except Exception as e:
            print(f"[ERROR] play_audio error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            if p:
                p.terminate()
            self.is_playing = False

    def save_audio(self, audio: np.ndarray, filepath: str):
        """Save audio to file."""
        if audio.size == 0:
            return
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)
        torchaudio.save(filepath, audio_tensor, self.sample_rate)

    def cleanup(self):
        """Clean up resources."""
        if self.stream:
            self.stream.close()
        if self.pyaudio:
            self.pyaudio.terminate()


# Singleton
_tts_service = None


def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
