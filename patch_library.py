import os
import sys

def patch_file(filepath, old_str, new_str):
    print(f"Patching {filepath}...")
    try:
        if not os.path.exists(filepath):
             print(f"⚠️ File not found: {filepath}")
             return

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if old_str in content:
            new_content = content.replace(old_str, new_str)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("✅ Patched successfully.")
        else:
            print("⚠️ String not found (already patched?).")
    except Exception as e:
        print(f"❌ Error patching {filepath}: {e}")

def get_firered_path():
    # Construct path relative to this script
    # Assuming this script is in d:\program\voice_chat_app\
    # And FireRedTTS2 is in d:\program\FireRedTTS2\
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir) # d:\program
    firered_path = os.path.join(parent_dir, "FireRedTTS2", "fireredtts2")
    
    return firered_path

def patch_llm_utils():
    base_path = get_firered_path()
    filepath = os.path.join(base_path, "llm", "utils.py")
    
    if not os.path.exists(filepath):
         print(f"Could not find llm/utils.py at {filepath}")
         return

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Target: state_dict = torch.load(checkpoint_path) -> ...map_location='cpu'...
    if "map_location" not in content:
        # Replace the specific calls we know are problematic
        # 1. torch.load(checkpoint_path)
        new_content = content.replace("torch.load(checkpoint_path)", "torch.load(checkpoint_path, map_location='cpu')")
        # 2. torch.load(ckpt_path)
        new_content = new_content.replace("torch.load(ckpt_path)", "torch.load(ckpt_path, map_location='cpu')")
        
        if content != new_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("✅ Patched llm/utils.py")
        else:
            print("⚠️ No changes made to llm/utils.py (already patched or pattern not found)")
    else:
        print("ℹ️ llm/utils.py seems already patched (found 'map_location')")

def patch_codec_model():
    base_path = get_firered_path()
    filepath = os.path.join(base_path, "codec", "model.py")
    
    if not os.path.exists(filepath):
         print(f"Could not find codec/model.py at {filepath}")
         return

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'torch.load(ckpt_path)' in content and 'map_location' not in content:
        new_content = content.replace('torch.load(ckpt_path)', 'torch.load(ckpt_path, map_location="cpu")')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("✅ Patched codec/model.py")
    else:
         print("ℹ️ codec/model.py seems okay or already patched")

if __name__ == "__main__":
    patch_llm_utils()
    patch_codec_model()
