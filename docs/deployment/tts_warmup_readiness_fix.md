# TTS Warmup Readiness Fix

## Scope and baseline

This narrow deployment-hardening change fixes only TTS-12 from the frozen
deterministic acceptance suite.  It does not modify VoxCPM cancellation,
ring-buffer behavior, CosyVoice, delivery-worker lifecycle, sentence flush,
queue capacity, generation retention, STT/FSMN-VAD, policy, session, RAG,
prompt, or model-profile behavior.

- Branch: `codex/a100-vllm-safety`
- Starting HEAD: `d18805115009ed80eda0791dd67d1efe82dea422`
- Frozen acceptance before this fix: **27 passed, 7 failed**
- Real VoxCPM2/audio/A100 smoke: **NOT RUN / environment unavailable**

## Defect and fix

`TTSService.warmup()` previously logged an empty result and returned `True`.
It now treats `None`, zero-length arrays, unusable array-like results, and
provider exceptions as `False`; only non-empty generated audio returns `True`.
The old “continuing” success wording was removed.

`MainWindow.load_models()` now consumes the warmup result.  A failed warmup is
logged, cleaned up best-effort through the existing service cleanup/unload
path, and retires `self.tts_service` by setting it to `None`.  It also sets
`tts_ok=False` and posts the participant-safe degraded status:

`语音合成不可用，已切换为无语音模式`

The overall application readiness path is unchanged: LLM/core/report setup
may continue and `models_loaded` still represents overall application
readiness, not TTS-only readiness.  The later `ConversationPipeline` receives
`tts_service=None` after this failure, so it cannot retain a broken provider.

## Tests

Added tracked regressions in `tests/test_tts_warmup_readiness.py` for:

- non-empty generated audio → `True`;
- empty array → `False`;
- `None` → `False`;
- raised generation failure → `False`;
- MainWindow warmup-result consumption, cleanup, retirement, and degraded
  status through a Qt-independent source seam.

Verification:

- Warmup-focused tests: **5 passed**.
- Existing TTS/Phase 7/adapters slice: **80 passed**.
- Full tracked regression excluding the intentionally uncommitted red
  acceptance file: **582 passed, 1 skipped, 0 failed**.
- Known skip: local PySide6/QtWidgets DLL load failure.
- `git diff --check`: passed.

The frozen acceptance file remains deliberately uncommitted at
`tests/test_tts_hardening_acceptance.py`.  After this fix it reports **28
passed, 6 failed**; the remaining red IDs are exactly TTS-13, TTS-14, TTS-15,
TTS-19, and the two independent TTS-20 ownership defects.

## Scope guard and deployment status

No completion-status or cancellation semantics were changed.  No STT,
FSMN-VAD, business authority, RAG, prompt, Qwen, or Phase 7 code was changed.
Real VoxCPM2 model execution, device latency, A100/vLLM, and speaker output
remain **NOT RUN / environment unavailable**.

The next independently scoped defect remains TTS-13; it is not included in
this change.
