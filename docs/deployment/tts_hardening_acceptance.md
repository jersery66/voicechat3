# TTS deterministic lifecycle acceptance

## Final gate

- Baseline before the final acceptance-only commit: `bd7042e822feeed2de2041568bdb00d7948e26c6`
- Frozen acceptance suite: `tests/test_tts_hardening_acceptance.py`
- Initial frozen-suite result: **27 passed / 7 failed**
- Final frozen-suite result: **34 passed / 0 failed**
- Production TTS defects open: **0**
- Final acceptance commit: `test: add tts cancellation and failure regressions`

This commit freezes deterministic evidence only. It does not change
production code or runtime behavior.

## Coverage map

The tracked acceptance suite covers every frozen deterministic ID from the
deployment hardening specification:

| ID | Acceptance coverage |
|---|---|
| TTS-01–TTS-08 | `test_tts01_completed_once_for_one_sentence` through `test_tts08_repeated_stop_cleanup_is_idempotent` |
| TTS-09 | completion, cancellation, and provider-failure parametrizations for both public and prompt-cache VoxCPM paths |
| TTS-10–TTS-11 | production selector and preflight/cleanup contract tests |
| TTS-12 | warmup empty/error parametrizations |
| TTS-13 | bounded VoxCPM ring-buffer overflow and unread-audio preservation |
| TTS-14 | CosyVoice cancelled-buffer suppression and missing-prompt containment |
| TTS-15 | duplicate-worker prevention after queue timeout/shutdown |
| TTS-16–TTS-18 | disabled-TTS, stale enqueue, and new-turn cancellation barriers |
| TTS-19 | provider stall causes bounded sentence flush |
| TTS-20 | finite GenerationController retention and SentenceDeliveryQueue capacity |
| TTS-21 | audio status remains delivery telemetry only |
| X-01–X-05 | authority separation, stale callback isolation, report-first farewell, controller isolation, and deployment contracts |

The suite was retained as the evidence artifact while each production defect
was fixed; its assertions were not weakened, deleted, or xfailed.

## Defect/fix chain

| Commit | Role |
|---|---|
| `8a0510d701383a6a16912508e0abf4de2153aa34` | explicit TTS completion status |
| `7aa8f78b90423e2cc8a52462edc702203d5f473f` | VoxCPM cancellation semantics |
| `103518380bd8ea5d6dead0c7653a29776c15b8ff` | reject unusable TTS warmup (TTS-12) |
| `069eb6738e282ded75c459252225a3fdd35ca1ae` | bound VoxCPM playback buffer (TTS-13) |
| `d740fd85dec15a2055b19278ecc88844ae790aae` | prevent duplicate sentence delivery workers (TTS-15) |
| `c50b8f5c5ff348200347ed282be64cf3462a65b4` | bound sentence delivery queue (TTS-20 capacity) |
| `0f6788de5193e99c172c5ee643194b57e7a74608` | bound delivery generation retention (TTS-20 retention) |
| `16f92e1812c4553d63f11d6a535768598e668f70` | flush stalled streaming sentences on deadline (TTS-19) |
| `bd7042e822feeed2de2041568bdb00d7948e26c6` | harden CosyVoice cancellation semantics (TTS-14) |

## Frozen deterministic state

The following are **FROZEN / ACCEPTED**:

- TTS explicit terminal status
- VoxCPM2 cancellation semantics
- VoxCPM2 buffer integrity
- TTS warmup readiness
- sentence delivery worker lifecycle
- sentence delivery queue capacity
- generation record retention
- streaming sentence deadline flush
- CosyVoice compatibility cancellation
- TTS deterministic lifecycle

Production backend: **VoxCPM2**.

CosyVoice: **compatibility-only / unreachable from the production selector**.

The accepted architecture boundaries remain unchanged: TurnPolicy owns action
adjudication, ScaleRuntime owns questionnaire state, SessionEngine owns session
lifecycle, and delivery cancellation cannot mutate business state.

## Verification

Focused TTS/delivery regressions:

```text
155 passed / 0 failed
```

Full suite including this acceptance file:

```text
698 passed / 0 failed
```

Command: `python -m pytest tests -q`.

## Real-device gate remains open

Deterministic acceptance is separate from hardware acceptance. The following
remain **NOT RUN / environment unavailable**:

- real VoxCPM2 model execution and speaker playback
- audio underrun and cancellation acoustic-stop latency
- rapid one/two interruption behavior and stale audible audio
- first-audio latency at deployment prebuffer candidates
- A100/vLLM deployment smoke
- two-session real audio/model cleanup

Therefore:

```text
TTS deterministic lifecycle: FROZEN / ACCEPTED
TTS real-device acceptance: PENDING / NOT RUN
```
