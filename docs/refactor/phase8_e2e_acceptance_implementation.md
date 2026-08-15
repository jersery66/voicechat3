# Phase 8: complete end-to-end conversation scenario implementation

## Status

- Phase 8 status: **implementation complete; final verification pending the
  documentation-only record push**.
- Phase 1–7 remain frozen and accepted.
- No Phase 9 or later architecture work was started.

## Baseline and preflight

- Branch: `codex/a100-vllm-safety`.
- Design baseline: `215d2ccd1ec55596dd43fec4f4268fac55544f04`
  (`docs: add phase 8 e2e acceptance plan`).
- Working tree was clean before test implementation.
- Baseline command:
  `$env:PYTHONPATH='E:\Anaconda\envs\voice_chat\Lib\site-packages'; E:\Anaconda\python.exe -m pytest tests -q`.
- Baseline result before Phase 8 tests: **471 passed, 1 skipped, 0 failed**.
- The skip is the existing optional `tests/integration/test_ui_boot_headless.py`
  module because `PySide6.QtWidgets` cannot load its Qt DLL in this
  environment. The full run also emits the known Windows DLL termination
  diagnostic after pytest's successful summary; pytest exits with code 0.

## Implementation commit and scope

- Primary implementation commit:
  `223d4ff7f2d685e3c83c81f9dfe500af5ead5a16`
  (`test: add complete end-to-end conversation scenario suite`).
- Changed files are test-only:
  - `tests/e2e/__init__.py` — Phase 8 test package marker.
  - `tests/e2e/fixtures.py` — deterministic trace recorder and scripted
    external seams; wires real policy, pipeline, ScaleRuntime, delivery, and
    SessionEngine components.
  - `tests/e2e/test_phase8_conversation_scenarios.py` — successful A–H
    conversation, scale, intervention, timeout, delivery, and reset scenarios.
  - `tests/e2e/test_phase8_failure_scenarios.py` — I failure/degradation
    scenarios and static authority/deployment checks.
- No production Python, configuration, prompt, RAG data, deployment, model
  endpoint, TTS provider, STT/VAD, report schema, or Phase 1–7 source file was
  modified.
- No production defect correction was required. Final change classification:
  **TEST ONLY** and **TEST SUPPORT ONLY**.

## Scenario coverage

All frozen inventory IDs are represented by named tests:

```text
A1–A4  ordinary chat and both directions of the needs_rag gate
B1–B6  PHQ-9, GAD-7, simplified 8-item PCL-5, clarification, pause/resume,
       and report projection
C1–C5  explicit/proactive relaxation thresholds, allowance, and scale wait
D1–D2  explicit game request versus boredom
E1–E5  explicit end, partial scale end, positive feedback, deferred media end,
       and report-before-farewell source ordering
F1–F3  one-shot timeout choice, continue suppression, and timeout end
G1–G6  sentence streaming, cancellation, stale callbacks, delivered history,
       exactly-once finalization, and zero-visible cancellation
H1–H2  two-session reset/isolation and stale session callback rejection
I1–I6  Router/RAG/provider/TTS/media/report/persistence failures and
       cancellation during failure handling
```

The simplified PCL-5 definition is exercised through the repository's
canonical 8-item definition; no standard 20-item instrument was introduced.

## Authority-chain verification

The acceptance harness records and asserts:

- one RouterProposal observation and one `TurnPolicy` call/`TurnDecision` per
  valid pipeline turn;
- Router item/score fields cannot become executable scale state;
- `ScaleRuntime` and `ScaleAnswerInterpreter` own item progression, legal
  scores, clarification, pause/resume, and completion;
- `SessionEngine` remains the lifecycle command/event writer for relaxation,
  deferred end, and timeout paths;
- `TurnDecision.needs_rag` is the only live retrieval gate; psychology keywords
  cannot override false and simple wording can enable true;
- production RAG contains only `knowledge.json`, has no converted-corpus or
  `safety/resources` path, and does not call `classify_rag_intent`;
- Phase 7 generation IDs, ordered sentences, cancellation, stale checks, and
  visible `delivered_text` history are asserted with real delivery classes;
- 72B legacy END/REC/SCALE text has no action effect;
- UI and pipeline source checks retain `SessionEngine`/`GenerationController`
  ownership and reject the removed raw generation counters/direct live TTS
  submission path;
- A100/vLLM ports 8000/8001 remain present in the deployment profile/launcher.

## Verification results

Focused Phase 8 suite:

```text
pytest tests/e2e -q
46 passed
```

Affected authority/regression slice:

```text
pytest tests/e2e \
  tests/test_turn_authority.py tests/test_turn_authority_pipeline.py \
  tests/test_scale_runtime.py tests/test_scale_state_boundary.py \
  tests/test_scale_pipeline_boundary.py tests/test_phase4_lifecycle_boundary.py \
  tests/test_phase5_policy.py tests/test_phase5_policy_boundary.py \
  tests/test_phase6_pipeline_boundary.py \
  tests/test_phase6_prompt_protocol_boundary.py tests/test_phase6_rag_boundary.py \
  tests/test_phase7_delivery_boundary.py tests/integration/test_pipeline_e2e.py \
  tests/integration/test_ui_boot_headless.py -q
```

Result: **192 passed, 1 skipped, 0 failed**. The skip is the same Qt DLL
environment limitation.

Final full regression:

```text
pytest tests -q
517 passed, 1 skipped, 0 failed
```

Warnings are limited to existing dependency deprecations and the known
PySide6 import-skip deprecation warning; no new test skip was introduced.

## Runtime smoke and deployment preservation

Real A100/vLLM 72B `:8000`, 3B Router `:8001`, FunASR/VAD microphone,
VoxCPM2/CosyVoice, and physical media/audio smoke were **NOT RUN / environment
unavailable**. Fake-backed E2E results are not represented as deployment
success.

The test implementation preserves the existing provider construction, A100
launch/profile, RAG allowlist, scale definitions, SessionEngine lifecycle,
Phase 7 delivery contract, and report ordering.

## Git result

- Implementation commit pushed:
  `223d4ff7f2d685e3c83c81f9dfe500af5ead5a16`.
- Remote branch at implementation push:
  `origin/codex/a100-vllm-safety` -> `223d4ff7f2d685e3c83c81f9dfe500af5ead5a16`.
- `git diff --check`: passed for the staged implementation.
- The final documentation-only record commit is intentionally separate from
  the primary test implementation commit, so the implementation SHA remains
  independently reproducible.

Phase 8 is complete only as the final test/acceptance phase. No further
architecture phase was started.
