# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**心医生 Heart Doctor** — a local-first AI voice counseling system for closed rehabilitation environments (drug rehab centers, mental health research). Conducts real-time voice conversations using Motivational Interviewing (MI), administers seamless psychological scales, recommends relaxation training, and generates structured reports.

**Language**: Python 100%. **Platform**: Windows with NVIDIA GPU (12GB+ VRAM recommended).

## Running

```bash
pip install -r requirements.txt
ollama pull qwen3.6:35b          # Default model (auto-detected)
python main.py                    # PySide6 desktop app
```

### Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v        # 72 tests, no external model deps
```

### Config Check

```bash
python scripts/check_config.py    # Validates Ollama, model paths, knowledge base
```

## Architecture

### Dual-Model Design

- **qwen3.6:35b** (`LLMService`) — primary counseling conversation, streaming. Auto-detected from installed Ollama models. Thinking mode disabled via `think=False`.
- **qwen3:8b** (`AgentService`) — lightweight classifier for intent, emotion, crisis risk. Falls back to keyword-based classification when unavailable.

### Conversation Pipeline

```
Microphone → STTService (FunASR) → AgentService (intent + emotion, parallel)
  → RAGService (knowledge lookup, truncated to 1200 chars)
  → LLMService (Ollama streaming, num_predict=120)
  → Response split on "|||" → Tag detection (END/REC/SCALE)
  → TTSService (VoxCPM2, play lock, non-blocking)
  → DataManager (save) → ReportGenerator (PDF with scale tables)
```

Orchestrated by `services/pipeline.py` (`ConversationPipeline`). Accepts `PipelineConfig`, returns `PipelineResult`. Thread-safe UI updates via `queue.Queue` + QTimer. No Qt dependency in pipeline.

### Key Files

| File | Role |
|------|------|
| `main.py` | PySide6 app entry, config check, model loading |
| `config.py` | All config: model paths, system prompt, thresholds, crisis hotlines |
| `services/pipeline.py` | Unified pipeline. Tag constants, regexes, `PipelineResult`, `ConversationPipeline.execute()` |
| `services/llm_service.py` | Ollama streaming chat, conversation history, auto-summarize, empty stream retry |
| `services/agent_service.py` | 3B model wrapper (OpenAI SDK). Intent, emotion, crisis, RAG routing |
| `services/scales.py` | Scale definitions (PHQ-9, GAD-7, PCL-5), `ScaleManager` with unified keywords |
| `services/report_service.py` | Session lifecycle, round counting, time limits, report generation |
| `services/report_generator.py` | PDF generation with scale results tables |
| `services/tts_service_voxcpm.py` | VoxCPM2 TTS with play lock, voice cloning |
| `services/rag_service.py` | Weighted keyword matching against knowledge base |
| `services/session_orchestrator.py` | Session state machine (IDLE→CHATTING→RELAXATION→VIDEO→POST_RELAX→SESSION_ENDING→SESSION_ENDED) |
| `services/session_end_controller.py` | End-of-session flow guard |
| `services/emotion_tracker.py` | Emotion trajectory tracking, intervention hints |
| `data/data_manager.py` | Hierarchical storage: profiles, sessions, reports by date/subject |
| `ui/main_window.py` | Main window, UI routing, session lifecycle, report generation thread |
| `ui/dialogs.py` | EndSessionDecisionDialog (4 states), CrisisDialog, etc. |
| `ui/chat_panel.py` | Chat bubbles, end session button, exit button |
| `knowledge_base/*.json` | Clinical psychology entries `{keywords, title, content}` |

### Critical Patterns

- **`|||` delimiter**: LLM output split at `|||` — left = clinical analysis (never shown), right = spoken reply (displayed + TTS).
- **Seamless scales**: PHQ-9/GAD-7/PCL-5 administered as natural conversation. `NATURAL_SCALE_QUESTIONS` dict provides conversational phrasing. `_build_active_scale_prompt()` enforces invisible-assessment rules (no "量表/题/评分" in output).
- **Scale scoring**: `[SCALE:PHQ-9:Q1:S2]` tags parsed by `parse_scale_tags()`. Fallback: `infer_scale_score_from_text()` maps "一半以上的天数"→2, etc.
- **Scale results**: `pipeline.get_scale_results()` exports structured data (per-item scores, totals, severity). Written to `researcher_report.json` and rendered in PDF.
- **Round gate**: `MIN_ROUNDS_BEFORE_SCALE = 5` — first N rounds are for rapport, no new scales started.
- **Scale completion**: When all queued scales finish, `result.all_scales_completed = True` triggers relaxation recommendation via `_recommend_relaxation_after_scales()`.
- **Greeting fast path**: "你好/嗨/哈喽" in first 2 rounds returns canned response instantly, skipping LLM.
- **TTS play lock**: `_play_lock` in TTSService prevents concurrent `generate_and_play` calls.
- **Non-blocking TTS**: Pipeline submits TTS via `add_done_callback`, does not wait for playback.
- **Report-first exit**: Exit path stops TTS, skips long farewell, generates report immediately. End-session path plays farewell TTS in background while reports generate in parallel.
- **Session lifecycle**: `_prepare_next_subject()` for cleanup (no new session). `_start_new_session()` only when next subject confirms info.
- **Empty stream retry**: If LLM returns empty stream, retries once non-streaming without stop sequences.
- **Stop sequences**: Only `["User:", "Visitor:", "用户:", "来访者:", "Human:"]`. No "Assistant:" or "薇薇老师:" (would cut off generation start).
- **Crisis detection**: Quick keyword check runs BEFORE programmatic scale branch. Negative emotions only for LLM crisis reassessment (not "happy").
- **RAG truncation**: `rag_suffix` capped at 1200 chars.
- **Pre-compiled regexes**: `_RE_REC_TAG`, `_RE_END_TAG`, `_RE_SCALE_TAG` (case-insensitive), `_RE_THINK`, etc.

## Key Configuration (`config.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OLLAMA_MODEL` | `qwen3.6:35b` | Auto-detected from installed models |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `MIN_ROUNDS_BEFORE_SCALE` | `5` | Rounds before new scale can start |
| `MIN_ROUNDS_FOR_RELAXATION` | `8` | Rounds before relaxation recommended |
| `MAX_CONVERSATION_ROUNDS` | `15` | Soft round limit (not enforced) |
| `MAX_CONVERSATION_MINUTES` | `45` | Hard time limit |
| `TIME_WARNING_MINUTES` | `40` | Warning before time limit |
| `POST_RELAXATION_TIMEOUT` | `60` | Seconds to wait after relaxation |
| `CRISIS_HOTLINES` | — | Emergency contacts |

## Control Tags

| Tag | Purpose |
|-----|---------|
| `[END_GOAL_ACHIEVED]` | Session end: goal achieved |
| `[END_TIME_LIMIT]` | Session end: time/round limit |
| `[END_SAFETY]` | Session end: safety risk |
| `[END_QUIT]` | Session end: user quit |
| `[REC_BREATHING]` | Recommend breathing exercise |
| `[REC_MUSCLE]` | Recommend muscle relaxation |
| `[REC_MEDITATION]` | Recommend meditation |
| `[REC_GAME]` | Recommend mini-game |
| `[SCALE:name:Q#:S#]` | Scale answer (case-insensitive) |
| `[breath]` / `[laughter]` | TTS prosody tags (kept for TTS, stripped from UI) |

## Development Rules

- Always run `python -m pytest tests/ -v` after changes
- `PipelineResult` is the single return type from pipeline — UI reads fields, never pipeline internals
- Scale tag regex is case-insensitive (`re.IGNORECASE`)
- Never expose clinical jargon (量表/评分/PHQ-9/题目) to participant-facing UI
- `ThreadPoolExecutor` must use `shutdown(wait=False, cancel_futures=True)`, never `with` statement (which blocks on timeout)
- TTS must never block pipeline return
- Report generation must never block on TTS playback
- Auto-push to GitHub after every task (use `-c http.proxy="" -c https.proxy=""` to bypass local proxy)

## External Dependencies (not in requirements.txt)

- **Ollama** at `http://localhost:11434` with `qwen3.6:35b` (counseling) and `qwen3:8b` (agent)
- **VoxCPM2** model files (auto-detected under `models/VoxCPM2/`)
- **FunASR** model files (auto-detected under `models/funasr/`)
- **Voice prompt audio** — reference WAV for TTS voice cloning
