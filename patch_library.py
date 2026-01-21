import os

def patch_file(filepath, old_str, new_str):
    print(f"Patching {filepath}...")
    try:
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

# Patch llm/utils.py
llm_utils = r"d:\program\FireRedTTS2\fireredtts2\llm\utils.py"
# Look for standard torch.load without map_location
patch_file(llm_utils, "state_dict = torch.load(", "state_dict = torch.load(map_location='cpu', f=")
patch_file(llm_utils, "f=checkpoint_path", "checkpoint_path") # Fix potential syntax error from simple replace if needed, or better:
# Let's simple replace the specific line if exact match, otherwise regex is better but simple string replace is safer if we know content.
# Based on findstr output: "state_dict = torch.load("
# We'll reload the file content again to be sure in the script

# Let's try a robust replace
def patch_llm_utils():
    filepath = r"d:\program\FireRedTTS2\fireredtts2\llm\utils.py"
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Target: state_dict = torch.load(checkpoint_path) or similar. 
    # The error came from "Attempting to deserialize object on a CUDA device"
    # So we want to add map_location='cpu' to any torch.load call.
    
    if "map_location" not in content:
        new_content = content.replace("torch.load(", "torch.load(map_location='cpu', ")
        # This might break if torch.load is called with args positionally valid but kwargs invalid.
        # torch.load(f, map_location=..., pickle_module=..., ...)
        # The first arg is 'f'. 'map_location' is second arg (optional). 
        # If code is `torch.load(path)`, then `torch.load(map_location='cpu', f=path)` is valid.
        # But `torch.load(path)` -> `torch.load(map_location='cpu', path)` is INVALID syntax.
        
        # Better replacement:
        new_content = content.replace("torch.load(checkpoint_path)", "torch.load(checkpoint_path, map_location='cpu')")
        new_content = new_content.replace("torch.load(ckpt_path)", "torch.load(ckpt_path, map_location='cpu')")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("Patched llm/utils.py")

def patch_codec_model():
    filepath = r"d:\program\FireRedTTS2\fireredtts2\codec\model.py"
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'torch.load(ckpt_path)' in content:
        new_content = content.replace('torch.load(ckpt_path)', 'torch.load(ckpt_path, map_location="cpu")')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("Patched codec/model.py")

if __name__ == "__main__":
    patch_llm_utils()
    patch_codec_model()
