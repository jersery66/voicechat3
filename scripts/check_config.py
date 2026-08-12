#!/usr/bin/env python3
"""Pre-launch configuration health check (NON-BLOCKING DIAGNOSTIC).

Validates that external dependencies (Ollama, model files, knowledge base,
relaxation media) are reachable / parseable before the main UI starts.

IMPORTANT: this is a *diagnostic*. run_check() returns False only when a
CRITICAL check fails (Ollama unreachable, data root not writable). The
launcher (main.py) currently only prints the result and does NOT abort the
startup, so a non-critical failure is reported but the app still launches.
Non-critical gaps (missing converted knowledge base, missing relaxation
videos) are emitted as WARN and still return True.

Usage:
    python scripts/check_config.py          # standalone
    python -c "from scripts.check_config import run_check; run_check()"  # importable
"""

import json
import os
import sys

# Ensure project root is on sys.path
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)


def _ok(label: str) -> None:
    print(f"  [OK]     {label}")


def _fail(label: str, detail: str) -> bool:
    print(f"  [FAIL]   {label}: {detail}")
    return False


def _warn(label: str, detail: str) -> None:
    print(f"  [WARN]   {label}: {detail}")


def check_ollama() -> bool:
    """Verify Ollama server is reachable and has at least one model."""
    from config import OLLAMA_HOST, OLLAMA_MODEL
    try:
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        models = client.list()
        model_list = models.get("models", []) if isinstance(models, dict) else getattr(models, "models", [])
        names = []
        for m in model_list:
            if isinstance(m, dict):
                names.append(m.get("model") or m.get("name") or "")
            else:
                names.append(getattr(m, "model", None) or getattr(m, "name", None) or "")
        names = [n for n in names if n]
        if not names:
            return _fail("Ollama", f"no models found at {OLLAMA_HOST}")
        _ok(f"Ollama ({len(names)} models, using '{OLLAMA_MODEL}')")
        return True
    except Exception as e:
        return _fail("Ollama", f"cannot reach {OLLAMA_HOST}: {e}")


def check_funasr() -> bool:
    """Verify FunASR model directory exists."""
    from config import FUNASR_MODEL_PATH
    if FUNASR_MODEL_PATH and os.path.isdir(FUNASR_MODEL_PATH):
        _ok(f"FunASR ({FUNASR_MODEL_PATH})")
        return True
    _warn("FunASR", f"path not found: {FUNASR_MODEL_PATH} (STT will be disabled)")
    return True  # non-fatal


def check_cosyvoice() -> bool:
    """Verify CosyVoice model directory exists (legacy)."""
    from config import COSYVOICE_MODEL_PATH
    if COSYVOICE_MODEL_PATH and os.path.isdir(COSYVOICE_MODEL_PATH):
        _ok(f"CosyVoice (legacy, {COSYVOICE_MODEL_PATH})")
        return True
    _warn("CosyVoice (legacy)", f"path not found: {COSYVOICE_MODEL_PATH}")
    return True


def check_voxcpm() -> bool:
    """Verify VoxCPM2 model availability."""
    from config import VOXCPM_MODEL_PATH
    if VOXCPM_MODEL_PATH and os.path.isdir(VOXCPM_MODEL_PATH):
        _ok(f"VoxCPM2 ({VOXCPM_MODEL_PATH})")
        return True
    try:
        import voxcpm
        _ok("VoxCPM2 (pip package installed, will auto-download from HF Hub)")
        return True
    except ImportError:
        _warn("VoxCPM2", "pip package not installed (TTS will be disabled)")
        return True


def check_voice_prompt() -> bool:
    """Verify optional zero-shot TTS voice prompt exists."""
    from config import VOICE_PROMPT_PATH
    if VOICE_PROMPT_PATH and os.path.exists(VOICE_PROMPT_PATH):
        _ok(f"Voice Prompt ({VOICE_PROMPT_PATH})")
        return True
    _warn("Voice Prompt", f"path not found: {VOICE_PROMPT_PATH} (default voice may be used)")
    return True


def check_offline_model_root() -> bool:
    """Print the model root used by offline deployment scripts."""
    from config import OFFLINE_MODELS_ROOT
    if os.path.isdir(OFFLINE_MODELS_ROOT):
        _ok(f"Offline Models Root ({OFFLINE_MODELS_ROOT})")
    else:
        _warn("Offline Models Root", f"path not found: {OFFLINE_MODELS_ROOT}")
    return True


def check_knowledge_base() -> bool:
    """Verify knowledge_base/knowledge.json is parseable."""
    kb_path = os.path.join(APP_ROOT, "knowledge_base", "knowledge.json")
    if not os.path.exists(kb_path):
        _warn("Knowledge Base", f"{kb_path} not found (will auto-generate)")
        return True
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = len(data) if isinstance(data, list) else 0
        if count == 0:
            return _fail("Knowledge Base", "empty or invalid format")
        _ok(f"Knowledge Base ({count} entries)")
        return True
    except (json.JSONDecodeError, OSError) as e:
        return _fail("Knowledge Base", f"parse error: {e}")


def check_data_root() -> bool:
    """Verify data root directory is writable."""
    from config import DATA_ROOT
    try:
        os.makedirs(DATA_ROOT, exist_ok=True)
        # Quick write test
        test_file = os.path.join(DATA_ROOT, ".write_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        _ok(f"Data Root ({DATA_ROOT})")
        return True
    except OSError as e:
        return _fail("Data Root", f"not writable: {e}")


# Converted (RAG-ready) knowledge base files produced by the offline
# conversion scripts. Missing ones are non-fatal: the app falls back to the
# raw knowledge.json.
_CONVERTED_KB_FILES = [
    "cpsycounr_converted.json",
    "psyqa_converted.json",
    "emollm_single_turn_1.json",
    "emollm_single_turn_2.json",
    "emollm_multi_turn.json",
]


def check_converted_knowledge_base() -> bool:
    """Verify the RAG-converted knowledge base files exist (non-fatal).

    These are generated by the offline conversion scripts; if missing the
    app falls back to the raw knowledge.json, so this is a WARN not a FAIL.
    """
    kb_dir = os.path.join(APP_ROOT, "knowledge_base")
    missing = [f for f in _CONVERTED_KB_FILES
               if not os.path.exists(os.path.join(kb_dir, f))]
    if missing:
        _warn("Converted Knowledge Base",
              f"missing {len(missing)} converted file(s): {', '.join(missing)} "
              f"(RAG falls back to knowledge.json)")
    else:
        _ok(f"Converted Knowledge Base ({len(_CONVERTED_KB_FILES)} files)")
    return True  # non-fatal


def check_relaxation_media() -> bool:
    """Verify relaxation training videos exist (non-fatal).

    The .mp4 assets are downloaded separately (scripts/download_media.py),
    so their absence is a WARN rather than a hard failure.
    """
    relax_dir = os.path.join(APP_ROOT, "media_library", "relaxation")
    if not os.path.isdir(relax_dir):
        _warn("Relaxation Media", f"{relax_dir} not found (download separately)")
        return True
    required = {"呼吸训练.mp4", "肌肉放松.mp4", "冥想训练.mp4"}
    available = {f for f in os.listdir(relax_dir) if f.lower().endswith(".mp4")}
    missing = sorted(required - available)
    if not available:
        _warn("Relaxation Media", f"no .mp4 files in {relax_dir} (download separately)")
    elif missing:
        _warn("Relaxation Media", f"missing required files: {', '.join(missing)}")
    else:
        _ok(f"Relaxation Media ({len(required)} required videos)")
    return True  # non-fatal


def run_check() -> bool:
    """Run all checks. Returns True if all critical checks pass."""
    print("=" * 50)
    print("Configuration Health Check")
    print("=" * 50)

    results = [
        check_offline_model_root(),
        check_ollama(),
        check_funasr(),
        check_cosyvoice(),
        check_voxcpm(),
        check_voice_prompt(),
        check_knowledge_base(),
        check_converted_knowledge_base(),
        check_relaxation_media(),
        check_data_root(),
    ]

    print("=" * 50)
    if all(results):
        print("All checks passed.")
        return True
    print("Some checks failed. See above for details.")
    return False


if __name__ == "__main__":
    ok = run_check()
    sys.exit(0 if ok else 1)
