# Phase 2 Router Proposal and Authoritative Turn Decision

## Scope and baseline

- Date: `2026-08-14`
- Branch: `codex/a100-vllm-safety`
- Phase 1 code baseline: `fafe78a919dc39044f5e6ebe1bb5f077a6d4b4d4`
- Phase 1 plan-only ancestor: `9676f0637b99b8473fcc59c9cf4155e991a02133`
- Python executable used for verification: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Runtime: Python `3.12.8`, pytest `9.1.1`
- Pre-Phase 2 baseline: `328 passed in 63.03s`, `0 failed`, `0 skipped`

Phase 2 is limited to concentrating per-turn action authority. It does not
redesign the scale engine, session engine, relaxation/game rules, crisis
detachment, or the production model deployment contract.

## Authority boundary implemented

The active turn path is now:

```text
user text/audio
  -> RouterProposal (suggestion only)
  -> TurnStateSnapshot + TurnSignals (read-only facts)
  -> TurnPolicy.decide() exactly once
  -> immutable TurnDecision
  -> existing Pipeline/UI execution adapters
```

`RouterProposal` contains only `action`, `scale_name`, `intervention_type`,
`emotion`, `intensity`, `needs_rag`, `confidence`, and `reason`. Legacy
`item`, score, risk, urgency, and UI-control keys are ignored at the boundary.
Invalid/timeout routes become `RouterProposal(CHAT)` and still pass through
`TurnPolicy`.

`TurnStateSnapshot`, `TurnSignals`, and `TurnDecision` are frozen Pydantic
value objects. `TurnDecision` is the only executable per-turn action contract:
`CHAT`, `START_SCALE`, `CONTINUE_SCALE`, `PAUSE_SCALE`,
`RECOMMEND_RELAXATION`, `RECOMMEND_GAME`, or `END_SESSION`.

Decision priority is deterministic: explicit end, active-scale answer or
pause/refusal, ending/time-limit restrictions, gated scale proposal/signals,
gated relaxation/game proposal/signals, then ordinary chat fallback.

## Changed files

### Production

- `assessment/scale_policy.py` — Router item hints are discarded; item
  progression remains executor-owned.
- `conversation/contracts.py` — Added immutable RouterProposal, snapshot,
  signals, action, and decision contracts; retained old PolicyDecision only as
  a non-production compatibility value.
- `conversation/turn_policy.py` — Added pure deterministic single-turn policy.
- `conversation/turn_signals.py` — Added side-effect-free signal collection.
- `conversation/__init__.py` — Exported new contracts with lazy compatibility
  imports to avoid provider/pipeline import cycles.
- `conversation/coordinator.py` — Journals proposal/snapshot/decision and keeps
  one voice transcript boundary; no longer creates PolicyDecision.
- `services/agent_service.py` — Router prompt/schema now emits proposal fields;
  `route_proposal()` adapts legacy backends without copying control fields.
- `services/pipeline.py` — Builds proposal/snapshot/signals, calls policy once,
  commits ordinary bookkeeping after the decision, and dispatches only from
  TurnDecision. LLM END/REC/SCALE tags remain metadata and cannot change the
  decision.
- `ui/main_window.py` — `_post_pipeline_routing()` dispatches from
  `result.turn_decision.action`; existing end/relaxation/game handlers remain
  compatibility adapters.

### Tests

- `tests/test_turn_authority.py` — Contract, validation, immutability, and
  priority/conflict matrix tests.
- `tests/test_turn_authority_pipeline.py` — Exactly-one decision and
  no-pre-decision scale mutation tests.
- `tests/test_turn_authority_boundary.py` — Static production-boundary tests
  for Router fields, PolicyDecision authority, pure policy, UI routing, and
  item ownership.
- `tests/integration/test_pipeline_e2e.py` — Legacy tag non-authority and
  decision-authorized end behavior.
- `tests/test_conversation_coordinator.py` — New typed journal records and
  compatibility adapter behavior.

No files under `safety/` were changed. No `ScaleRuntime` migration was made,
and `SessionEngine` remains compatibility/shadow infrastructure rather than a
new authoritative state source. No Phase 3 or Phase 4 work is included.

## Compatibility and deployment preservation

- Existing `|||` response splitting, scale scoring, legacy tags, report flow,
  TTS flow, and headless UI paths remain in place.
- `PolicyDecision` and `route_conversation_actions()` remain only for old
  adapters/tests; production turn execution uses RouterProposal and
  TurnDecision.
- The A100/vLLM deployment contract is unchanged: dialogue 72B at
  `127.0.0.1:8000`, Router 3B at `127.0.0.1:8001`. No third model service was
  added.

## Verification

- Targeted Phase 2 authority/coordinator/pipeline suite: `53 passed` (fresh
  local venv run after the final bookkeeping adjustment).
- Full command required by the specification:
  `python -m pytest tests -q` — `351 passed in 59.57s`, `0 failed`,
  `0 skipped`.
- `git diff --check` — passed with exit code `0`.
- Local runtime smoke: model servers on ports 8000/8001 were not available on
  this development computer. Real A100/vLLM, FunASR STT, and VoxCPM2 TTS
  smoke is **NOT RUN / environment unavailable**; no result is represented as
  passed. Tests use local fakes and do not claim hardware deployment success.

## Final Git result

- Implementation commit: `3fa975b97217e4a7ce3f23f6d2ffabf211885349`
- Remote branch HEAD: `3fa975b97217e4a7ce3f23f6d2ffabf211885349`
- Full pytest count: `351 passed`, `0 failed`, `0 skipped`
- Working tree after implementation commit: clean
- Remote/local synchronization after push: `0 ahead / 0 behind`
- Local runtime smoke: `NOT RUN / environment unavailable` for live A100/vLLM,
  STT, and TTS; ports 8000/8001 were not listening on the development host.
