# Phase 6 preflight: prompt, RAG, and response-protocol inventory

**Status:** pre-implementation inventory; production code is unchanged.

- Date: 2026-08-15
- Branch: `codex/a100-vllm-safety`
- Baseline HEAD: `b00acb873ed0d7484d504551c8e30bb22eaa87e3`
- Phase 5 production ancestor:
  `95c8bb42134e9a7ff0eaa7a2d1fa6a812eb49fb8`
- Python executable used for the baseline:
  `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Baseline result: **440 passed, 0 failed, 0 skipped** (`pytest tests -q`).

This is a read-only map of the current protocol and data paths. It does not
authorize edits. The companion
`phase6_prompt_rag_response_spec.md` is the normative authority for the
future implementation.

## Scope and search method

The scan covered production Python under `app/`, `assessment/`,
`conversation/`, `core/`, `game/`, `services/`, `ui/`, `deployment/`,
`config.py`, and `main.py`, plus `knowledge_base/`, offline scripts, and
tests where a current contract is named. Generated `.vs/` and `graphify-out/`
artifacts were treated as audit output, not runtime code.

The required marker search included:

```text
|||
END_PATTERNS, END_TAGS, [END_, END_
REC_TAGS, [REC_, REC_
SCALE_PATTERN, [SCALE:
analysis_text, spoken_text, tts_text
parse_, clean_, strip_
system_prompt, prompt, prompt_version
needs_rag, rag_service, knowledge, knowledge_base
PsyQA, EmoLLM, CPsyCoun
agent_route, intent, emotion
```

The direct repository search also covered:

```text
ResponseBuilder, clean_for_display, clean_for_tts, detect_tag,
parse_scale_tags, get_system_suffix, classify_rag_intent,
knowledge_base, knowledge.json, RAGService, rag_service, needs_rag
```

The marker scan produced these focused results in production/tests (generated
audit files excluded):

| Marker family | Current hit summary | Freeze interpretation |
|---|---|---|
| `|||`, `analysis_text`, `spoken_text`, `tts_text` | Present in `config.py`, `conversation/response_builder.py`, `services/llm_service.py`, `services/pipeline.py`, TTS/report adapters, and protocol fixtures. | Legacy response protocol is still active and must be simplified without changing the authority chain. |
| `END_PATTERNS`, `[END_` | `END_PATTERNS` is in `core/tags.py`/pipeline/tests; `[END_` is also cleaned by history, TTS, reports, and UI adapters. | Keep only as a compatibility/report boundary; no live action authority. |
| `REC_TAGS`, `[REC_` | Present in `app/contracts.py`, `core/tags.py`, pipeline, agent helper, TTS/report/UI adapters, and tests. | Keep media execution behind `TurnDecision`; remove model-tag control from the live path. |
| `SCALE_PATTERN`, `[SCALE:` | Present in `core/tags.py`, pipeline, config, scoring/scales, UI, and tests. | ScaleRuntime/ScaleAnswerInterpreter remain authoritative; parser is compatibility-only. |
| `END_TAGS`, `prompt_version` | No exact production/test symbol hit. | Do not invent either symbol as a new Phase 6 protocol. |
| `needs_rag` | Present in `RouterProposal`, `TurnPolicy`, `TurnDecision`, AgentService, and Pipeline. | Preserve as the sole live RAG gate. |
| `agent_route`, `intent`, `emotion` | Present across Router contracts, pipeline analytics, reports, and UI projections. | Keep as observation/derived context; do not turn them into 72B control output. |
| `PsyQA`, `EmoLLM`, `CPsyCoun` (case-sensitive names) | No exact case-sensitive symbol hit; lower-case converted corpus filenames and loader entries are present. | Treat the lower-case files as the offline corpora listed above; no runtime corpus import. |

### Observed corpus facts

`knowledge_base/knowledge.json` is 6,484 bytes and contains 10 curated entries:
失眠干预技术、焦虑情绪干预、幻觉应对策略、戒断症状应对、抑郁情绪干预、
解离症状应对、愤怒情绪管理、家庭关系支持、放松训练技术、心理评估工具。

The same directory also contains the following converted corpora, which the
current `RAGService.LAZY_FILES` loads on demand and which Phase 6 must move to
offline-only handling:

| File | Observed size | Phase 6 disposition |
|---|---:|---|
| `cpsycounr_converted.json` | 15,083,466 bytes | OFFLINE ONLY |
| `psyqa_converted.json` | 84,008,074 bytes | OFFLINE ONLY |
| `emollm_single_turn_1.json` | 1,434,251 bytes | OFFLINE ONLY |
| `emollm_single_turn_2.json` | 2,317,727 bytes | OFFLINE ONLY |
| `emollm_multi_turn.json` | 1,175,955 bytes | OFFLINE ONLY |

`safety/resources/crisis_knowledge.json` is a separate legacy safety resource
and is not a production RAG source. `git grep` found the corpus names in the
RAG service, preprocessing/health-check scripts, tests, and the corpus files;
the `graphify-out/` matches are generated audit artifacts. No external
`PsyQA`, `EmoLLM`, or `CPsyCoun` Python package import was found.

## Disposition vocabulary

- **KEEP:** remains an active contract or runtime input without gaining new
  authority.
- **SIMPLIFY:** preserve the capability while removing obsolete protocol or
  policy text.
- **REMOVE:** delete from the live production path after a boundary test proves
  the replacement path.
- **OFFLINE ONLY:** retain for preprocessing/evaluation/curation, never load or
  import from live conversation RAG.
- **COMPATIBILITY ONLY:** read historical/legacy data if necessary; it cannot
  affect a `TurnDecision`, runtime state, command, or RAG gate.
- **DERIVED ONLY:** reporting, logging, or display projection; it cannot feed
  policy or mutable state.

## Inventory matrix

| ID | Current symbol/path and evidence | Current use | Phase 6 disposition and required boundary |
|---|---|---|---|
| P1 | `config.py:360-520`, `SYSTEM_PROMPT` | Requires `|||`, analysis headings, END/REC/SCALE tags, model-side policy, and old end semantics while also carrying useful MI/ASR/style rules. | **SIMPLIFY**. Keep language guidance; remove every control-output and decision instruction. |
| P2 | `config.py:780-794`, `AGENT_SCALE_SYSTEM_MESSAGE` | 3B scale-trigger observation prompt. | **KEEP** as a Router/observation contract; never copy its structured output into the 72B response. |
| P3 | `config.py:796-803`, `AGENT_SUMMARY_SYSTEM_MESSAGE` | 3B history compression. | **KEEP** as a separate helper contract; its summary is context, not policy. |
| P4 | `services/agent_service.py:614-655`, Router JSON prompt | Produces action/scale/emotion/intensity/`needs_rag` proposal. | **KEEP** Phase 2 boundary. `needs_rag` remains an input to `TurnDecision`; no item/score or 72B control protocol may be added. |
| P5 | `conversation/contracts.py:53-137`, `RouterProposal` | Frozen adapter from Router/legacy routes. | **KEEP** unchanged in Phase 6. |
| P6 | `conversation/contracts.py:184-220`, `TurnDecision` | Single executable turn decision, including `needs_rag`. | **KEEP** unchanged and authoritative. |
| P7 | `conversation/response_builder.py:1-41`, `BuiltResponse`, `ResponseBuilder` | Splits `|||`, detects reversed analysis/spoken orientation, and calls pipeline cleaners. | **SIMPLIFY**. Normalize generated/spoken/TTS text only; analysis/full-response data is compatibility/reporting metadata. |
| P8 | `core/tags.py:14-109`, `END_PATTERNS`, `REC_TAGS`, `SCALE_PATTERN`, `detect_tag`, `parse_scale_tags` | Parses legacy model tags for pipeline metadata and tests. | **COMPATIBILITY ONLY**. No parser result may execute an action, score, state change, or RAG call. |
| P9 | `core/tags.py:112-145`, `clean_for_display`, `clean_for_tts` | Removes `|||`, tags, internal headings, and keeps some TTS markers. | **SIMPLIFY**. Move toward a plain-text/prosody normalizer; keep a narrow historical adapter if reports/tests require it. |
| P10 | `core/tags.py:_FORBIDDEN_INTERNAL_TERMS` | Prevents clinical/internal strategy leakage. | **KEEP** as an output-safety defense, independent of action parsing. |
| P11 | `services/pipeline.py:323-345`, `PipelineResult` | Carries `full_response`, `analysis_text`, spoken/TTS, end/relaxation tags, route and authority snapshots. | **SIMPLIFY**. Spoken/TTS and authority snapshots remain; raw protocol fields become derived/compatibility metadata, not controls. |
| P12 | `services/pipeline.py:1765-1830`, `_stream_llm` | Accumulates output, splits `|||`, detects reversed format, strips duplicate analysis, and emits cleaned text. | **SIMPLIFY/REMOVE** the protocol parsing; keep provider streaming, fallback, bounded output, and one normalized emission. |
| P13 | `services/pipeline.py:1135-1185` | Builds legacy `analysis|||spoken` fallback and cleans it before display. | **SIMPLIFY**. Fallback is plain language; no synthetic control block. |
| P14 | `services/pipeline.py:1185-1213`, `detect_tag`/`parse_scale_tags` calls | Reads END/REC/SCALE tags after the 72B response. | **REMOVE from live authority**. Preserve only explicit compatibility/report metadata and prove tags cannot execute anything. |
| P15 | `services/pipeline.py:789-814`, remaining-scale prompt | Tells the model to append `[SCALE:...]` for scoring. | **REMOVE** tag instruction. Runtime-selected question and `ScaleAnswerInterpreter` own scoring. |
| P16 | `services/pipeline.py:1485-1513`, `_build_scale_context_hint` | Uses `SCALE_ITEM_CORES`/natural question text but still requests model-generated SCALE tags. | **SIMPLIFY**. Keep the natural question and answer-axis context; remove tag/score output instructions. |
| P17 | `services/pipeline.py:1705-1729`, `needs_rag` and `get_system_suffix` call | Uses `TurnDecision.needs_rag` to append RAG context. | **KEEP/SIMPLIFY**. This remains the sole gate; context must be language-only and bounded. |
| P18 | `services/llm_service.py:264-301`, `_history_visible_text` | Heuristically selects the spoken side of `|||` and strips analysis/control tags before history. | **SIMPLIFY**. Store normalized spoken text; no marker/orientation heuristics for new responses. |
| P19 | `services/llm_factory.py:81-100`, vLLM/Ollama history surface | Duplicates history-visible text compatibility for the alternate backend. | **SIMPLIFY** while preserving Ollama/vLLM backend and endpoint contracts. |
| P20 | `services/rag_service.py:255-264`, `CORE_FILES`/`LAZY_FILES` | Loads `knowledge.json` immediately and five large converted corpora lazily. | **REMOVE** lazy corpus loading from production; **KEEP** explicit curated core allowlist. |
| P21 | `services/rag_service.py:490-520`, `_intent_routing` | Decides whether retrieval is needed from synonyms, phrases, and jieba terms. | **REMOVE as authority**. Retrieval only runs after `TurnDecision.needs_rag` is true; indexes may remain for query expansion. |
| P22 | `services/rag_service.py:674-728`, `get_context` | Applies local routing, skips casual text, and builds search context. | **SIMPLIFY** to deterministic allowlist retrieval under the decision gate. |
| P23 | `services/rag_service.py:693-713`, `AgentService.classify_rag_intent` call | Invokes a second 3B model to decide implicit RAG need. | **REMOVE** from live path. `TurnDecision.needs_rag` is authoritative. |
| P24 | `services/rag_service.py:730-759`, `get_system_suffix` | Adds “must use” instructions requiring `|||` analysis and a concrete action. | **REMOVE** control prose. Return bounded context only. |
| P25 | `services/rag_service.py:17-37`, `_init_jieba`, and `SYNONYM_MAP` | Expands colloquial psychology queries and gates retrieval. | **SIMPLIFY/KEEP** as deterministic query indexing only; it cannot decide whether RAG runs. |
| P26 | `knowledge_base/knowledge.json` | Curated 10-entry production knowledge file. | **KEEP** as the only production RAG source, with an explicit allowlist/boundary test. |
| P27 | `knowledge_base/cpsycounr_converted.json`, `psyqa_converted.json`, `emollm_*.json` | Large converted corpora available to lazy RAG and offline preprocessors. | **OFFLINE ONLY**. No live loader/import; retain only for curation, evaluation, or preprocessing. |
| P28 | `scripts/preprocess_knowledge_base.py` and `scripts/check_config.py:231-255` | Preprocesses and health-checks converted corpora. | **OFFLINE ONLY**. Health checks may report their presence without making them runtime RAG inputs. |
| P29 | `services/agent_service.py:257-274`, `classify_rag_intent` | Public 3B helper for RAG intent classification. | **COMPATIBILITY ONLY/REMOVE** after live RAG no longer calls it; it cannot override `needs_rag`. |
| P30 | `services/agent_service.py:289-322`, `recommend_relaxation_strategy` | Returns legacy `[REC_*]` control-tag strings from a helper model. | **COMPATIBILITY ONLY/REMOVE**. It cannot approve or execute relaxation. |
| P31 | `app/contracts.py:26-35`, `REC_TAG_TO_KIND` | Maps legacy REC tags to normalized media event kinds. | **COMPATIBILITY ONLY** until all historical/event adapters use decision-driven commands. |
| P32 | `services/tts_service_voxcpm.py:180-203` and `services/tts_service_cosyvoice.py:150-179` | Strips control tags and preserves selected breath/laughter markers. | **SIMPLIFY**. Accept normalized spoken text and an explicit prosody allowlist; stripping is a defensive compatibility layer only. |
| P33 | `services/report_service.py:546-588`, relaxation helper; `:610-620`, report cleanup | Report-side strategy helper and historical REC/END cleanup. | **COMPATIBILITY ONLY/DERIVED ONLY**. Reports cannot produce live actions or policy. |
| P34 | `services/tools/report_tool.py` cleanup helpers and protocol fields | Cleans legacy response chunks for reports. | **COMPATIBILITY ONLY/DERIVED ONLY**; preserve historical readability, not live parsing. |
| P35 | `conversation/context_builder.py:1-40` | Builds scale/RAG context fragments. | **SIMPLIFY/KEEP**. Context is a value passed to the 72B; it cannot make decisions. |
| P36 | `conversation/turn_policy.py:79-187` and `conversation/contracts.py:194` | Computes the authoritative decision and `needs_rag`. | **KEEP**. Phase 6 must not introduce another policy path. |
| P37 | `ui/main_window.py:2058` and other UI cleanup | Removes legacy SCALE text before display; displays/starts media from events. | **SIMPLIFY/DERIVED ONLY**. UI must consume final decisions/events, not interpret 72B tags. |
| P38 | `tests/test_conversation_components.py`, `tests/test_core_tags.py`, `tests/test_pipeline.py`, and E2E fixtures | Assert legacy `analysis|||spoken` and END/REC/SCALE compatibility behavior. | **UPDATE during implementation**. Add red boundary tests first; retain historical parser tests only for compatibility adapters. |
| P39 | `tests/test_rag.py` and RAG integration fakes | Exercise core and converted knowledge behavior. | **UPDATE during implementation** to prove core-only production loading, no agent RAG classifier, and `needs_rag` gating. |
| P40 | `AGENTS.md` architecture description | Describes the pre-Phase6 delimiter/tag/RAG architecture. | **FOLLOW-UP DOCUMENTATION**. Do not edit in this design freeze; update only with the implementation record after production behavior changes. |

## Required Phase 6 boundary tests

The implementation must add or update tests for these invariants before
touching the old protocol code:

1. A plain 72B text response becomes `spoken_text`/`tts_text` without a
   delimiter or synthetic analysis.
2. Legacy `|||`, `[END_*]`, `[REC_*]`, and `[SCALE:*]` text is normalized or
   retained only as compatibility metadata; it cannot mutate
   `TurnDecision`, `ScaleRuntime`, `SessionEngine`, media, or report policy.
3. `TurnDecision.needs_rag=False` results in zero production RAG calls; true
   results in a deterministic lookup from `knowledge.json` only.
4. `RAGService` never calls `classify_rag_intent` on the live path and never
   loads any `LAZY_FILES` corpus.
5. The production `main.py` import graph and RAG service have no access to
   `safety/resources/**`.
6. History stores normalized spoken text, and TTS accepts only the explicit
   prosody vocabulary.
7. Existing Router/TurnPolicy, ScaleRuntime, SessionEngine, vLLM/Ollama,
   STT/TTS, report, and headless UI tests remain green.

## Freeze gate

At this point the repository has no Phase 6 production code change. The next
permitted production commit is exactly:

```text
refactor: simplify prompts rag and response protocol
```

That implementation must stay within the files and dispositions above. It
must not introduce Phase 7 cancellable sentence streaming, alter A100/vLLM or
STT/TTS architecture, or reopen the authority/state migrations accepted in
Phases 1–5.
