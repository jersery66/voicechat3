# Deployment artifact location map

All runtime artifacts are under ignored `test_output/` unless an operator
explicitly chooses another output root.

| Artifact | Location | Meaning |
| --- | --- | --- |
| Deployment readiness | `test_output/deployment_readiness/` | doctor summary/details |
| Deployment manifest | `test_output/deployment_readiness/deployment_manifest.json` | derived expected contract |
| Acceptance manifest | `test_output/deployment_readiness/acceptance_manifest.json` | future acceptance matrix |
| Offline gate | `test_output/offline_integration/offline_integration_summary.json` | simulated contract evidence |
| Phase 5 | `test_output/blackwell_acceptance/<timestamp>/` | hardware/live probe evidence |
| A/B arms | `test_output/qwen_dialogue_ab/<timestamp>/` | synthetic/real arm artifacts |
| Blind packets | A/B output directory | reviewer-facing packets; keep private map private |
| Measurement events | `test_output/observability/measurement_events.jsonl` | evidence-aware timing events |
| Memory snapshots | `test_output/observability/memory_snapshots.jsonl` | descriptive GPU snapshots |
| Performance summary | `test_output/observability/performance_summary.json` | measured availability, not pass/fail |
| Observability summary | `test_output/observability/observability_summary.json` | privacy/status metadata |
| WSL PID/metadata/logs | `~/.voicechat/vllm/` in WSL | owned service lifecycle |
| Application logs | repository `logs/` by default | ordinary application/error logs |
| Session reports | configured `DATA_ROOT` | participant/session output; never commit |

Paths containing participant data, credentials, or personal machine locations
must not be committed.
