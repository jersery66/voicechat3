# Pre-Hardware final freeze

This document is the final software-readiness boundary for the current
repository. Its immutable release identity is the Git tag
`pre-hardware-freeze-20260817`. Verify both values before deployment:

```powershell
git rev-parse pre-hardware-freeze-20260817
git rev-parse HEAD
```

The two hashes must be equal.

```text
PRE-HARDWARE DEVELOPMENT: COMPLETE
ARCHITECTURE:             FROZEN
OFFLINE INTEGRATION:      PASS / SIMULATED
REAL DEPLOYMENT:          NOT RUN
REAL HARDWARE ACCEPTANCE: NOT RUN
REAL GPU / CUDA / vLLM:   NOT RUN
REAL PHASE 5 / A-B:       NOT RUN
REAL STT / TTS / E2E:     NOT RUN
QWEN3.8 PROMOTION:        NOT APPROVED
```

The approved baseline before Batch 5 finalization was
`6b626aec9813906b7a844cead058743f0aea56cc`. The functional Batch 5 content
commit was `83e48184e304150a380c7fcac44b21064a565779`; the tag, not a mutable
document SHA, is the final checkout identity.

No further deterministic Batch or Phase 8 is created by this freeze. The next
authorized activity is target-workstation installation and evidence-backed
hardware acceptance. Any real production bug found there must be isolated as a
separate narrow fix.
