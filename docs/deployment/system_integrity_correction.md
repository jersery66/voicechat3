# System Integrity Correction — Pre-Hardware

This correction keeps the frozen conversation authority chain unchanged and
addresses privacy, evidence semantics, storage reliability, and reproducibility
before real RTX PRO 6000 validation.

## Changes

- RAG diagnostics record lengths and short hashes only; raw participant text is
  not written to the warning/error stream.
- Reports record completed relaxation as a system event without inferring a
  post-exercise benefit. Report-generation failure artifacts contain a category
  and session reference, not a second copy of the conversation.
- Group statistics are labelled **会话与评估趋势** and expose descriptive
  paired scale changes. They do not report treatment efficacy or a recovery
  rate. Cross-session model-emotion observations are not injected into the
  dialogue context as conclusions.
- JSON, text, and treatment-progress writes use same-directory temporary files,
  flush/fsync, and atomic replace. New session artifacts carry a schema version.
- Each new session receives a `runtime_manifest.json` with model/profile,
  prompt/scale/RAG/catalog hashes, and Git provenance. No secrets or absolute
  model paths are included.
- The optional `TurnTraceRecorder` writes de-identified stage/decision fields
  and never serializes participant text.

## Evidence boundary

These are deterministic software-integrity corrections. They do not establish
RTX PRO 6000 hardware compatibility, CUDA/vLLM readiness, real STT/TTS
performance, or clinical/treatment efficacy.
