import os
import sys
import re
import numpy as np
import torch
import torchaudio
import sounddevice as sd
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from services.logger import get_logger

logger = get_logger(__name__)


class TTSService:
    def __init__(self):
        self.model = None
        self.sample_rate = None
        self.prompt_cache = None
        self.is_playing = False
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
        if progress_callback:
            progress_callback("Loading VoxCPM2 model...")

        voxcpm_path = config.VOXCPM_MODEL_PATH
        prompt_wav = config.VOICE_PROMPT_PATH
        prompt_text = config.VOICE_PROMPT_TEXT

        logger.info(f"Loading VoxCPM2 from: {voxcpm_path or 'will auto-download'}")

        from voxcpm import VoxCPM

        if voxcpm_path and os.path.isdir(voxcpm_path):
            self.model = VoxCPM(
                voxcpm_model_path=voxcpm_path,
                zipenhancer_model_path=None,
                enable_denoiser=False,
                optimize=True,
            )
        else:
            voxcpm_path = self._download_model()
            self.model = VoxCPM(
                voxcpm_model_path=voxcpm_path,
                zipenhancer_model_path=None,
                enable_denoiser=False,
                optimize=True,
            )

        self.sample_rate = self.model.tts_model.sample_rate

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

    def warmup(self):
        logger.info("Warming up VoxCPM2...")
        try:
            audio = self.generate("你好，很高兴认识你。")
            if audio is not None and len(audio) > 0:
                logger.info(f"VoxCPM2 warmup successful. Generated {len(audio)} samples.")
            else:
                logger.warning("Warmup generated empty audio, continuing...")
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

    def generate_and_play(self, text: str, **kwargs):
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        logger.debug(f"generate_and_play called with text length: {len(text)}")
        self.is_playing = True

        clean_text = self._preprocess_text(text)
        if not clean_text:
            logger.warning("Empty text after preprocessing")
            self.is_playing = False
            return

        try:
            cfg_value = kwargs.get("cfg_value", config.VOXCPM_CFG_VALUE)
            inference_timesteps = kwargs.get("inference_timesteps", config.VOXCPM_INFERENCE_TIMESTEPS)

            # Collect all generated audio into a single buffer, then play.
            # This avoids stutter from chunk-size mismatch in callback-based streaming.
            audio_parts = []

            if self.prompt_cache is not None:
                gen_result = self.model.tts_model._generate_with_prompt_cache(
                    target_text=clean_text,
                    prompt_cache=self.prompt_cache,
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                    streaming=True,
                )
                try:
                    for wav, _, _ in gen_result:
                        if not self.is_playing:
                            logger.debug("Generation interrupted.")
                            break
                        audio_np = wav.squeeze(0).cpu().numpy().astype(np.float32)
                        if audio_np.ndim == 0 or len(audio_np) == 0:
                            continue
                        audio_parts.append(audio_np)
                finally:
                    gen_result.close()
            else:
                for chunk in self.model.generate_streaming(
                    text=clean_text,
                    reference_wav_path=config.VOICE_PROMPT_PATH,
                    prompt_wav_path=config.VOICE_PROMPT_PATH,
                    prompt_text=config.VOICE_PROMPT_TEXT or "",
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                ):
                    if not self.is_playing:
                        logger.debug("Generation interrupted.")
                        break
                    audio_np = chunk.astype(np.float32)
                    if audio_np.ndim == 0 or len(audio_np) == 0:
                        continue
                    audio_parts.append(audio_np)

            if not audio_parts:
                logger.warning("No audio generated")
                return

            full_audio = np.concatenate(audio_parts)
            logger.debug(f"Generated {len(full_audio)} samples ({len(full_audio)/self.sample_rate:.1f}s)")

            # Play the complete audio
            sd.play(full_audio, samplerate=self.sample_rate, blocking=True)

        except Exception as e:
            logger.error(f"generate_and_play failed: {e}")
            logger.exception("Exception occurred")

        finally:
            self.is_playing = False

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
        self.is_playing = False

    def play_audio(self, audio: np.ndarray):
        if audio is None or len(audio) == 0:
            logger.warning("play_audio called with empty audio data")
            return

        logger.debug(f"play_audio called with {len(audio)} samples")
        self.is_playing = True

        try:
            sd.play(audio, samplerate=self.sample_rate, blocking=True)
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
        sd.stop()


_tts_service = None


def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
