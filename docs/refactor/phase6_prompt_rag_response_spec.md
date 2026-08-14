# Phase 6: simplify prompts, production RAG, and the response protocol

> This document is the Phase 6 design-freeze artifact. It is an execution
> contract for the next production phase; it does not implement Phase 6.

## Status and freeze boundary

- Status: **formal specification only; Phase 6 production implementation is
  not started**.
- Date: 2026-08-15.
- Branch: `codex/a100-vllm-safety`.
- Accepted Phase 5 implementation:
  `95c8bb42134e9a7ff0eaa7a2d1fa6a812eb49fb8`
  (`refactor: unify relaxation game end and timeout routing`).
- Current design-freeze baseline:
  `b00acb873ed0d7484d504551c8e30bb22eaa87e3`
  (`docs: record phase 5 remote verification`).
- Local baseline command:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe -m pytest tests -q`.
- Accepted baseline: **440 passed, 0 failed, 0 skipped** in 58.33 seconds.
- The freeze changes no production Python, tests, deployment settings, model
  endpoints, STT/TTS providers, knowledge files, or runtime contracts. Before
  implementation the only intended changes are this specification and the
  accompanying inventory.

The companion file `phase6_prompt_protocol_inventory.md` is part of this
freeze. Both files must be reviewed together before any Phase 6 production
edit.

## 1. Objective

Phase 6 removes the obsolete control-output protocol from the 72B dialogue
boundary and makes production retrieval a decision-gated, curated context
source. The resulting path is:

```text
RouterProposal (suggestion)
        -> TurnPolicy
        -> exactly one TurnDecision
        -> TurnDecision.needs_rag ? curated production RAG : no RAG
        -> TurnDecision + ScaleRuntime snapshot + session context
        -> 72B language generation (Chinese spoken text only)
        -> ResponseBuilder normalization
        -> MainWindow / TTS / report adapters
```

The 72B model no longer chooses an action, scores a scale item, or emits a
machine-readable control block. It receives an already approved action and
expresses that action naturally.

## 2. Frozen authority boundaries

| Component | Phase 6 authority | Explicit prohibition |
|---|---|---|
| `RouterProposal` / 3B Router | Supplies observations, a suggested action, emotion/intensity, and `needs_rag`. | It cannot choose a question number or score and cannot execute media/end actions. |
| `TurnPolicy` | Produces the one executable `TurnDecision`, including `needs_rag`. | No prompt, RAG helper, or 72B output may replace it. |
| `ScaleRuntime` | Owns current scale/item, answer acceptance, clarification, pause/resume, and completion. | The 72B response and response parser cannot advance the runtime. |
| `SessionEngine` | Owns lifecycle execution after an approved command. | It does not infer language intent or decide RAG. |
| Production RAG | Retrieves from the explicitly curated production allowlist when `TurnDecision.needs_rag` is true. | It does not run a second intent model, issue a policy instruction, or import `safety/resources`. |
| 72B dialogue model | Produces participant-facing Chinese language for the supplied context and action. | No `|||`, JSON control block, `[END_*]`, `[REC_*]`, `[SCALE:*]`, analysis headings, or new business decision. |
| `ResponseBuilder` | Normalizes generated text and produces spoken/TTS text. | It does not infer actions, end types, relaxation, game, scale scores, or RAG need. |
| TTS adapters | Synthesize normalized spoken text and retain only explicitly supported prosody markers. | Control tags are not a TTS-to-engine control channel. |
| `MainWindow` | Displays text, sends commands, and performs device/media I/O. | UI code cannot parse a model tag to start, pause, score, or end a turn. |

The Phase 1 safety/Guard boundary remains unchanged: `safety/**` and
`safety/resources/**` are not part of the production RAG import graph.

## 3. Frozen generated-response contract

### 3.1 Input to the 72B model

The pipeline builds a language-only context from these already-authoritative
values:

1. the action in `TurnDecision` (`CHAT`, `START_SCALE`,
   `CONTINUE_SCALE`, `PAUSE_SCALE`, `RECOMMEND_RELAXATION`,
   `RECOMMEND_GAME`, or `END_SESSION`);
2. the read-only `ScaleRuntime.snapshot()` when a scale is active, including
   the actual current item and the natural question text;
3. the session and recent conversation context needed for continuity;
4. a bounded production-RAG context only when `TurnDecision.needs_rag` is
   true; and
5. the language/style requirements (natural Chinese, MI-consistent, concise,
   non-clinical participant-facing wording, and ASR-tolerant clarification).

The context must state that the action is already decided and that the model
must not re-decide it. A `START_SCALE` or `CONTINUE_SCALE` context tells the
model which natural question to express; it never asks the model to select an
item or calculate a score.

### 3.2 Output from the 72B model

The normative output is a single participant-facing Chinese text value. It may
contain only ordinary punctuation and the small prosody vocabulary explicitly
supported by the selected TTS adapter (`[breath]` and `[laughter]` where
appropriate). The model is not required to output a delimiter, analysis, JSON,
or a control tag.

During migration a provider may still return legacy text. Such text is treated
as untrusted compatibility input and never as authority. The implementation
must normalize it before display/TTS and must prove that a legacy tag cannot
cause a `TurnDecision`, `SessionEngine` command, `ScaleRuntime` mutation, or
RAG call.

### 3.3 ResponseBuilder target

`ResponseBuilder` should converge on a provider-neutral value with this
semantic shape:

```python
BuiltResponse(
    generated_text: str,  # raw provider text, retained for diagnostics only
    spoken_text: str,     # normalized participant-facing text
    tts_text: str,        # normalized text accepted by the TTS adapter
)
```

`analysis_text` and `full_response` may remain as explicitly marked
compatibility/reporting fields during the migration, but they are not part of
the live decision path. The builder may trim whitespace, remove an accidental
legacy delimiter/control block, and preserve an allowlisted prosody marker. It
must not call `detect_tag`, `parse_scale_tags`, `TurnPolicy`, `RAGService`, or
any action executor.

## 4. Frozen prompt contract

`SYSTEM_PROMPT` becomes a language-realization prompt. It keeps the useful
MI, listening, factuality, ASR-correction, short-sentence, and non-jargon
guidance. It removes instructions that ask the 72B model to:

- produce an analysis block or the `|||` delimiter;
- classify emotion/defense/change talk as a machine protocol;
- decide when to start, continue, pause, or complete a scale;
- choose relaxation, game, or end actions;
- emit `[REC_*]`, `[END_*]`, or `[SCALE:*]` tags;
- enforce timeouts, readiness, minimum rounds, or one-shot policy; or
- make a second RAG/knowledge-use decision.

Action-specific language instructions are supplied by the deterministic
pipeline context. For example, an approved scale action contains the exact
Runtime-selected natural question; an approved relaxation action contains the
already-selected exercise/media wording; an approved end action contains only
the farewell language requirements. “Positive feedback” and other ordinary
language do not become a hidden end rule in the prompt.

The 3B Router and summary prompts are separate model contracts. They may
remain structured because they support observation and `RouterProposal`, but
their payloads must not be copied into the 72B output protocol.

## 5. Frozen production-RAG contract

### 5.1 Allowlist

The production retrieval allowlist is the manually curated
`knowledge_base/knowledge.json` only (currently 10 entries). The Phase 6
implementation must make this allowlist explicit in code and tests. The five
large converted files currently present under `knowledge_base/` are not
production RAG sources:

```text
cpsycounr_converted.json
psyqa_converted.json
emollm_single_turn_1.json
emollm_single_turn_2.json
emollm_multi_turn.json
```

They remain available for offline preprocessing, evaluation, or future
curation, but must not be loaded by `RAGService` in the live import/runtime
path. Existing scripts that prepare or health-check them are offline tooling,
not evidence that they are production context.

`safety/resources/crisis_knowledge.json` remains isolated legacy safety data;
Phase 6 must not import it, merge it, or expose it through the production RAG
singleton.

### 5.2 Retrieval gate and return value

The only live gate is:

```text
if TurnDecision.needs_rag is false:
    do not call production RAG
if TurnDecision.needs_rag is true:
    retrieve deterministically from the curated allowlist
    return bounded factual/context text
```

`RAGService` may retain keyword/synonym indexing and bounded top-k/truncation,
but it must not call `AgentService.classify_rag_intent`, infer a new intent,
or emit a “must use this knowledge”/`|||`/analysis instruction. The returned
context is optional evidence for language realization; it is not a policy
command and cannot override `TurnDecision` or `ScaleRuntime`.

## 6. Legacy protocol disposition

The inventory records every current delimiter, tag, parser, cleaner, prompt,
and corpus hit. The following dispositions are normative:

- **KEEP:** Phase 2–5 contracts (`RouterProposal`, `TurnDecision`,
  `TurnDecision.needs_rag`), ScaleRuntime-derived context, TTS prosody
  allowlist, and curated knowledge retrieval.
- **SIMPLIFY:** `SYSTEM_PROMPT`, `ResponseBuilder`, history projection,
  `RAGService.get_system_suffix`, and TTS text preparation.
- **REMOVE from live path:** 72B analysis/`|||` requirements, model-generated
  action tags, internal RAG policy instructions, and RAG-side 3B routing.
- **COMPATIBILITY ONLY:** legacy `END_PATTERNS`, `REC_TAGS`, `SCALE_PATTERN`,
  tag parsers, old report cleanup, and adapters that must read historical
  transcripts. They may never be an authority.
- **OFFLINE ONLY:** PsyQA, EmoLLM, and CPsyCoun converted corpora and their
  preprocessing/inspection scripts.

No legacy item is deleted merely because it is listed here. Each removal must
be preceded by a boundary test proving the live path no longer depends on it,
and historical/report readers must be kept or migrated deliberately.

## 7. Implementation sequence (the next production phase)

The following order is frozen for the implementation commit
`refactor: simplify prompts rag and response protocol`.

1. **Add red boundary tests.** Assert plain-text 72B input/output, no live
   action from `|||`/END/REC/SCALE tags, `needs_rag` as the sole RAG gate, no
   RAG-side model call, allowlist-only loading, and no `safety/resources`
   import. Preserve the Phase 1–5 authority tests.
2. **Extract text normalization.** Move display/TTS normalization out of the
   pipeline/tag-control module as needed; make `ResponseBuilder` thin and
   provider-neutral. Keep compatibility adapters only where a test or
   historical report needs them.
3. **Rewrite the 72B prompt/context assembly.** Keep MI/language guidance;
   pass approved action, Runtime-selected scale context, recent history, and
   optional RAG context. Remove delimiter, analysis, control-tag, and hidden
   policy instructions.
4. **Remove live response parsing.** Keep streaming transport and fallback,
   but stop using model output to detect end, relaxation, game, or scale
   scores. Scale scoring continues through `ScaleAnswerInterpreter` and
   `ScaleRuntime`; action execution continues through `TurnDecision` and
   `SessionEngine`.
5. **Narrow production RAG.** Remove `LAZY_FILES` loading from the live
   service, remove `classify_rag_intent` from the retrieval path, make the
   curated allowlist explicit, and return context without control prose.
6. **Migrate history, TTS, reports, and adapters.** History stores normalized
   spoken text; TTS accepts normalized text plus allowlisted prosody; reports
   preserve historical readability without becoming a live parser.
7. **Run the affected suites and the full suite.** Update protocol fixtures
   only as part of implementation, run `python -m pytest tests -q`, run
   `git diff --check`, inspect the exact staged file list, and record results
   before creating the implementation commit.

## 8. Acceptance gates before Phase 6 implementation is accepted

All of the following are required:

1. The two freeze documents agree on the allowlist, authority chain, and
   compatibility boundary.
2. The implementation has no production import or runtime access from
   `main.py`/the production RAG path to `safety/resources/**`.
3. `TurnDecision.needs_rag` is the only live retrieval gate; false means no
   RAG call, and true cannot invoke a second policy model.
4. A 72B response containing any legacy delimiter or control tag cannot start,
   pause, score, recommend, or end anything.
5. ScaleRuntime, TurnPolicy, SessionEngine, and RouterProposal contracts remain
   intact; no Phase 7 cancellable sentence-streaming TTS is introduced.
6. Production RAG loads only the curated core file. PsyQA/EmoLLM/CPsyCoun are
   offline-only and have no live loader/import.
7. The full test suite is green, `git diff --check` is clean, and the final
   implementation record names the exact test count and changed files.
8. The production commit uses exactly:
   `refactor: simplify prompts rag and response protocol`.

## 9. Explicit non-goals

Phase 6 does not:

- redesign RouterProposal, TurnPolicy, ScaleRuntime, or SessionEngine;
- add a new policy authority or let 72B decide an action;
- migrate cancellable sentence streaming or TTS interruption (Phase 7);
- change A100/vLLM ports, model placement, STT, or TTS providers;
- reintroduce crisis/Guard runtime or production access to safety resources;
- expand the curated knowledge base or claim clinical validation for any
  unreviewed corpus; or
- introduce RouterProposal/TurnPolicy/authoritative TurnDecision changes,
  ScaleRuntime authority migration, or SessionEngine authority migration.

Phase 6 ends when language generation, response normalization, and curated
retrieval are simple consumers of the existing Phase 1–5 authorities. Only
then may the separate Phase 7 streaming/cancellation design begin.
