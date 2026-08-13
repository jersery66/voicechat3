# Phase 1 Crisis Runtime Detachment Implementation Record

## Baseline

- Date: `2026-08-14`
- Branch: `codex/a100-vllm-safety`
- Starting code commit: `2b88b99e9a0919ed91d8df0bed771687ecae4dc1`
- Preflight HEAD: `9676f0637b99b8473fcc59c9cf4155e991a02133` (implementation-plan-only commit; not the code baseline)
- Tracking state before code changes: `origin/codex/a100-vllm-safety [ahead 1]`
- Working tree before code changes: clean
- Python: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Runtime: Python `3.12.8`, pytest `9.1.1`
- Baseline executable: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Baseline command: `& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests -q`
- Baseline pytest: `321 passed in 60.77s`; `0 failed`, `0 skipped` (exit code `0`)

## Initial dependency inventory

The bounded preflight search used this exact pattern set and excluded `docs/**`:

```powershell
$patterns = 'SafetyGate|SafetyAction|SafetyDecision|show_crisis|CrisisDialog|assess_crisis|_keyword_crisis|crisis_lock|crisis_risk|crisis_indicators|safety_payload|END_SAFETY|EndType.SAFETY|CRISIS_INTERVENTION_SUFFIX|AGENT_CRISIS_SYSTEM_MESSAGE|CRISIS_HOTLINES|build_safety_gate|build_guard_client|GUARD_MODEL|from safety|import safety'
git grep -n -E $patterns -- ':!docs/**'
```

The command found matches in 51 tracked files. In the Symbols column, `import safety` means an import dependency expressed either as `import safety...` or `from safety...`.

| File | Domain | Symbols | Disposition | Reason |
|---|---|---|---|---|
| `adapters/protocols.py` | production | `assess_crisis` → exact `assess_crisis_risk`, `_keyword_crisis` → exact `_keyword_crisis_risk`, `crisis_risk` | remove | Remove the exact `assess_crisis_risk()` and `_keyword_crisis_risk()` methods from the production Agent protocol so ordinary backends no longer implement an unused safety contract. |
| `app/engine.py` | production | `EndType.SAFETY` | update | Remove SAFETY-only executable branching while preserving the historical enum in `core/types.py`. |
| `config.py` | production | `END_SAFETY`, `CRISIS_INTERVENTION_SUFFIX`, `AGENT_CRISIS_SYSTEM_MESSAGE`, `CRISIS_HOTLINES`, `GUARD_MODEL` | update | Remove crisis protocol text and Guard exports; relocate legacy constants to `safety/legacy_config.py`. |
| `conversation/coordinator.py` | production | `SafetyGate`, `SafetyAction`, `SafetyDecision`, `show_crisis`, `crisis_risk`, `crisis_indicators`, `safety_payload`, `import safety` via `from safety...` | remove | Make Coordinator an ordinary voice/text adapter with no safety import, bypass, payload, or event. |
| `core/scale_fsm.py` | production | `crisis_lock` | remove | Remove the crisis lock state without changing ordinary scale reset/delegation behavior. |
| `core/session_fsm.py` | production | `EndType.SAFETY` | update | Remove SAFETY special handling from new runtime session transitions. |
| `core/tags.py` | production | `END_SAFETY` | remove | Make the legacy tag non-executable; normal ending tags remain available. |
| `deployment/profiles.py` | production | `GUARD_MODEL` | update | Remove optional Guard fields while retaining the A100 dialogue/Router model and endpoint contract. |
| `inference/__init__.py` | production | `build_safety_gate`, `build_guard_client` | remove | Stop exporting Guard construction from the production inference package. |
| `inference/factory.py` | production | `SafetyGate`, `build_safety_gate`, `build_guard_client`, `import safety` via `from safety...` | remove | Narrow the production factory to dialogue construction only. |
| `inference/guard_client.py` | production | `SafetyDecision`, `import safety` via `from safety...` | relocate | Move the retained Guard protocol to `safety/guard_client.py`, then delete the production-package copy. |
| `inference/vllm_guard_client.py` | production | `SafetyAction`, `SafetyDecision`, `GUARD_MODEL` (within the Guard environment/model reference), `import safety` via `from safety...` | relocate | Move the vLLM Guard adapter to `safety/vllm_guard_client.py`, outside the production inference namespace. |
| `scripts/check_config.py` | production | `GUARD_MODEL` | update | Remove Guard health-check configuration and invocation; keep dialogue, Router, STT/TTS, knowledge, and media checks. |
| `scripts/start_a100_vllm_stack.ps1` | production | `GUARD_MODEL` | update | Remove only legacy Guard environment cleanup; preserve the 8000/8001 launch contract. |
| `services/agent_service.py` | production | `assess_crisis` → exact `assess_crisis_risk`, `_keyword_crisis` → exact `_keyword_crisis_risk`, `crisis_risk`, `AGENT_CRISIS_SYSTEM_MESSAGE` | remove | Remove the exact `assess_crisis_risk()` and `_keyword_crisis_risk()` APIs, the third crisis classification call, and the crisis prompt dependency from the Router service. |
| `services/pipeline.py` | production | `show_crisis`, `assess_crisis` → exact `assess_crisis_risk`, `_keyword_crisis` → exact `_keyword_crisis_risk`, `crisis_lock`, `crisis_risk`, `crisis_indicators`, `safety_payload`, `EndType.SAFETY`, `CRISIS_INTERVENTION_SUFFIX` | remove | Eliminate calls to the exact `assess_crisis_risk()`/`_keyword_crisis_risk()` APIs plus crisis state, suffix injection, UI event, payload, and executable ending from ordinary turns. |
| `services/report_service.py` | production | `CRISIS_HOTLINES` | remove | Stop new sessions from generating crisis hotline resources; report parsing remains compatible with historical data. |
| `services/tools/report_tool.py` | production | `crisis_risk` | remove | Stop creating new crisis key events while leaving historical report readers unchanged. |
| `ui/__init__.py` | production | `CrisisDialog` | remove | Stop exporting the crisis dialog from the production UI package. |
| `ui/dialogs.py` | production | `CrisisDialog` | relocate | Move the retained dialog implementation to `safety/legacy_dialog.py`. |
| `ui/main_window.py` | production | `show_crisis`, `CrisisDialog`, `EndType.SAFETY`, `CRISIS_HOTLINES`, `build_safety_gate` | remove | Remove crisis imports, construction, queue routing, dialog display, and SAFETY ending branches. |
| `safety/__init__.py` | legacy | `SafetyGate`, `SafetyAction`, `SafetyDecision`, `import safety` via `from safety...` | update | Keep the detached safety namespace coherent and expose only retained legacy/offline safety objects. |
| `safety/crisis_policy.py` | legacy | `SafetyAction`, `SafetyDecision`, `import safety` via `from safety...` | retain | Preserve the offline deterministic policy source for later redesign; production must not import it. |
| `safety/safety_gate.py` | legacy | `SafetyGate`, `SafetyDecision`, `import safety` via `from safety...` | retain | Preserve the detached gate and its dedicated tests without production reachability. |
| `safety/types.py` | legacy | `SafetyAction`, `SafetyDecision`, `GUARD_MODEL` (inside the safety decision source literal) | retain | Preserve legacy safety contracts and Guard source metadata for offline compatibility. |
| `tests/integration/fakes.py` | tests | `assess_crisis` → exact `assess_crisis_risk`, `_keyword_crisis` → exact `_keyword_crisis_risk`, `crisis_risk` | update | Remove the exact `assess_crisis_risk()`/`_keyword_crisis_risk()` fake APIs and add ordinary intent/emotion counters for pass-through tests. |
| `tests/integration/test_pipeline_e2e.py` | tests | `show_crisis`, `_keyword_crisis`, `crisis_risk`, `crisis_indicators` | update | Replace crisis-trigger expectations with proof that former keywords use the ordinary pipeline. |
| `tests/test_adapter_conformance.py` | tests | `assess_crisis` → exact `assess_crisis_risk`, `_keyword_crisis` → exact `_keyword_crisis_risk`, `crisis_risk` | update | Remove the exact `assess_crisis_risk`/`_keyword_crisis_risk` conformance requirements and align the test with the safety-free production Agent interface. |
| `tests/test_app_engine.py` | tests | `EndType.SAFETY` | update | Remove SAFETY from executable no-force behavior coverage. |
| `tests/test_config_health_vllm.py` | tests | `GUARD_MODEL` | update | Assert config health no longer invokes a Guard backend check. |
| `tests/test_config_vllm.py` | tests | `GUARD_MODEL` | update | Replace active Guard configuration expectations with absence assertions where applicable. |
| `tests/test_conversation_coordinator.py` | tests | `show_crisis`, `crisis_risk`, `safety_payload` | update | Assert crisis wording follows the ordinary text/voice pipeline and journals no safety decision. |
| `tests/test_core_scale_fsm.py` | tests | `crisis_lock` | update | Remove crisis-lock fixtures/assertions while retaining scale-state coverage. |
| `tests/test_core_session_fsm.py` | tests | `EndType.SAFETY` | update | Remove the obsolete SAFETY-specific session transition test. |
| `tests/test_crisis_risk.py` | tests | `assess_crisis` → exact `assess_crisis_risk`, `_keyword_crisis` → exact `_keyword_crisis_risk`, `crisis_risk` | remove | Delete tests dedicated only to the exact AgentService `assess_crisis_risk()`/`_keyword_crisis_risk()` APIs removed from production. |
| `tests/test_deployment_profiles.py` | tests | `GUARD_MODEL` | update | Assert Guard fields are absent and A100 dialogue/Router endpoints remain unchanged. |
| `tests/test_inference_protocols.py` | tests | `SafetyDecision`, `import safety` via `from safety...` | update | Remove production inference-protocol dependence on the legacy safety decision contract. |
| `tests/test_pipeline.py` | tests | `END_SAFETY` | update | Assert the removed tag is ignored and does not mask a normal ending tag. |
| `tests/test_report_service.py` | tests | `EndType.SAFETY` | update | Use an ordinary ending in report fallback tests because new runtime cannot create SAFETY endings. |
| `tests/test_safety_gate.py` | tests | `SafetyGate`, `SafetyAction`, `import safety` via `from safety...` | retain | Keep dedicated tests proving detached legacy safety source remains internally usable. |
| `tests/test_vllm_deploy_script.py` | tests | `GUARD_MODEL` | update | Remove the obsolete launcher expectation for Guard environment configuration. |
| `tests/test_vllm_guard_client.py` | tests | `SafetyGate`, `SafetyAction`, `build_safety_gate`, `build_guard_client`, `GUARD_MODEL` (Guard profile/environment fixture), `import safety` via `from safety...` | update | Import and construct the relocated adapter directly from `safety`; production factory exports must disappear. |
| `AGENTS.md` | docs | `CrisisDialog`, `CRISIS_HOTLINES` | retain | Repository guidance supplied by the user is not a runtime import; do not rewrite it inside this scoped implementation. |
| `CLAUDE.md` | docs | `CrisisDialog`, `END_SAFETY`, `CRISIS_HOTLINES` | retain | Repository guidance is inventory context, not executable code; it remains outside the locked Phase 1 file list. |
| `README.md` | docs | `END_SAFETY` | update | Replace active-safety claims with the temporary-detachment status and operational limitation. |
| `graphify-out/.graphify_chunk_01.json` | docs | `CRISIS_INTERVENTION_SUFFIX`, `AGENT_CRISIS_SYSTEM_MESSAGE` | retain | Generated historical graph snapshot; it is not reachable from `main.py` and is not authoritative runtime documentation. |
| `graphify-out/.graphify_chunk_03.json` | docs | `CrisisDialog` | retain | Generated historical graph snapshot outside the live import graph. |
| `graphify-out/.graphify_semantic_new.json` | docs | `CrisisDialog`, `CRISIS_INTERVENTION_SUFFIX`, `AGENT_CRISIS_SYSTEM_MESSAGE` | retain | Generated semantic snapshot; regeneration is outside Phase 1 runtime detachment. |
| `graphify-out/GRAPH_REPORT.md` | docs | `CrisisDialog`, `CRISIS_INTERVENTION_SUFFIX`, `AGENT_CRISIS_SYSTEM_MESSAGE` | retain | Generated report is non-executable and not used as the current architecture contract. |
| `graphify-out/graph.html` | docs | `CrisisDialog`, `CRISIS_INTERVENTION_SUFFIX`, `AGENT_CRISIS_SYSTEM_MESSAGE` | retain | Generated visualization is non-executable and remains a historical snapshot. |
| `graphify-out/graph.json` | docs | `CrisisDialog`, `CRISIS_INTERVENTION_SUFFIX`, `AGENT_CRISIS_SYSTEM_MESSAGE` | retain | Generated graph data is non-executable and not imported by production. |

Legacy rows are intentionally retained or updated under `safety/**`; Phase 1's boundary test must prove that `main.py` cannot reach that package. Documentation/generated rows marked retain must not be interpreted as live crisis functionality.

## Changed files

The following path-by-path list is the current `git status --short` Phase 1 scope; each entry records the reason for the change.

- `README.md` — remove active crisis-keyword and deterministic safety-gate claims from Chinese and English architecture, pipeline, tags, deployment and ethics sections, and document the Phase 1 operational limitation.
- `adapters/protocols.py` — remove crisis methods from the production Agent protocol so ordinary backends expose only the active dialogue contract.
- `app/contracts.py` — remove the executable crisis event contract while retaining ordinary event types.
- `app/engine.py` — remove executable SAFETY event handling from the application engine.
- `config.py` — remove production crisis prompts, hotlines, Guard exports and the executable `END_SAFETY` configuration.
- `conversation/coordinator.py` — make text and voice turns use the ordinary pipeline without SafetyGate calls or crisis events.
- `core/scale_fsm.py` — remove crisis-lock state and delegation while preserving scale state transitions.
- `core/session_fsm.py` — remove SAFETY from new-runtime special ending transitions while preserving historical enum compatibility.
- `core/tags.py` — remove `END_SAFETY` from executable tag parsing.
- `deployment/profiles.py` — remove optional Guard fields while preserving the A100 dialogue and Router model/endpoint contract.
- `docs/refactor/01_feature_inventory.md` — change F06 from an executable crisis smoke scenario to detached `safety/**` legacy source guarded by the import-graph boundary test.
- `docs/refactor/phase1_crisis_detachment_implementation.md` — record the Phase 1 baseline, path-level changes, verification evidence and deployment preservation contract.
- `inference/__init__.py` — stop exporting Guard construction from the production inference package.
- `inference/factory.py` — narrow the production factory to ordinary dialogue construction.
- `inference/guard_client.py` (deleted) — remove the Guard protocol from the production inference namespace after relocating it to `safety/`.
- `inference/vllm_guard_client.py` (deleted) — remove the vLLM Guard adapter from the production inference namespace after relocating it to `safety/`.
- `knowledge_base/knowledge.json` — remove crisis-specific production knowledge entries while retaining ordinary counseling knowledge.
- `safety/__init__.py` — expose the detached legacy safety namespace without making it reachable from production imports.
- `safety/guard_client.py` (new) — retain the Guard protocol for offline/legacy safety use under the isolated namespace.
- `safety/legacy_config.py` (new) — retain exact legacy crisis constants outside production configuration.
- `safety/legacy_dialog.py` (new) — retain the legacy crisis dialog outside the production UI package.
- `safety/resources/crisis_knowledge.json` (new) — preserve the exact crisis-only knowledge entry removed from the production knowledge base without making it a RAG source.
- `safety/safety_gate.py` — adapt the retained safety gate to its isolated namespace and preserve its offline behavior.
- `safety/vllm_guard_client.py` (new) — retain the vLLM Guard adapter under the isolated legacy namespace.
- `scripts/check_config.py` — remove Guard backend health checks while keeping dialogue, Agent, STT/TTS, knowledge and media checks.
- `scripts/start_a100_vllm_stack.ps1` — remove only legacy Guard environment cleanup while preserving the two-service A100 launcher.
- `services/agent_service.py` — remove crisis classification APIs, crisis prompt calls and risk fields from the production Agent service.
- `services/pipeline.py` — remove crisis bypasses, state, suffix injection, safety payloads and `show_crisis` emission from ordinary turns.
- `services/rag_service.py` — remove production crisis-specific retrieval/routing behavior while retaining ordinary weighted RAG lookup.
- `services/report_service.py` — stop creating new crisis hotline resources while retaining historical report compatibility.
- `services/tools/report_tool.py` — stop writing new crisis risk key events while preserving historical report reads.
- `tests/integration/fakes.py` — remove crisis fake APIs and add ordinary intent/emotion counters for pass-through integration tests.
- `tests/integration/test_pipeline_e2e.py` — replace crisis-trigger expectations with ordinary-pipeline pass-through assertions.
- `tests/integration/test_ui_boot_headless.py` — assert headless startup no longer constructs or imports a production crisis UI path.
- `tests/test_adapter_conformance.py` — align Agent adapter conformance with the safety-free production protocol.
- `tests/test_agent_timeout.py` — update timeout coverage for the reduced ordinary Agent call set.
- `tests/test_app_contracts.py` — update event contract assertions after removing the executable crisis event.
- `tests/test_app_engine.py` — remove SAFETY executable ending coverage and retain ordinary end behavior.
- `tests/test_config_health_vllm.py` — assert configuration health no longer invokes a Guard check.
- `tests/test_config_vllm.py` — assert Guard configuration is absent while ordinary vLLM settings remain.
- `tests/test_conversation_coordinator.py` — assert crisis wording follows the ordinary text/voice pipeline and journals no safety decision.
- `tests/test_conversation_integration.py` — use the repository-root fixture path for integration checks after the runtime boundary change.
- `tests/test_core_scale_fsm.py` — remove crisis-lock fixtures and assertions while retaining scale coverage.
- `tests/test_core_session_fsm.py` — remove obsolete SAFETY-specific session transition coverage.
- `tests/test_crisis_risk.py` (deleted) — delete tests for the removed production Agent crisis APIs.
- `tests/test_crisis_runtime_boundary.py` (new) — enforce the production import boundary, absence of crisis symbols, PHQ-9 Q9 retention, and legacy-resource isolation.
- `tests/test_deployment_profiles.py` — assert Guard fields are absent and A100 dialogue/Router endpoints remain unchanged.
- `tests/test_inference_protocols.py` — remove production inference dependence on detached safety decision types.
- `tests/test_pipeline.py` — assert the removed safety tag is ignored and cannot mask an ordinary ending.
- `tests/test_rag.py` — update RAG expectations after removing crisis-specific production knowledge/routing.
- `tests/test_report_service.py` — use ordinary endings and report expectations because new sessions cannot create SAFETY endings.
- `tests/test_vllm_deploy_script.py` — remove obsolete launcher expectations for Guard environment configuration.
- `tests/test_vllm_guard_client.py` — import and construct the retained Guard adapter directly from `safety` rather than the production factory.
- `ui/__init__.py` — stop exporting `CrisisDialog` from the production UI package.
- `ui/dialogs.py` — remove the production crisis dialog implementation after relocating the legacy copy to `safety/legacy_dialog.py`.
- `ui/main_window.py` — remove crisis imports, queue routing, dialog display and executable SAFETY ending branches.

## Verification

- Baseline full suite: `321 passed in 60.77s`; `0 failed`, `0 skipped`.
- Baseline suite exit code: `0`.
- Task 10 documentation/deployment regression: `25 passed in 46.59s` from `tests/test_deployment_profiles.py`, `tests/test_config_health_vllm.py`, `tests/test_config_vllm.py`, `tests/test_vllm_backend.py`, and `tests/test_vllm_deploy_script.py`.
- Task 10 documentation and stale-claim paths changed: `README.md`, `docs/refactor/01_feature_inventory.md`, `deployment/profiles.py` and `services/pipeline.py`; this implementation record was updated with the current Phase 1 change summary and preservation contract.
- Task 10 deployment contract check: all four A100 model/endpoint values were found in `deployment/profiles.py`; `scripts/start_vllm_a100.ps1` remains unchanged by this task.
- Task 10 `git diff --check`: passed with no whitespace errors.
- Final boundary/PHQ protection suite: `72 passed in 18.61s` from
  `tests/test_crisis_runtime_boundary.py`, `tests/test_pipeline.py`,
  `tests/test_core_scoring.py`, and `tests/test_core_scale_fsm.py`.
- Final ordinary behavior slice: `62 passed in 18.60s` from the Coordinator,
  pipeline E2E, game, report, and data-manager tests.
- Final headless UI smoke: `7 passed in 20.44s` with `QT_QPA_PLATFORM=offscreen`.
- Final deployment/config regression: `25 passed in 48.10s`; the A100 model and
  endpoint assertions remain green.
- Final full regression after crisis-knowledge relocation: `328 passed in 62.85s`;
  `0 failed`, `0 skipped`.
- Final static boundary check: `git diff --check` produced no errors, and the
  excluded-safety production scan found no active runtime hits. Historical
  docs, tests, legacy safety files, reader compatibility files, and the
  historical `EndType.SAFETY` enum were excluded exactly as specified.
- Crisis knowledge relocation: the baseline-only entry titled `危机干预与自杀预防`
  was preserved byte-for-byte at `safety/resources/crisis_knowledge.json`;
  `knowledge_base/knowledge.json` remains production-only and has no such
  entry, and `services/rag_service.py` contains no reference to
  `safety/resources` or `crisis_knowledge`.
- Affected Phase 1 suite after relocation: `209 passed in 51.01s`.

## Deployment preservation

- Phase 1 must preserve the A100 dialogue service: `Qwen/Qwen2.5-72B-Instruct-AWQ` at `http://127.0.0.1:8000/v1`.
- Phase 1 must preserve the A100 Router service: `Qwen/Qwen2.5-3B-Instruct-AWQ` at `http://127.0.0.1:8001/v1`.
- The launcher continues to start only these two loopback vLLM services with the existing `0.82 + 0.08 = 0.90` GPU budget and readiness checks.
- STT, TTS, the PySide6 entry point, report generation, and the two-service A100 launch contract are outside the removal scope.
- Crisis/Guard routing is temporarily detached from the production runtime; its legacy source remains under `safety/` for later redesign and is not imported by `main.py`.
- Final deployment diff and endpoint checks: passed; the four A100 model/endpoint
  values and the unchanged two-service launcher contract were verified.

## Local runtime smoke

- Local headless PySide6 smoke: **PASS**, `QT_QPA_PLATFORM=offscreen` with
  `tests/integration/test_ui_boot_headless.py` (`7 passed in 18.67s`). The test
  intentionally disables model loading, so this proves UI/session wiring only.
- Local GPU observed: `NVIDIA GeForce RTX 3060 Laptop GPU, 6144 MiB`.
- Real A100/vLLM dialogue and Router services: **NOT RUN — environment
  unavailable**. This machine is the 6 GB development GPU and both
  `127.0.0.1:8000` and `127.0.0.1:8001` were unreachable.
- Real FunASR/STT microphone path: **NOT RUN — environment unavailable**; the
  local smoke tests do not load a live STT model or capture audio.
- Real VoxCPM2/TTS playback path: **NOT RUN — environment unavailable**; the
  local smoke tests do not initialize a live audio device or TTS model.

## Phase 1 boundary answers

- Can production runtime import `safety`? **NO** — the `main.py` local import
  graph cannot reach `safety/**`.
- Can Coordinator invoke `SafetyGate`? **NO**.
- Can Pipeline emit `show_crisis`? **NO**.
- Can Agent call a crisis model? **NO** — only ordinary intent/emotion calls
  remain in the production Agent contract.
- Can current runtime create `EndType.SAFETY`? **NO** — the historical enum is
  retained only for compatibility; executable tags and new-runtime branches do
  not create it.
- Is PHQ-9 Q9 retained? **YES** — both the item and score-tag protection tests
  pass.
- A100 profile preserved? **YES**.
- vLLM 8000/8001 preserved? **YES**.

## Git result

- Preflight HEAD: `9676f0637b99b8473fcc59c9cf4155e991a02133`.
- Starting code commit: `2b88b99e9a0919ed91d8df0bed771687ecae4dc1`.
- Final implementation commit, pushed remote HEAD, and final clean-working-tree
  result are verified in the final handoff. The commit SHA is deliberately
  reported there rather than embedded self-referentially in this commit.
