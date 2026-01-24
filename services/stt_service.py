# STT Service - FunASR Integration

import os
import sys

# Set environment variable to force soundfile backend before importing torchaudio
os.environ["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"

import queue
import threading
import numpy as np
import sounddevice as sd
import torch
import tempfile
import soundfile as sf

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FUNASR_MODEL_PATH, SAMPLE_RATE, CHANNELS


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
        
    def load_model(self, progress_callback=None):
        """Load the FunASR model."""
        if progress_callback:
            progress_callback("Loading STT model...")
            
        try:
            # Add model directory to sys.path to allow importing local model.py
            if self.model_path not in sys.path:
                sys.path.insert(0, self.model_path)
            
            # Dynamic import of FunASRNano from model.py in the model directory
            try:
                from model import FunASRNano
            except ImportError:
                # Fallback: try to find it in the directory explicitly if simple import failed
                print(f"Could not import FunASRNano from {self.model_path}, checking path...")
                raise

            # Load model and kwargs
            self.model, self.model_kwargs = FunASRNano.from_pretrained(
                model=self.model_path,
                device=self.device
            )
            # Force Chinese language recognition
            self.model_kwargs['language'] = 'zh'
            self.model.eval()
            
        except Exception as e:
            print(f"Error loading FunASR model: {e}")
            raise e
        
        if progress_callback:
            progress_callback("STT model loaded!")
            
        return True
    
    def start_recording(self):
        """Start recording audio from microphone."""
        self.is_recording = True
        self.recorded_audio = []
        self.audio_queue = queue.Queue() # Clear queue
        
        def audio_callback(indata, frames, time, status):
            if status:
                print(f"Audio status: {status}")
            if self.is_recording:
                self.audio_queue.put(indata.copy())
        
        try:
            device_id = None
            # Find the best input device
            devices = sd.query_devices()
            best_device_id = None
            
            # Priority 1: User requested Virtual Sound Card ("Cable", "Virtual", etc.)
            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    name = dev['name'].lower()
                    if "cable" in name or "virtual" in name or "stereo mix" in name:
                        best_device_id = i
                        print(f"Selected prioritized Virtual Device: {dev['name']} (Index {i})")
                        break
            
            # Priority 2: Explicit "Mic" or "麦克风" (Fallback)
            if best_device_id is None:
                for i, dev in enumerate(devices):
                    if dev['max_input_channels'] > 0:
                        name = dev['name'].lower()
                        if "mic" in name or "麦克风" in name:
                            best_device_id = i
                            print(f"Selected Microphone (Fallback): {dev['name']} (Index {i})")
                            break
            
            # Priority 3: Fallback to default
            if best_device_id is None:
                try:
                    default_device = sd.query_devices(kind='input')
                    best_device_id = default_device['index']
                    print(f"Using default input device: {default_device['name']}")
                except Exception:
                    pass
            
            # Priority 4: Fallback to ANY input device
            if best_device_id is None:
                for i, dev in enumerate(devices):
                    if dev['max_input_channels'] > 0:
                        best_device_id = i
                        print(f"Found fallback device: {dev.get('name')} (Index {i})")
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
            self.stream.start()
            
            # Start a thread to collect audio from queue
            def collect_audio():
                while self.is_recording:
                    try:
                        audio_chunk = self.audio_queue.get(timeout=0.1)
                        self.recorded_audio.append(audio_chunk)
                    except queue.Empty:
                        continue
                        
            self.collect_thread = threading.Thread(target=collect_audio, daemon=True)
            self.collect_thread.start()
            
        except Exception as e:
            print(f"Error starting recording stream: {e}")
            self.is_recording = False
            # We can't easily propagate error to UI thread from here without the queue
            # But main.py checks self.is_recording state or we could print it.
            # Ideally the UI should know.
            import traceback
            traceback.print_exc()
        
    def stop_recording(self) -> np.ndarray:
        """Stop recording and return the audio data."""
        self.is_recording = False
        
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
            
        # Wait for collect thread to finish
        if hasattr(self, 'collect_thread') and self.collect_thread.is_alive():
            self.collect_thread.join(timeout=1.0)
            
        # Concatenate all audio chunks
        if self.recorded_audio:
            audio = np.concatenate(self.recorded_audio, axis=0)
            return audio.flatten()
        return np.array([])
    
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
            print(f"[DEBUG] Audio length: {len(audio)} samples ({len(audio)/SAMPLE_RATE:.2f}s)")
            print(f"[DEBUG] Audio range: [{audio.min():.4f}, {audio.max():.4f}], mean: {audio.mean():.4f}")
            
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
                
                print(f"[DEBUG] Raw result: {res}")
                
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
                        print(f"[WARNING] Non-Chinese text detected: {text}, retrying with forced Chinese...")
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
                        print(f"[INFO] Retry result: {text}")
                    
                    # Post-processing: correct common misrecognitions
                    text = self._correct_common_errors(text)
                    return text
                
                return ""
            finally:
                # Cleanup temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            
        except Exception as e:
            print(f"Transcription error: {e}")
            import traceback
            traceback.print_exc()
            return ""
    
    def record_and_transcribe(self) -> str:
        """Convenience method to record and transcribe."""
        audio = self.stop_recording()
        return self.transcribe(audio)

    def warmup(self):
        """Warmup the STT model with test audio data."""
        print("[INFO] Warming up STT model...")
        try:
            # Create 1 second of white noise instead of silence
            # This helps avoid edge cases in model processing
            dummy_audio = np.random.randn(SAMPLE_RATE).astype(np.float32) * 0.01
            result = self.transcribe(dummy_audio)
            print(f"[INFO] STT warmup complete. Test result: '{result}'")
            return True
        except Exception as e:
            print(f"[WARNING] STT Warmup had issue (non-fatal): {e}")
            # Don't fail completely - the model may still work
            return True
    
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
        print(f"[DEBUG] Chinese ratio: {ratio:.2f} ({chinese_count}/{total_chars})")
        
        # If less than 30% Chinese, likely not Chinese
        return ratio >= 0.3


# Singleton instance
_stt_service = None

def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
