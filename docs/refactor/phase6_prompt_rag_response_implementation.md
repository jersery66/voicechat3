# Phase 6 Implementation Record

## Status

Phase 6 (`simplify prompts rag and response protocol`) is implemented on
`codex/a100-vllm-safety`. Phases 1–5 remain frozen and their authority
boundaries were not reopened. Phase 7 was not started.

- Baseline commit: `960047db56d698a06416a7425ac6ec2b003a0f67`
- Baseline regression: `440 passed / 0 failed / 0 skipped`
- Implementation commit: `b8d8ed1536130f235c7651eea758edd222725561`
- Remote HEAD verified after implementation push: `b8d8ed1536130f235c7651eea758edd222725561`
- Final full regression: `450 passed / 0 failed / 0 skipped`

## Authority chain preserved

```text
RouterProposal  -> advisory input only
TurnPolicy      -> sole per-turn action authority
TurnDecision    -> sole executable turn decision
ScaleRuntime    -> sole scale state and score owner
SessionEngine   -> sole session lifecycle owner
72B             -> participant-facing language realization only
RAG             -> bounded context provider, gated by TurnDecision.needs_rag
MainWindow      -> UI, device and media execution
```

No `RouterProposal`, `TurnPolicy`, `TurnDecision`, `ScaleRuntime`, or
`SessionEngine` authority migration was redesigned in this phase.

## Prompt and response protocol

- Replaced the live `SYSTEM_PROMPT` with language-only guidance: Chinese
  conversational style, MI-compatible empathy, ASR tolerance, brevity,
  factuality, prosody, and explicit instruction not to make a new decision.
- Removed live requirements for `|||`, analysis/control blocks, JSON control
  output, `[END_*]`, `[REC_*]`, and `[SCALE:*]` output.
- Added decision-conditioned context in `ConversationPipeline`; the model is
  told which expression task has already been approved and is not asked to
  choose an action, item, score, intervention, or ending.
- Scale prompt guidance now points to `ScaleAnswerInterpreter` and
  `ScaleRuntime`; the 72B never supplies the score or advances the item.
- `ResponseBuilder` now exposes `generated_text`, `spoken_text`, and
  `tts_text`. Its small legacy delimiter adapter is isolated in `core.tags`
  and remains compatibility/reporting-only; it cannot execute a command or
  mutate any runtime owner.
- Pipeline streaming buffers provider chunks, normalizes one response, and
  no longer detects or parses model control tags. Legacy tags are only
  defensively cleaned. Relaxation, game, end, and scale effects come from the
  authoritative decision/interpreter path.
- New history and DataManager assistant records store normalized
  participant-facing text. Historical cleanup remains available for old
  transcripts.

## RAG boundary

- Production `RAGService` has the explicit allowlist
  `CORE_FILES = ["knowledge.json"]` and `LAZY_FILES = []`.
- Converted corpora (`cpsycounr_converted.json`, `psyqa_converted.json`,
  `emollm_single_turn_1.json`, `emollm_single_turn_2.json`, and
  `emollm_multi_turn.json`) are offline curation/evaluation inputs only.
  Their preprocessing/health-check tooling does not make them runtime RAG
  sources, and no loader remains in the production service.
- Retrieval executes only after `TurnDecision.needs_rag` is true. A false
  gate returns no context even when the text contains psychology keywords;
  a true gate performs deterministic curated-core search, query expansion,
  ranking, and bounded context formatting.
- The live RAG path no longer calls `AgentService.classify_rag_intent`, runs
  a second routing model, or emits instructions requiring a delimiter or a
  concrete action.
- `safety/resources/crisis_knowledge.json` remains outside the production
  import graph and is not referenced by `services/rag_service.py`.

## Compatibility and safety

- `END_PATTERNS`, `REC_TAGS`, `SCALE_PATTERN`, historical report cleanup,
  TTS defensive stripping, and the internal-control leakage list remain only
  where required for old data or output safety.
- The removed AgentService RAG classifier and model-generated relaxation-tag
  helper cannot be called from the live path. The legacy relaxation helper
  now returns only a report/display fallback and never emits a control tag.
- vLLM/Ollama transport compatibility, FunASR, VoxCPM2, VAD, and the A100
  launch/profile contracts were not changed.

## Verification

- Phase 6 boundary and affected suites: `73 passed`.
- Full command: `python -m pytest tests -q` -> `450 passed / 0 failed / 0 skipped`.
- `python -m compileall` for changed production/test modules: PASS.
- `git diff --check`: PASS (only normal Windows line-ending warnings from
  Git were emitted; no whitespace errors).
- Local pure-Python smoke: PASS (`LOCAL_PYTHON_SMOKE_PASS`).
- Real A100/vLLM/STT/TTS smoke: **NOT RUN / environment unavailable**. The
  current machine reports an NVIDIA RTX 3060 Laptop GPU; local ports 8000
  and 8001 were closed. No A100, vLLM, FunASR, or VoxCPM2 result is claimed.

## Git result

The production implementation was committed as:

```text
b8d8ed1536130f235c7651eea758edd222725561
refactor: simplify prompts rag and response protocol
```

It was pushed to `origin/codex/a100-vllm-safety`. This record is a
documentation-only follow-up commit; the implementation SHA above is the
production baseline to use for subsequent work. The working tree was clean
after the implementation push, and will be rechecked after this record is
pushed.

Phase 6 stops here. No sentence-level streaming TTS, generation IDs,
cancellation-generation coordination, or delivered/generated history split
was implemented.
