
import os
import sys

# Add Qwen3-TTS path
MODEL_PATH = "D:/program/QWEN/Qwen3-TTS"
sys.path.append(MODEL_PATH)

try:
    from qwen_tts import Qwen3TTSModel
    
    # Just need to check the class or instantiate minimal version if possible
    # But Qwen3TTSModel.from_pretrained loads heavy weights.
    
    # Let's try to verify if 'get_supported_speakers' is static or needs instance
    # The code I read showed it's an instance method.
    
    print("Loading model to check speakers (this might take a minute)...")
    tts = Qwen3TTSModel.from_pretrained(MODEL_PATH)
    
    speakers = tts.get_supported_speakers()
    print("\n--- Supported Speakers ---")
    if speakers:
        for s in speakers:
            print(f"- {s}")
    else:
        print("None (Model might require custom audio or use generic defaults)")
        
except Exception as e:
    print(f"Error: {e}")
