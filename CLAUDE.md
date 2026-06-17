# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**小薇 (Heart Doctor)** — a local-first AI voice counseling system for closed rehabilitation environments (drug rehab centers, mental health research). Conducts real-time voice conversations using Motivational Interviewing (MI), administers seamless psychological scales, recommends relaxation training, and generates structured reports.

**Language**: Python 100%. **Platform**: Windows with NVIDIA GPU (12GB+ VRAM recommended).

## Running

```bash
pip install -r requirements.txt
ollama pull qwen2.5:72b          # Default model (auto-detected)
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

- **qwen2.5:72b** (`LLMService`) — primary counseling conversation, streaming. Auto-detected from installed Ollama models.
- **qwen3:8b** (`AgentService`) — lightweight classifier for intent, emotion, crisis risk, scale routing. Falls back to keyword-based classification when unavailable.

### Conversation Pipeline

```
Microphone → STTService (FunASR) → ASR correction
  → AgentService (unified routing: chat/start_scale/continue_scale/recommend_relaxation)
  → Symptom signal scoring (cumulative per-scale: PHQ-9, GAD-7, PCL-5)
  → RAGService (knowledge lookup, truncated to 500-1200 chars)
  → LLMService (Ollama streaming, num_predict=1024)
  → Response split on "|||" (handles reversed format)
  → Tag detection (END/REC/SCALE) + internal tag leak protection
  → Scale sync with spoken_text
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
| `services/agent_service.py` | 3B model wrapper (OpenAI SDK). Unified routing: intent, emotion, crisis, scale decisions |
| `services/scales.py` | Scale definitions (PHQ-9, GAD-7, PCL-5), `ScaleManager` with unified keywords |
| `services/report_service.py` | Session lifecycle, round counting, time limits, report generation |
| `services/report_generator.py` | PDF generation with scale results tables |
| `services/tts_service_voxcpm.py` | VoxCPM2 TTS with play lock, voice cloning |
| `services/rag_service.py` | Weighted keyword matching against knowledge base |
| `services/session_orchestrator.py` | Session state machine |
| `services/session_end_controller.py` | End-of-session flow guard |
| `services/emotion_tracker.py` | Emotion trajectory tracking, intervention hints |
| `data/data_manager.py` | Hierarchical storage: profiles, sessions, reports by date/subject |
| `ui/main_window.py` | Main window, UI routing, session lifecycle, report generation thread |
| `ui/dialogs.py` | EndSessionDecisionDialog, CrisisDialog, etc. |
| `ui/chat_panel.py` | Chat bubbles, end session button, exit button |
| `knowledge_base/*.json` | Clinical psychology entries `{keywords, title, content}` |

### Critical Patterns

- **`|||` delimiter**: LLM output split at `|||` — left = clinical analysis (never shown), right = spoken reply (displayed + TTS). Handles reversed format automatically.
- **Unified Agent routing**: `route_conversation_actions()` returns `{action, scale, item, confidence, reason}`. Actions: `chat/start_scale/continue_scale/recommend_relaxation/recommend_game/exit`. Field mapping handles both new (`action`) and old (`scale_action`) formats.
- **Cumulative symptom scoring**: `score_symptom_signals()` returns per-scale deltas (PHQ-9/GAD-7/PCL-5). Threshold ≥3 triggers scale start. Agent timeout doesn't block triggering.
- **Scale control ownership**: Agent decides WHEN to enter scale. Pipeline controls WHICH item. LLM generates natural questions from `NATURAL_SCALE_QUESTIONS` + `SCALE_ITEM_CORES`.
- **Relaxation rules**: Once per session (`relaxation_used`), not during `waiting_scale_answer`, only between items. Held via `_pending_relaxation_after_scale` during active scale.
- **Scale scoring**: `[SCALE:PHQ-9:Q1:S2]` tags parsed by `parse_scale_tags()` (case-insensitive). Fallback: `infer_scale_score_from_text()` with item-aware symptom matching. Short answer scorer for "经常/没有/嗯" etc.
- **ASR correction**: `correct_asr_text()` fixes common errors (进场→经常, 又→有 for single-char responses).
- **Internal tag leak protection**: `_FORBIDDEN_INTERNAL_TERMS` strips "高防御/情感反映/具体化开放式提问/PHQ-9" etc. from spoken output. Replaces with `_make_scale_clarify_reply()`.
- **LLM duplicate output**: `|||` split handles reversed format. Duplicate analysis tags in spoken_text truncated.
- **TTS play lock**: `_play_lock` in TTSService prevents concurrent `generate_and_play` calls.
- **Non-blocking TTS**: Pipeline submits TTS via `add_done_callback`, does not wait for playback.
- **Report-first exit**: Exit path stops TTS, skips long farewell, generates report immediately. End-session path plays farewell TTS AFTER report/PDF completes.
- **Session lifecycle**: `_prepare_next_subject()` for cleanup (no new session). `_start_new_session()` only when next subject confirms info.
- **Empty stream retry**: If LLM returns empty stream, retries once non-streaming. Graceful fallback if retry also fails.
- **Stop sequences**: Only `["User:", "Visitor:", "用户:", "来访者:", "Human:"]`. No "Assistant:" or model names.
- **Crisis detection**: Quick keyword check runs BEFORE agent route. Negative emotions only for LLM crisis reassessment.
- **RAG truncation**: `rag_suffix` capped at 500 chars during active scale, 1200 otherwise.

## Key Configuration (`config.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OLLAMA_MODEL` | `qwen2.5:72b` | Auto-detected from installed models |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `AGENT_ROUTE_ENABLED` | `True` | Enable AgentRoute for scale/relaxation decisions |
| `AGENT_TIMEOUT` | `3` | Agent call timeout (seconds) |
| `ENABLE_SCALE_HARD_TRIGGER` | `False` | Disable deterministic trigger (let agent control) |
| `MIN_ROUNDS_BEFORE_SCALE` | `5` | Rounds before new scale can start |
| `MIN_ROUNDS_FOR_RELAXATION` | `8` | Rounds before relaxation recommended |
| `MAX_CONVERSATION_MINUTES` | `45` | Hard time limit |
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
- `ThreadPoolExecutor` must use `shutdown(wait=False, cancel_futures=True)`, never `with` statement
- TTS must never block pipeline return
- Report generation must never block on TTS playback
- Agent only decides WHEN to enter scale; Pipeline controls WHICH item
- Relaxation: once per session, not during waiting_scale_answer, only between items
- Auto-push to GitHub after every task (use `-c http.proxy="" -c https.proxy=""` to bypass local proxy)

## External Dependencies (not in requirements.txt)

- **Ollama** at `http://localhost:11434` with `qwen2.5:72b` (counseling) and `qwen3:8b` (agent)
- **VoxCPM2** model files (auto-detected under `models/VoxCPM2/`)
- **FunASR** model files (auto-detected under `models/funasr/`)
- **Voice prompt audio** — reference WAV for TTS voice cloning
