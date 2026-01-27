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
import time

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
            
        # Streaming state
        self.streaming_mode = False
        self.streaming_queue = None
        self.streaming_playback_queue = None
        self.streaming_stop_event = None
        self.streaming_threads = []
        
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

    def _normalize_text(self, text: str) -> str:
        """Normalize text for TTS: replace problematic punctuation and strip internal tags."""
        if not text:
            return ""
            
        import re
        
        # 0. Preserve Speaker Tag if present (e.g. [S1]) which is required by FireRedTTS
        speaker_tag = ""
        match = re.match(r'^(\[S\d+\])\s*', text)
        if match:
            speaker_tag = match.group(1)
            text = text[match.end():] # Remove tag from text for cleaning
        
        # 1. Replace problematic Chinese punctuation that might cause "garbled" audio
        replacements = {
            '“': '"', '”': '"',
            '‘': "'", '’': "'",
            '（': '(', '）': ')',
            '「': '"', '」': '"',
            '『': '"', '』': '"',
            '—': '-',
            '…': '，', # Convert Chinese ellipsis to comma for better pause
            ' \n': ' ', '\n': ' ',
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
            
        # 2. Strip any remaining [REC_...] or [END_...] tags that main.py might have missed
        text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
        text = re.sub(r'\[END_[A-Z_]+\]', '', text)
        # Also strip psychological markers if any
        # Strip any remaining tags
        text = re.sub(r'【.*?】', '', text)
        
        # 3. Strip Markdown characters that TTS shouldn't read
        text = text.replace("**", "").replace("*", "")
        
        # 3.5 Convert English ellipses to comma (before strict cleaning)
        text = text.replace("...", "，").replace("..", "，")
        
        # 3.6 Preserve Emotion/Paralinguistic Tags (e.g. <|breath|>)
        # We replace them with safe placeholders that pass strict cleaning (\w include _)
        preserved_tags = []
        def save_tag(match):
            preserved_tags.append(match.group(0))
            return f"__TAG_{len(preserved_tags)-1}__"
            
        text = re.sub(r'<\|[^|]+\|>', save_tag, text)

        # 4. Strict Whitelist Cleaning (User Request: Remove all symbols except 。，！？)
        # We also keep English punctuation for compatibility and basic content (\w\s)
        # Note: \w in Python 3 matches Unicode characters (Chinese, etc.)
        text = re.sub(r'[^\w\s。，！？.,!?]', '', text)
        
        text = text.strip()
        
        # Ensure punctuation at the end to help EOS detection
        if text and text[-1] not in ['.', '。', '!', '！', '?', '？']:
            text += "。"
            
        # Restore Emotion Tags
        for i, tag in enumerate(preserved_tags):
            text = text.replace(f"__TAG_{i}__", tag)
            
        # Restore speaker tag
        if speaker_tag:
            text = f"{speaker_tag} {text}"
            
        return text
    
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
                    # Check if already converted in temp to avoid re-conversion delay
                    # But we use unique names, so... keep logic simple for now
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
                    chunk = playback_queue.get(timeout=0.2)  # Reduced timeout for responsiveness
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
                # print(f"[DEBUG] Prepending [S1] tag to text")
                text = f"[S1] {text}"
            
            # Normalize text (strip tags, fix punctuation)
            text = self._normalize_text(text)
            
            # Extract and strip emotion tags for prompt selection
            import re
            emotion_match = re.search(r'<\|emotion_(\w+)\|>', text)
            emotion = "default"
            if emotion_match:
                emotion = emotion_match.group(1)
                # Strip the tag from text to be spoken
                text = re.sub(r'<\|emotion_\w+\|>', '', text)
            
            # Also strip any other <|...|> tags (e.g. breath) if model doesn't support them
            # For now, we strip all <|...|> tags to be safe as user reported leakage
            text = re.sub(r'<\|[^>]+\|>', '', text)
            
            # Clean up double spaces
            text = re.sub(r'\s+', ' ', text).strip()
                
            text_list = [text] 
            # print(f"[DEBUG] Final TTS Input to Model: {text_list}") 
            
            # Prepare prompts
            cfg_wav = getattr(config, 'VOICE_PROMPT_PATH', None)
            cfg_text = getattr(config, 'VOICE_PROMPT_TEXT', None)
            
            prompt_wav_list = []
            prompt_text_list = []
            
            if cfg_wav:
                 if isinstance(cfg_wav, dict):
                     # Use extracted emotion
                     # print(f"[DEBUG] Emotion detected for prompt selection: {emotion}")
                     
                     # Select path
                     wav_path = cfg_wav.get(emotion) or cfg_wav.get("default")
                     if not wav_path and len(cfg_wav) > 0:
                         wav_path = list(cfg_wav.values())[0]
                     
                     if wav_path and os.path.exists(wav_path):
                         prompt_wav_list.append(self._prepare_prompt(wav_path))
                         prompt_text_list.append(str(cfg_text) if cfg_text else "")
                         
                 elif isinstance(cfg_wav, list):
                     prompt_wav_list = [self._prepare_prompt(p) for p in cfg_wav]
                     prompt_text_list = cfg_text if isinstance(cfg_text, list) else [str(cfg_text)] * len(cfg_wav)
                 elif isinstance(cfg_wav, str) and os.path.exists(cfg_wav):
                     prompt_wav_list.append(self._prepare_prompt(cfg_wav))
                     if cfg_text:
                         prompt_text_list.append(str(cfg_text))
                     else:
                         prompt_text_list.append("")
            
            # print(f"[DEBUG] Inputs - Text: {len(text_list)} | PromptWav: {len(prompt_wav_list)} items")
            # print(f"[DEBUG] Text Content: {text_list}")

            try:
                # Streaming generation
                # Note: We create a generator but iterate it to push to queue
                audio_generator = self.model.generate_dialogue(
                    text_list=text_list,
                    prompt_wav_list=prompt_wav_list if prompt_wav_list else None,
                    prompt_text_list=prompt_text_list if prompt_text_list else None,
                    temperature=temperature,
                    topk=topk
                )
                
                chunk_count = 0
                previous_chunk = None

                for audio_chunk in audio_generator:
                    if not self.is_playing:
                        print("[DEBUG] Playback interrupted.")
                        break
                        
                    chunk_np = audio_chunk.squeeze().float().cpu().numpy().astype(np.float32)
                    
                    # Playback Buffer Logic with Fade-out
                    if previous_chunk is not None:
                        all_audio.append(previous_chunk)
                        playback_queue.put(previous_chunk)
                    
                    previous_chunk = chunk_np
                    chunk_count += 1
                
                # Process the Final Chunk
                if previous_chunk is not None:
                    # Apply fade-out to the last chunk (last 100ms or 2000 samples)
                    fade_len = min(len(previous_chunk), int(self.sample_rate * 0.1))
                    if fade_len > 0:
                        fade_curve = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
                        previous_chunk[-fade_len:] *= fade_curve
                    
                    # print(f"[DEBUG] Applying fade-out to last chunk (len={len(previous_chunk)})")
                    all_audio.append(previous_chunk)
                    playback_queue.put(previous_chunk)
                
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
            text = f"[S1] {text}"

        # Normalize text
        text = self._normalize_text(text)

        # Extract and strip emotion tags for prompt selection
        import re
        emotion_match = re.search(r'<\|emotion_(\w+)\|>', text)
        emotion = "default"
        if emotion_match:
            emotion = emotion_match.group(1)
            # Strip the tag from text to be spoken
            text = re.sub(r'<\|emotion_\w+\|>', '', text)
        
        # Also strip any other <|...|> tags
        text = re.sub(r'<\|[^>]+\|>', '', text)

        # Clean up double spaces
        text = re.sub(r'\s+', ' ', text).strip()

        # Prepare inputs
        text_list = [text]
        prompt_wav_list = []
        prompt_text_list = []
        
        cfg_wav = getattr(config, 'VOICE_PROMPT_PATH', None)
        cfg_text = getattr(config, 'VOICE_PROMPT_TEXT', None)

        if cfg_wav:
             if isinstance(cfg_wav, dict):

                 # DETECT EMOTION (Peek only) - actually logic moved up, but for consistency in `generate`...
                 # Wait, for `generate` we also need to move the stripping up.
                 pass # Logic below needs update
                 
                 wav_path = cfg_wav.get(emotion) or cfg_wav.get("default")
                 if not wav_path and len(cfg_wav) > 0:
                     wav_path = list(cfg_wav.values())[0]
                 
                 if wav_path and os.path.exists(wav_path):
                     prompt_wav_list.append(self._prepare_prompt(wav_path))
                     prompt_text_list.append(str(cfg_text) if cfg_text else "")
                     
             elif isinstance(cfg_wav, list):
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
    
    def play_audio(self, audio: np.ndarray):
        """Play pre-generated audio data synchronously."""
        # Kept mostly same as before for compatibility
        if audio is None or len(audio) == 0:
            print("[WARNING] play_audio called with empty audio data")
            return
            
        print(f"[DEBUG] play_audio called with {len(audio)} samples")
        self.is_playing = True
        
        p = None
        stream = None
        
        try:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=2048
            )
            
            # Convert to float32 if needed
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            
            # Play audio in chunks
            chunk_size = 4096
            for i in range(0, len(audio), chunk_size):
                if not self.is_playing:
                    print("[DEBUG] Playback interrupted")
                    break
                chunk = audio[i:i+chunk_size]
                stream.write(chunk.tobytes())
            
            print("[DEBUG] play_audio finished")
            
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

    # ================= CONTINUOUS STREAMING METHODS =================
    
    def start_streaming_mode(self):
        """Start background threads for continuous streaming synthesis and playback."""
        if self.streaming_mode:
            print("[INFO] Streaming mode already active.")
            return

        print("[INFO] Starting continuous streaming mode...")
        self.streaming_mode = True
        self.streaming_queue = queue.Queue()
        self.streaming_playback_queue = queue.Queue(maxsize=100) # Buffer some audio
        self.streaming_stop_event = threading.Event()
        self.is_playing = True
        self.collected_audio_for_streaming = [] # Collect all audio during session to return at end?
        
        # Start synthesis worker
        synth_thread = threading.Thread(
            target=self._synthesis_worker,
            args=(self.streaming_queue, self.streaming_playback_queue, self.streaming_stop_event),
            daemon=True
        )
        synth_thread.start()
        self.streaming_threads.append(synth_thread)
        
        # Start playback worker
        # We need a dedicated PyAudio instance for this session
        self.streaming_p = pyaudio.PyAudio()
        self.streaming_stream = self.streaming_p.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=self.sample_rate,
            output=True,
            frames_per_buffer=2048
        )
        
        pb_thread = threading.Thread(
            target=self._playback_worker,
            args=(self.streaming_playback_queue, self.streaming_stream, self.streaming_stop_event),
            daemon=True
        )
        pb_thread.start()
        self.streaming_threads.append(pb_thread)

    def queue_text(self, text: str):
        """Queue a sentence for synthesis in streaming mode."""
        if not self.streaming_mode or not self.streaming_queue:
            print("[WARNING] queue_text called but streaming mode not active!")
            return
            
        if not text.strip():
            return
            
        print(f"[DEBUG] Queueing text for streaming: {text[:20]}...")
        self.streaming_queue.put(text)
        
    def stop_streaming_mode(self):
        """Stop streaming mode and wait for queues to drain."""
        if not self.streaming_mode:
            return
            
        print("[INFO] Stopping streaming mode. Waiting for queues...")
        
        # Send sentinel to synthesis queue
        if self.streaming_queue:
            self.streaming_queue.put(None)
        
        # Wait for threads
        for t in self.streaming_threads:
            t.join(timeout=30) # Prevent hanging forever
        
        # Close PyAudio
        if hasattr(self, 'streaming_stream') and self.streaming_stream:
            self.streaming_stream.stop_stream()
            self.streaming_stream.close()
        if hasattr(self, 'streaming_p') and self.streaming_p:
            self.streaming_p.terminate()
            
        self.streaming_mode = False
        self.streaming_queue = None
        self.streaming_playback_queue = None
        self.streaming_threads = []
        self.is_playing = False
        
        print("[INFO] Streaming mode stopped.")
        
        # Return collected audio if any
        if self.collected_audio_for_streaming:
            return np.concatenate(self.collected_audio_for_streaming)
        return np.array([])

    def _synthesis_worker(self, text_queue, playback_queue, stop_event):
        """Continuous background text synthesis."""
        print("[DEBUG] Synthesis worker started.")
        while not stop_event.is_set():
            try:
                # Wait for text
                text = text_queue.get(timeout=0.5)
                if text is None: # Sentinel
                    # Signal playback worker to stop (by sending None to playback queue)
                    playback_queue.put(None)
                    break
                
                # Synthesis logic (reused from generate_and_play logic partially)
                # Normalize
                if not text.lstrip().startswith("[S"):
                    text = f"[S1] {text}"
                text = self._normalize_text(text)
                
                # If text became empty after normalization, skip
                if not text.strip() or text.strip() == "。":
                    text_queue.task_done()
                    continue
                
                import re
                
                # Extract/Strip emotion tags as above
                emotion_match = re.search(r'<\|emotion_(\w+)\|>', text)
                # emotion = "default" # Unused variable locally but used for prompt selection logic if we copy it?
                # Actually _synthesis_worker currently doesn't implement emotion selection in previous code!
                # Wait, looking at lines 692+, it just used prompt_wav_list.append(...)
                
                # Let's fix that while we are here: Prompt selection support in streaming
                emotion = "default"
                if emotion_match:
                    emotion = emotion_match.group(1)
                    text = re.sub(r'<\|emotion_\w+\|>', '', text)
                
                text = re.sub(r'<\|[^>]+\|>', '', text)
                text = re.sub(r'\s+', ' ', text).strip()
                
                text_list = [text] 
                
                # Simple prompt selection (optimize: cache prompts?)
                prompt_wav_list = []
                prompt_text_list = []
                
                cfg_wav = getattr(config, 'VOICE_PROMPT_PATH', None)
                cfg_text = getattr(config, 'VOICE_PROMPT_TEXT', None)
                
                # Logic copied from generate
                if cfg_wav:
                     if isinstance(cfg_wav, str) and os.path.exists(cfg_wav):
                         prompt_wav_list.append(self._prepare_prompt(cfg_wav))
                         prompt_text_list.append(str(cfg_text) if cfg_text else "")
                     elif isinstance(cfg_wav, dict):
                         # Streaming support for emotion!
                         wav_path = cfg_wav.get(emotion) or cfg_wav.get("default")
                         if not wav_path and len(cfg_wav) > 0:
                             wav_path = list(cfg_wav.values())[0]
                         if wav_path and os.path.exists(wav_path):
                             prompt_wav_list.append(self._prepare_prompt(wav_path))
                             prompt_text_list.append(str(cfg_text) if cfg_text else "")
                
                # Synthesize
                # To reduce latency, we iterate chunks and push IMMEDIATELY to playback_queue
                audio_generator = self.model.generate_dialogue(
                    text_list=text_list,
                    prompt_wav_list=prompt_wav_list if prompt_wav_list else None,
                    prompt_text_list=prompt_text_list if prompt_text_list else None,
                    temperature=0.5,
                    topk=10
                )
                
                audio_for_sentence = []
                
                for audio_chunk in audio_generator:
                    if stop_event.is_set(): break
                    chunk_np = audio_chunk.squeeze().float().cpu().numpy().astype(np.float32)
                    playback_queue.put(chunk_np)
                    audio_for_sentence.append(chunk_np)
                
                # Add to total collection
                if audio_for_sentence:
                    self.collected_audio_for_streaming.append(np.concatenate(audio_for_sentence))

                text_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[ERROR] Synthesis worker error: {e}")
                import traceback
                traceback.print_exc()
        
        print("[DEBUG] Synthesis worker finished.")


# Singleton instance
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
