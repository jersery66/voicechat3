# Pre-Hardware Deployment Readiness Audit

Status: Batch 2 implemented; pre-hardware audit remains open for later batches

This audit records what can be completed without the target Windows 11
workstation and what must remain a real-hardware acceptance task.  It does not
create a new architecture phase and it does not change the frozen production
authority chain:

```text
RouterProposal -> TurnPolicy -> TurnDecision
                         |-> ScaleRuntime
                         |-> SessionEngine
                         `-> needs_rag gate -> dialogue LLM -> delivery -> TTS/UI/history/report
```

The dialogue model remains a wording-realization component.  No deployment
tool in this batch is allowed to make business decisions, mutate session or
scale state, or select a model from detected hardware.

## Audit baseline

- Repository: `jersery66/voicechat3`
- Branch: `codex/a100-vllm-safety`
- Starting HEAD: `4677b5a6fded435b979344d9896cd93c783dde38`
- Starting commit: `fix: complete qwen dialogue ab acceptance contract`
- Deterministic baseline reported before this batch: `850 passed / 0 failed`
- Target hardware evidence: `NOT RUN` for the RTX PRO 6000 Blackwell target
- Current development host is not the target workstation; its local GPU must
  not be used as Blackwell acceptance evidence.

## READY

The following deployment-facing capabilities already existed and were
reviewed without changing their production behavior:

- Profile-owned deployment contracts in `deployment/profiles.py`, including
  the baseline and explicit Qwen3.8 candidate Blackwell profiles.
- Immutable model and endpoint contracts for strict profiles.
- Strict preflight fail-closed behavior before Qt/MainWindow construction.
- Windows-to-WSL2 launcher scripts with Agent-before-dialogue startup,
  exact `/v1/models` identity checks, bounded readiness waits, PID/log files,
  and explicit GPU-memory arguments.
- WSL service scripts using `nohup`/`exec` and narrow PID ownership rather than
  broad process killing.
- The standalone Blackwell live probe and the Qwen dialogue A/B acceptance
  harness.  Both remain observation/acceptance tools; neither owns service
  lifecycle.
- Phase 7 generation-scoped delivery, sentence streaming, cancellation,
  stale-callback protection, and delivered-history semantics.
- Deterministic STT/FSMN-VAD and TTS lifecycle/cancellation acceptance suites.
- Existing profile, launcher, live-probe, strict-startup, prompt, delivery,
  and A/B contract tests.

## PARTIAL

These capabilities existed only as separate pieces or had an operational gap:

- `scripts/check_config.py` is a startup health check, not a read-only
  deployment doctor.  It performs a data-root write probe and is therefore not
  suitable as the artifact-producing observer requested for pre-hardware use.
- Launcher and live-probe paths perform useful endpoint/model checks, but there
  was no single operator-facing readiness report covering host, WSL, GPU,
  Python, dependencies, endpoints, and profile validation.
- Profile validation was distributed across launcher, startup, and acceptance
  tests rather than exposed as one offline static contract check.
- Port and model-identity observations existed in individual tools, but there
  was no common status vocabulary for `PORT FREE`, `PORT LISTENING`,
  `ENDPOINT HEALTHY`, `ENDPOINT WRONG MODEL`, and `ENDPOINT UNAVAILABLE`.
- Deployment docs describe the launcher and live probe, but there was no
  single pre-hardware audit or offline readiness artifact schema.
- Performance, memory, structured logging, error taxonomy, and end-to-end
  timing contracts are not yet unified into one operator artifact.

## Batch 1 additions

Batch 1 adds the narrowly scoped, read-only foundation:

- `scripts/deployment/doctor.py` — profile-aware host/WSL/dependency/endpoint
  observer.  It never installs packages, starts/stops services, kills
  processes, or edits configuration.
- `scripts/deployment/__init__.py` — deployment-tool package marker.
- `tests/test_deployment_doctor.py` — deterministic profile, endpoint, WSL
  unavailable, artifact, and read-only safety contracts.
- `test_output/deployment_readiness/readiness_summary.json` and
  `readiness_details.json` are generated on execution (the output directory is
  ignored and runtime artifacts are not committed).
- `doctor.py` records explicit statuses (`PASS`, `FAIL`, `NOT AVAILABLE`,
  `NOT RUN`, `SKIPPED`) and keeps `hardware_evidence` at `NOT RUN`; missing
  hardware is never converted into a false PASS.

## Batch 2 additions

The existing Windows/WSL launcher was reviewed and retained as the sole
operator lifecycle owner. Batch 2 added only the missing contract seams:

- A read-only `-VerifyOnly`/`-Status` path in the existing Windows launcher.
  It reports profile, endpoint, PID metadata, and exact model identity without
  starting/stopping services or launching the GUI.
- Shared WSL PID/metadata identity checks using service slot, model, port,
  and `/proc/<pid>/cmdline` evidence. Stale PID metadata is removable only in
  its own slot; ownership mismatches fail closed.
- Unknown listeners and wrong endpoint models are reported without killing or
  replacing an unknown process. Agent-before-dialogue startup and partial
  cleanup of only newly started services remain unchanged.
- Derived `deployment_manifest.json` and `acceptance_manifest.json` generation
  through `scripts/deployment/manifest.py`. DeploymentProfile remains the
  source of truth; manifests are configuration/acceptance artifacts only.
- Deterministic lifecycle and manifest contract tests.

The doctor, Phase 5 probe, and A/B harness remain observe/validate/compare
tools. None owns service lifecycle.

The following remain outside Batches 1–3 and are intentionally still missing:

- Offline STT fixture, TTS fixture, production-chain dry-run, session-torture,
  scale, RAG, and report contract harnesses as one documented readiness gate.
- Operator, model-switch, shutdown/recovery, first-machine, and troubleshooting
  runbooks.

## HARDWARE-BLOCKED

The following cannot be accepted on the current development host and require
the real Windows + WSL2 RTX PRO 6000 Blackwell workstation:

- Exact Windows GPU identity, 96 GB memory confirmation, and driver evidence.
- WSL2 GPU passthrough, CUDA visibility, PyTorch CUDA device identity, and
  vLLM compatibility.
- Agent and dialogue model startup with measured VRAM coexistence.
- Phase 5 live probe results for baseline and candidate models.
- 20–30-turn stability, real latency/throughput distributions, and model A/B
  comparison.
- VoxCPM2/FunASR/FSMN-VAD coexistence, microphone-to-playback E2E behavior,
  and human blind review.

The Batch 1 doctor may observe these signals when available, but
`NOT AVAILABLE` or `NOT RUN` is evidence of missing environment, not hardware
acceptance.

## Batch 1 boundary

No participant prompt, model profile, TurnPolicy, TurnDecision, ScaleRuntime,
SessionEngine, RAG, STT business behavior, TTS behavior, GUI behavior, or
acceptance-harness semantics were changed by this audit.  No service is
started or stopped by the doctor.  No Blackwell performance, compatibility,
Qwen3.8 promotion, or real deployment result is claimed.  Batch 3 measurement
helpers remain passive and do not alter these frozen runtime paths.

The next safe work, after reviewing this batch, is the separately scoped
performance/observability work.  It must remain measurement infrastructure and
must not reopen the frozen architecture.

## Batch 3 measurement audit

The existing measurement paths were reviewed before adding new tooling:

- Phase 5 already measures client-side non-stream latency, streaming TTFT,
  total stream latency, optional usage-derived completion tokens/throughput,
  and descriptive GPU snapshots.  Its acceptance and leakage semantics remain
  frozen.
- The Phase 7 A/B harness already records non-stream latency, stream TTFT and
  total latency, repeatability, and descriptive performance summaries.  Token
  usage is explicitly unavailable there when the client/server does not expose
  it.  Its comparison and promotion semantics remain frozen.
- `conversation/delivery.py` already owns generation IDs, sentence sequence,
  cancellation, stale checks, and delivered-history finalization.  Batch 3
  must observe this identity rather than create another telemetry generation.
- `services/metrics.py` provides an in-process duration ring buffer, and
  `services/logger.py`/`services/error_monitor.py` provide ordinary logging and
  warning/error JSONL.  They do not provide the requested evidence-aware
  measurement schema, privacy-filtered structured event contract, or stable
  cross-component error taxonomy.
- LLM timing currently records a first-content metric and the pipeline records
  a total metric, but there is no unified first-sentence, STT/VAD, TTS/audio,
  or speech-to-first-audio timing contract.

Batch 3 therefore adds passive, standalone measurement/observability helpers
and tests.  It does not rewrite Phase 5/A/B, inject timing into TurnPolicy,
ScaleRuntime, SessionEngine, RAG, STT, TTS, or delivery, and it does not define
performance thresholds.  Measurement infrastructure can be `READY` while
real measurements remain `NOT RUN`.

## Batch 3 additions

- `scripts/deployment/measurement.py` defines monotonic timing events,
  first-token/first-sentence/generation latency, speech-to-first-audio
  derivations, token-usage handling, deterministic percentile aggregation, and
  evidence-aware measurement records.
- `scripts/deployment/memory_snapshot.py` provides a read-only `nvidia-smi`
  parser/snapshot path.  Missing GPU tooling is `NOT AVAILABLE`; no VRAM
  threshold or hardware approval is encoded.
- `scripts/deployment/observability.py` provides privacy-filtered JSONL event
  output and `test_output/observability/` artifact initialization.  Content
  logging is `OFF` by default and write failures are best-effort only.
- `scripts/deployment/error_taxonomy.py` provides stable category/code mapping
  without recovery policy.
- Acceptance manifests now expose `performance_measurement`,
  `memory_measurement`, and `e2e_timing` evidence slots, all `NOT RUN` with
  null references.
- Batch 3 contract tests cover monotonic timing, cancelled/failed sample
  exclusion, usage-unavailable behavior, memory parsing, privacy, error-code
  stability, and no-business-side-effect boundaries.

The real performance, VRAM, and end-to-end measurement state remains
`NOT RUN`.  No Phase 5 or A/B acceptance semantics were changed.

Batch 3 status:

```text
MEASUREMENT INFRASTRUCTURE: READY
REAL PERFORMANCE:           NOT RUN
REAL VRAM:                  NOT RUN
REAL E2E:                   NOT RUN
```
