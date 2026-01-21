# TTS Service - FireRedTTS2 Streaming Integration

import os
import sys
import queue
import threading
import numpy as np
import torch
import torchaudio
import pyaudio
import soundfile as sf
import tempfile
import uuid

# Try importing librosa for robust audio loading
try:
    import librosa
except ImportError:
    librosa = None

# Add FireRedTTS2 to path
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(root_dir, "FireRedTTS2"))
try:
    from fireredtts2.fireredtts2 import FireRedTTS2_Stream
except ImportError:
    # Fallback or check if imported correctly
    print("Warning: could not import FireRedTTS2_Stream")

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from config import FIREREDTTS2_MODEL_PATH


class TTSService:
    """Text-to-Speech service using FireRedTTS2 with streaming support (Dialogue Mode)."""
    
    def __init__(self, model_path: str = FIREREDTTS2_MODEL_PATH, device: str = None):
        self.model_path = model_path
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if device:
            self.device = device
        self.model = None
        self.sample_rate = 24000
        self.is_playing = False
        self.pyaudio = None
        self.stream = None
        self.temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_audio")
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir, exist_ok=True)
        
    def load_model(self, progress_callback=None, **kwargs):
        """Load the FireRedTTS2 model in dialogue mode."""
        if progress_callback:
            progress_callback("Loading TTS model...")
            
        print(f"[DEBUG] Loading FireRedTTS2_Stream on {self.device} with gen_type='dialogue'")
        self.model = FireRedTTS2_Stream(
            pretrained_dir=self.model_path,
            gen_type="dialogue",
            device=self.device,
        )
            
        # Initialize PyAudio for playback
        self.pyaudio = pyaudio.PyAudio()
        
        if progress_callback:
            progress_callback("TTS model loaded!")
            
        return True

    def warmup(self):
        """Warmup the model by generating a short clip with full voice cloning pipeline.
        
        This ensures the prompt audio encoding is cached for subsequent generations,
        which fixes the issue where the first sentence doesn't use the cloned voice.
        """
        print("[INFO] Warming up TTS model with voice cloning pipeline...")
        try:
            # Use a slightly longer text to ensure proper warmup
            warmup_text = "你好，很高兴认识你。"
            
            # Generate using the full pipeline (with prompt audio)
            # This ensures the voice prompt is encoded and cached
            audio = self.generate(warmup_text)
            
            if audio is not None and len(audio) > 0:
                print(f"[INFO] TTS warmup successful. Generated {len(audio)} samples.")
            else:
                print("[WARNING] TTS warmup generated empty audio, but continuing...")
            
            # Run a second warmup pass to ensure cache is properly populated
            audio2 = self.generate("好的，我明白了。")
            if audio2 is not None and len(audio2) > 0:
                print(f"[INFO] TTS second warmup pass successful.")
            
            print("[INFO] TTS model warmed up with voice cloning.")
            return True
        except Exception as e:
            print(f"[ERROR] TTS Warmup failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _prepare_prompt(self, path):
        """Preprocess prompt audio path. Convert to WAV if needed (e.g. FLAC issues)."""
        if not path or not os.path.exists(path):
            return path
            
        # If librosa exists, try to load and convert to WAV to ensure compatibility
        # especially if soundfile fails on FLAC directly.
        if librosa:
            try:
                ext = os.path.splitext(path)[1].lower()
                # Always convert FLAC to safe WAV using librosa -> soundfile write
                if ext == '.flac':
                    print(f"[DEBUG] Converting FLAC prompt to WAV: {path}")
                    y, sr = librosa.load(path, sr=None)
                    
                    temp_name = f"prompt_{uuid.uuid4().hex}.wav"
                    temp_path = os.path.join(self.temp_dir, temp_name)
                    
                    sf.write(temp_path, y, sr)
                    print(f"[DEBUG] Saved temp prompt to currently: {temp_path}")
                    return temp_path
            except Exception as e:
                print(f"[WARNING] Failed to convert prompt with librosa: {e}")
                
        return path
    
    def _playback_worker(self, playback_queue, stream, stop_event):
        """Consumer thread for audio playback."""
        first_chunk = True
        buffer_threshold = 5  # Wait for 5 chunks before starting playback (increased for stability)
        buffered_chunks = []
        
        try:
            while not stop_event.is_set():
                try:
                    # Wait for audio data with a timeout to check stop_event
                    chunk = playback_queue.get(timeout=0.5)  # Increased timeout for slower generation
                    if chunk is None: # Sentinel value
                        # Play any remaining buffered chunks
                        for c in buffered_chunks:
                             if stream and stream.is_active():
                                stream.write(c.tobytes())
                        break
                    
                    if first_chunk:
                        buffered_chunks.append(chunk)
                        if len(buffered_chunks) >= buffer_threshold:
                            print(f"[DEBUG] Pre-buffer filled ({len(buffered_chunks)} chunks). Starting playback.")
                            for c in buffered_chunks:
                                if stream and stream.is_active():
                                    stream.write(c.tobytes())
                            buffered_chunks = []
                            first_chunk = False
                        playback_queue.task_done()
                        continue
                    
                    # Normal playback
                    if stream and stream.is_active():
                        stream.write(chunk.tobytes())
                    
                    playback_queue.task_done()
                except queue.Empty:
                    # If queue is empty but we have buffered chunks (timeout reached without filling threshold), flush them
                    if first_chunk and buffered_chunks:
                        print(f"[DEBUG] Pre-buffer timeout. Flushing {len(buffered_chunks)} chunks.")
                        for c in buffered_chunks:
                             if stream and stream.is_active():
                                stream.write(c.tobytes())
                        buffered_chunks = []
                        first_chunk = False
                    continue
                except Exception as e:
                    print(f"[ERROR] Playback worker error: {e}")
                    break
        finally:
            print("[DEBUG] Playback worker finished")

    def generate_and_play(self, text: str, temperature: float = 0.5, topk: int = 10):
        """Generate speech and play it in real-time (streaming) using buffered playback."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")
            
        print(f"[DEBUG] generate_and_play called with text length: {len(text)}")
        self.is_playing = True
        all_audio = []
        
        # Audio queue for buffering
        playback_queue = queue.Queue(maxsize=200) 
        stop_event = threading.Event()
        
        # Initialize PyAudio locally
        p = None
        stream = None
        
        try:
            print("[DEBUG] Initializing PyAudio locally...")
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=2048  # Increased for smoother playback
            )
            
            # Start Playback Thread
            playback_thread = threading.Thread(
                target=self._playback_worker,
                args=(playback_queue, stream, stop_event),
                daemon=True
            )
            playback_thread.start()

            # Prepare inputs for dialogue mode (lists)
            # Auto-add speaker tag [S1] if missing (required by FireRedTTS2 dialogue mode)
            if not text.lstrip().startswith("[S"):
                print(f"[DEBUG] Prepending [S1] tag to text")
                text = f"[S1]{text}"
                
            text_list = [text] 
            
            # Prepare prompts
            cfg_wav = getattr(config, 'VOICE_PROMPT_PATH', None)
            cfg_text = getattr(config, 'VOICE_PROMPT_TEXT', None)
            
            prompt_wav_list = []
            prompt_text_list = []
            
            if cfg_wav:
                 if isinstance(cfg_wav, list):
                     prompt_wav_list = [self._prepare_prompt(p) for p in cfg_wav]
                     prompt_text_list = cfg_text if isinstance(cfg_text, list) else [str(cfg_text)] * len(cfg_wav)
                 elif isinstance(cfg_wav, str) and os.path.exists(cfg_wav):
                     prompt_wav_list.append(self._prepare_prompt(cfg_wav))
                     if cfg_text:
                         prompt_text_list.append(str(cfg_text))
                     else:
                         prompt_text_list.append("")
            
            print(f"[DEBUG] Inputs - Text: {len(text_list)} | PromptWav: {len(prompt_wav_list)} items")
            print(f"[DEBUG] Text Content: {text_list}")

            try:
                # Streaming generation
                audio_generator = self.model.generate_dialogue(
                    text_list=text_list,
                    prompt_wav_list=prompt_wav_list if prompt_wav_list else None,
                    prompt_text_list=prompt_text_list if prompt_text_list else None,
                    temperature=temperature,
                    topk=topk
                )
                
                chunk_count = 0
                for audio_chunk in audio_generator:
                    if not self.is_playing:
                        print("[DEBUG] Playback interrupted.")
                        break
                        
                    chunk_np = audio_chunk.squeeze().float().cpu().numpy().astype(np.float32)
                    
                    # Normalize if needed
                    max_amp = np.abs(chunk_np).max()
                    if max_amp > 1e-6:
                         pass

                    all_audio.append(chunk_np)
                    
                    # Add to playback queue
                    playback_queue.put(chunk_np)
                    chunk_count += 1
                
                print(f"\n[DEBUG] Streaming finished. Chunks processed: {chunk_count}")
                    
            except Exception as e:
                print(f"[ERROR] Logic error in generate_and_play: {e}")
                import traceback
                traceback.print_exc()
            
            # Signal playback to finish
            playback_queue.put(None)
            
            # Wait for playback to finish
            if chunk_count > 0:
                 playback_thread.join()
                
        except Exception as e:
            print(f"[ERROR] Setup error: {e}")
            
        finally:
            stop_event.set()
            if stream:
                stream.stop_stream()
                stream.close()
            if p:
                p.terminate()
            self.is_playing = False
            
        # Return concatenated audio for saving
        if all_audio:
            return np.concatenate(all_audio)
        return np.array([])
    
    def generate(self, text: str, temperature: float = 0.9, topk: int = 30) -> np.ndarray:
        """Generate speech without playing (for saving)."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")
            
        # Auto-add speaker tag [S1] if missing
        if not text.lstrip().startswith("[S"):
            text = f"[S1]{text}"

        # Prepare inputs
        text_list = [text]
        prompt_wav_list = []
        prompt_text_list = []
        
        cfg_wav = getattr(config, 'VOICE_PROMPT_PATH', None)
        cfg_text = getattr(config, 'VOICE_PROMPT_TEXT', None)

        if cfg_wav:
             if isinstance(cfg_wav, list):
                 prompt_wav_list = [self._prepare_prompt(p) for p in cfg_wav]
                 prompt_text_list = cfg_text if isinstance(cfg_text, list) else [str(cfg_text)] * len(cfg_wav)
             elif isinstance(cfg_wav, str) and os.path.exists(cfg_wav):
                 prompt_wav_list.append(self._prepare_prompt(cfg_wav))
                 if cfg_text:
                     prompt_text_list.append(str(cfg_text))
                 else:
                     prompt_text_list.append("")
        
        # Determine device
        device = self.device

        all_audio_tensors = []
        audio_generator = self.model.generate_dialogue(
            text_list=text_list,
            prompt_wav_list=prompt_wav_list if prompt_wav_list else None,
            prompt_text_list=prompt_text_list if prompt_text_list else None,
            temperature=temperature,
            topk=topk
        )
        for audio_chunk in audio_generator:
            all_audio_tensors.append(audio_chunk)
            
        if all_audio_tensors:
             # Cat then numpy
             full_tensor = torch.cat(all_audio_tensors, dim=1)
             return full_tensor.squeeze().cpu().numpy()
             
        return np.array([])
    
    def stop_playing(self):
        """Stop audio playback."""
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


# Singleton instance
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
