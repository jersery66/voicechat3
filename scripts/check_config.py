#!/usr/bin/env python3
"""Pre-launch service health check (NON-BLOCKING DIAGNOSTIC).

Validates the configured dialogue and Agent services, the configured
ASR/TTS providers' model files, the knowledge base, and relaxation media
before the main UI starts. Checks are engine agnostic: any
OpenAI-compatible provider (llama.cpp, vLLM, ...) is probed through its
``/v1/models`` endpoint, and the Ollama provider through the Ollama
client API. Voice checks apply to the provider named by runtime.yaml;
an unselected provider is reported as such instead of being probed.

IMPORTANT: this is a *diagnostic*. When ``runtime.strict_preflight`` is
false the launcher may still continue for troubleshooting; strict runtime
configs additionally require their configured Agent endpoint, because their
startup path treats a failed check as a hard stop.
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

from deployment.runtime_config import PROVIDER_OLLAMA, get_runtime_config


def _ok(label: str) -> None:
    print(f"  [OK]     {label}")


def _fail(label: str, detail: str) -> bool:
    print(f"  [FAIL]   {label}: {detail}")
    return False


def _warn(label: str, detail: str) -> None:
    print(f"  [WARN]   {label}: {detail}")


def check_ollama() -> bool:
    """Verify Ollama server is reachable and has at least one model."""
    # OLLAMA_MODEL was removed from config.py when the provider matrix moved
    # to runtime.yaml; the endpoint choice alone decides what matters here.
    from config import OLLAMA_HOST
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
        _ok(f"Ollama ({len(names)} models)")
        return True
    except Exception as e:
        return _fail("Ollama", f"cannot reach {OLLAMA_HOST}: {e}")


def check_dialogue_backend() -> bool:
    """Check the configured dialogue service without assuming an engine.

    OpenAI-compatible providers (llama.cpp, vLLM, ...) are probed through
    their ``/v1/models`` endpoint; the desktop UI must never try to probe
    them through the Ollama client API.
    """
    from config import DIALOGUE_PROVIDER, DIALOGUE_BASE_URL, DIALOGUE_MODEL

    if DIALOGUE_PROVIDER == PROVIDER_OLLAMA:
        return check_ollama()
    return _check_openai_compatible_model(
        f"Dialogue ({DIALOGUE_PROVIDER})", DIALOGUE_BASE_URL, DIALOGUE_MODEL
    )


def _check_openai_compatible_model(label: str, base_url: str,
                                   model: str | None) -> bool:
    """Verify one configured endpoint serves its configured model."""
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
    """Check the Agent endpoint, enforcing strict runtime configurations."""
    from config import AGENT_ENABLED, AGENT_PROVIDER, AGENT_MODEL, AGENT_MODEL_SERVER

    if not AGENT_ENABLED:
        _ok("Agent (disabled by runtime config)")
        return True

    runtime = get_runtime_config()
    # Every supported provider exposes an OpenAI-compatible /v1/models
    # endpoint (llama.cpp, vLLM, and Ollama's compatibility layer alike).
    available = _check_openai_compatible_model(
        f"Agent ({AGENT_PROVIDER})", AGENT_MODEL_SERVER, AGENT_MODEL
    )
    if not available:
        if runtime.runtime.strict_preflight:
            return _fail("Agent", "required by the strict preflight runtime configuration")
        _warn("Agent", "unavailable; deterministic keyword routing remains active")
    return True


def check_funasr() -> bool:
    """Verify the FunASR model directory when it is the configured ASR provider."""
    from config import ASR_PROVIDER, FUNASR_MODEL_PATH
    if ASR_PROVIDER != "funasr":
        _ok(f"FunASR (not the configured ASR provider: {ASR_PROVIDER})")
        return True
    if FUNASR_MODEL_PATH and os.path.isdir(FUNASR_MODEL_PATH):
        _ok(f"FunASR ({FUNASR_MODEL_PATH})")
        return True
    _warn("FunASR", f"path not found: {FUNASR_MODEL_PATH} (STT will be disabled)")
    return True  # non-fatal


def check_cosyvoice() -> bool:
    """Verify the CosyVoice model directory when it is the configured TTS provider."""
    from config import COSYVOICE_MODEL_PATH, TTS_PROVIDER
    if TTS_PROVIDER != "cosyvoice":
        _ok(f"CosyVoice (not the configured TTS provider: {TTS_PROVIDER})")
        return True
    if COSYVOICE_MODEL_PATH and os.path.isdir(COSYVOICE_MODEL_PATH):
        _ok(f"CosyVoice ({COSYVOICE_MODEL_PATH})")
        return True
    _warn("CosyVoice", f"path not found: {COSYVOICE_MODEL_PATH}")
    return True


def check_voxcpm() -> bool:
    """Verify VoxCPM2 availability when it is the configured TTS provider."""
    from config import TTS_PROVIDER, VOXCPM_MODEL_PATH
    if TTS_PROVIDER != "voxcpm":
        _ok(f"VoxCPM2 (not the configured TTS provider: {TTS_PROVIDER})")
        return True

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


def check_decision_backend() -> bool:
    """Report the decision layer's readiness without ever blocking the launch.

    A decision failure is *designed* to fall back to the Qwen Agent (or to a
    deterministic chat), so an unreachable decision service must remain a WARN:
    turning it into a hard FAIL would contradict the fallback contract and stop
    interviews that would otherwise run fine.
    """
    try:
        decision = get_runtime_config().decision
    except Exception as exc:  # config already validated at import; defensive only
        _warn("Decision Layer", f"config unavailable: {type(exc).__name__}")
        return True

    if decision.mode == "legacy":
        _ok(f"Decision Layer (mode=legacy, backend={decision.backend})")
        return True

    label = f"Decision Layer (mode={decision.mode}, backend={decision.backend})"

    if decision.backend in {"anyjev_decider", "tacit"} and decision.transport == "server":
        endpoint = decision.endpoint
        if not endpoint:
            _warn(label, "no endpoint configured; will fall back at runtime")
            return True
        url = urljoin(endpoint.rstrip("/") + "/", "health")
        try:
            # Loopback must not be routed through a system proxy.
            response = requests.get(url, timeout=float(decision.timeout or 5.0),
                                    proxies={"http": None, "https": None})
            payload = response.json() if response.ok else {}
        except Exception as exc:
            _warn(label, f"{endpoint} unreachable ({type(exc).__name__}); falls back at runtime")
            return True
        if str(payload.get("status")) == "ok":
            integrity = payload.get("integrity") or {}
            if integrity and not integrity.get("ok", True):
                _warn(label, f"service up but weights unverified: {integrity.get('problems')}")
            else:
                _ok(f"{label} at {endpoint}")
        else:
            _warn(label, f"{endpoint} reported status={payload.get('status')!r}")
        return True

    if decision.transport == "inprocess" or decision.backend == "tacit":
        model_path = decision.model_path or decision.model
        if not model_path:
            _warn(label, "no model_path/model configured; falls back at runtime")
            return True
        try:
            from decision.integrity import summarise, verify_model_dir

            report = verify_model_dir(model_path, require_manifest=True, extra_files_ok=True)
        except Exception as exc:
            _warn(label, f"integrity check failed: {type(exc).__name__}: {exc}")
            return True
        if report.ok:
            _ok(f"{label} — {summarise(report)}")
        else:
            _warn(label, summarise(report))
        return True

    if decision.backend == "fake":
        _warn(label, "fake backend is for tests only; not production inference")
        return True

    _warn(label, "no readiness probe defined for this backend")
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
        check_decision_backend(),
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
