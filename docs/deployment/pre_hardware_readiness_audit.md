# Pre-Hardware Deployment Readiness Audit

Status: Batch 1 working-tree audit

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

## MISSING (implemented in Batch 1 working tree)

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

The following remain outside Batch 1 and are intentionally still missing:

- A consolidated operator service-lifecycle/verify-only mode beyond the
  existing launcher.
- Deployment and acceptance manifests, memory-budget snapshots, and a unified
  performance/E2E timing collector.
- Structured application logging and the cross-component error taxonomy.
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
Qwen3.8 promotion, or real deployment result is claimed.

The next safe work, after reviewing this batch, is the separately scoped
deployment-lifecycle/identity and manifest work.  It must remain observation
and orchestration infrastructure and must not reopen the frozen architecture.
