#!/usr/bin/env python3
"""Pre-launch configuration health check (NON-BLOCKING DIAGNOSTIC).

Validates the selected dialogue backend, model files, knowledge base, and
relaxation media before the main UI starts.

IMPORTANT: this is a *diagnostic*. Development profiles return False only when
a critical dependency fails (dialogue backend unreachable, data root not
writable); their launcher may still continue for troubleshooting. The
Profiles with ``strict_preflight`` additionally require their configured 3B
Agent endpoint, because their startup path treats a failed check as a hard stop.
Offline-only converted knowledge corpora and relaxation videos remain warnings.

Usage:
    python scripts/check_config.py          # standalone
    python -c "from scripts.check_config import run_check; run_check()"  # importable
"""

import json
import os
import sys
from urllib.parse import urljoin

import requests

# Ensure project root is on sys.path
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from deployment.profiles import get_deployment_profile


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


def check_dialogue_backend() -> bool:
    """Check the selected dialogue transport without assuming Ollama.

    For vLLM this deliberately calls the OpenAI-compatible ``/v1/models``
    endpoint. The desktop UI must never try to probe the A100 dialogue server
    through the Ollama client API.
    """
    from config import DIALOGUE_BACKEND, DIALOGUE_BASE_URL, OLLAMA_MODEL

    if DIALOGUE_BACKEND == "ollama":
        return check_ollama()
    if DIALOGUE_BACKEND != "vllm":
        return _fail("Dialogue Backend", f"unsupported backend: {DIALOGUE_BACKEND}")
    try:
        url = urljoin(DIALOGUE_BASE_URL.rstrip("/") + "/", "models")
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        available = [item.get("id") for item in data.get("data", []) if item.get("id")]
        if OLLAMA_MODEL not in available:
            return _fail("vLLM", f"model '{OLLAMA_MODEL}' not served by {url}")
        _ok(f"vLLM ({len(available)} models, using '{OLLAMA_MODEL}')")
        return True
    except Exception as exc:
        return _fail("vLLM", f"cannot reach {DIALOGUE_BASE_URL}: {exc}")


def _check_openai_compatible_model(label: str, base_url: str,
                                   model: str | None) -> bool:
    """Verify one profile-owned vLLM endpoint serves its configured model."""
    if not model:
        _ok(f"{label} (not configured)")
        return True
    if not base_url:
        return _fail(label, "configured model has no endpoint")
    try:
        url = urljoin(base_url.rstrip("/") + "/", "models")
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        available = [item.get("id") for item in data.get("data", []) if item.get("id")]
        if model not in available:
            return _fail(label, f"model '{model}' not served by {url}")
        _ok(f"{label} ({len(available)} models, using '{model}')")
        return True
    except Exception as exc:
        return _fail(label, f"cannot reach {base_url}: {exc}")


def check_agent_backend() -> bool:
    """Check the Agent endpoint, enforcing strict profiles."""
    from config import AGENT_BACKEND, AGENT_MODEL, AGENT_MODEL_SERVER
    profile = get_deployment_profile()

    if AGENT_BACKEND == "ollama":
        available = _check_openai_compatible_model("Agent Ollama", AGENT_MODEL_SERVER, AGENT_MODEL)
    elif AGENT_BACKEND == "vllm":
        available = _check_openai_compatible_model("Agent vLLM", AGENT_MODEL_SERVER, AGENT_MODEL)
    else:
        available = _fail("Agent Backend", f"unsupported backend: {AGENT_BACKEND}")
    if not available:
        if profile.strict_preflight:
            return _fail("Agent", f"required by the {profile.name} strict preflight profile")
        _warn("Agent", "unavailable; deterministic keyword routing remains active")
    return True


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
    from services.tts_service_voxcpm import TTSService

    blocker = TTSService.get_load_blocker()
    if blocker:
        _warn("VoxCPM2", blocker)
        return True

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


# Converted knowledge corpora produced by offline conversion scripts. They are
# deliberately not production RAG inputs; this check only reports their
# optional offline availability.
_CONVERTED_KB_FILES = [
    "cpsycounr_converted.json",
    "psyqa_converted.json",
    "emollm_single_turn_1.json",
    "emollm_single_turn_2.json",
    "emollm_multi_turn.json",
]


def check_converted_knowledge_base() -> bool:
    """Report optional offline corpora without treating them as live RAG."""
    kb_dir = os.path.join(APP_ROOT, "knowledge_base")
    missing = [f for f in _CONVERTED_KB_FILES
               if not os.path.exists(os.path.join(kb_dir, f))]
    if missing:
        _warn("Offline Knowledge Corpora",
              f"missing {len(missing)} optional file(s): {', '.join(missing)}")
    else:
        _ok(f"Offline Knowledge Corpora ({len(_CONVERTED_KB_FILES)} files; not loaded by production RAG)")
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
        check_dialogue_backend(),
        check_agent_backend(),
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
