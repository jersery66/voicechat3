#!/usr/bin/env python3
"""Pre-launch configuration health check.

Validates that external dependencies (Ollama, model files, knowledge base)
are reachable / parseable before the main UI starts.  Returns 0 on success,
1 on any failure.

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
        names = []
        for m in models.get("models", []):
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
    """Verify CosyVoice model directory exists."""
    from config import COSYVOICE_MODEL_PATH
    if COSYVOICE_MODEL_PATH and os.path.isdir(COSYVOICE_MODEL_PATH):
        _ok(f"CosyVoice ({COSYVOICE_MODEL_PATH})")
        return True
    _warn("CosyVoice", f"path not found: {COSYVOICE_MODEL_PATH} (TTS will be disabled)")
    return True  # non-fatal


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


def run_check() -> bool:
    """Run all checks. Returns True if all critical checks pass."""
    print("=" * 50)
    print("Configuration Health Check")
    print("=" * 50)

    results = [
        check_ollama(),
        check_funasr(),
        check_cosyvoice(),
        check_knowledge_base(),
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
