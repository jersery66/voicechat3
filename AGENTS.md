# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**小薇 (Heart Doctor)** — an AI psychological counseling voice system for mandatory drug rehabilitation centers. Conducts real-time voice conversations using Motivational Interviewing (MI) techniques, monitors emotional states, triggers relaxation training, and generates clinical assessment reports.

**Language**: Python 100%. **Platform**: Windows with NVIDIA GPU (12GB+ VRAM recommended).

## Running the Application

```bash
pip install -r requirements.txt
ollama pull qwen2.5:72b
python main.py                # Main entry (PySide6 UI)
```

### Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v    # 172 unit tests (no external deps required)
```

### Config Health Check

```bash
python scripts/check_config.py   # Validates Ollama, model paths, knowledge base
```

## Architecture

### Conversation Pipeline

```
Microphone → STTService (FunASR) → ASR correction
  → AgentService (unified routing: chat/start_scale/continue_scale/recommend_relaxation)
  → Symptom signal scoring (cumulative per-scale: PHQ-9, GAD-7, PCL-5)
  → RAGService (knowledge lookup, truncated 500-1200 chars)
  → LLMService (Ollama streaming, qwen2.5:72b, num_predict=1024)
  → Response split on "|||" (handles reversed format)
  → Tag detection (END/REC/SCALE) + internal tag leak protection
  → Scale sync with spoken_text
  → TTSService (VoxCPM2, play lock, non-blocking)
  → DataManager (save) → ReportGenerator (PDF with scale tables)
```

### Key Files

- **`main.py`** — PySide6 application entry point. Runs config health check, then loads models and launches the main window.
- **`config.py`** — All configuration: model paths, system prompt, thresholds, crisis hotlines, `AGENT_ROUTE_ENABLED`, `ENABLE_SCALE_HARD_TRIGGER`.
- **`services/pipeline.py`** — Unified pipeline. `ConversationPipeline.execute()` orchestrates everything. Key patterns: cumulative symptom scoring, agent routing, scale state machine, relaxation sync, ASR correction, internal tag leak protection.
- **`services/llm_service.py`** — Ollama streaming chat. `_history_visible_text()` keeps conversation history clean. `LLMRawFull` logging for debugging. Thinking mode support with `reasoning_buffer`.
- **`services/agent_service.py`** — 3B model wrapper (OpenAI SDK). `route_conversation_actions()` returns unified JSON: `{action, scale, item, confidence, reason}`. Field mapping handles new (`action`) and old (`scale_action`) formats. `_parse_json_loose()` handles ```json fences.
- **`services/scales.py`** — Scale definitions (PHQ-9, GAD-7, PCL-5). `ScaleManager` with unified keyword lists (`DEPRESSION_KW`, `ANXIETY_KW`, `TRAUMA_KW`).
- **`services/report_service.py`** — Session lifecycle, round counting, time limits. `time_limit_prompt_shown` + `continued_after_time_limit` prevent repeated dialogs.
- **`services/report_generator.py`** — PDF generation with scale results tables (section 三、量表评估结果).
- **`services/tts_service_voxcpm.py`** — VoxCPM2 TTS with `_play_lock` (threading.Lock) preventing concurrent playback.
- **`services/rag_service.py`** — Weighted keyword matching. RAG truncated to 500 chars during active scale, 1200 otherwise.
- **`services/session_orchestrator.py`** — Session state machine: IDLE→CHATTING→RELAXATION_RECOMMENDED→VIDEO_PLAYING→POST_RELAXATION→SESSION_ENDING→SESSION_ENDED.
- **`data/data_manager.py`** — Hierarchical storage by date/subject ID.
- **`ui/main_window.py`** — Main window. Key methods: `_run_pipeline()`, `_post_pipeline_routing()`, `_handle_session_end()`, `_request_end_with_readiness_check()`, `_on_session_finished()`.
- **`ui/dialogs.py`** — `EndSessionDecisionDialog` (4 states), `CrisisDialog`, `ContinueOrEndDialog`.
- **`ui/chat_panel.py`** — WeChat-style layout (top spacer, messages pinned to bottom). `_on_scroll_range_changed` for auto-scroll.
- **`knowledge_base/*.json`** — Clinical psychology entries `{keywords, title, content}`.

### Critical Patterns

- **`|||` delimiter**: LLM output split at `|||` — left = analysis, right = spoken. Handles reversed format (spoken|||analysis) automatically.
- **Unified Agent routing**: `route_conversation_actions()` returns `{action, scale, item, confidence, reason}`. Actions: `chat/start_scale/continue_scale/recommend_relaxation/recommend_game/exit`.
- **Cumulative symptom scoring**: `score_symptom_signals()` returns per-scale deltas (PHQ-9/GAD-7/PCL-5). Threshold ≥3 triggers scale. Agent timeout doesn't block.
- **Scale control ownership**: Agent decides WHEN to enter scale. Pipeline controls WHICH item. LLM generates natural questions from `NATURAL_SCALE_QUESTIONS` + `SCALE_ITEM_CORES`.
- **Relaxation rules**: Once per session (`relaxation_used`), not during `waiting_scale_answer`, only between items.
- **Scale scoring**: `[SCALE:PHQ-9:Q1:S2]` tags (case-insensitive). `infer_scale_score_from_text()` with item-aware matching. `_score_short_scale_answer()` for "经常/没有/嗯".
- **ASR correction**: `correct_asr_text()` fixes common errors (进场→经常, 又→有 for single-char).
- **Internal tag leak protection**: `_FORBIDDEN_INTERNAL_TERMS` strips "高防御/情感反映/具体化开放式提问/PHQ-9" from spoken output.
- **TTS play lock**: `_play_lock` prevents concurrent `generate_and_play`.
- **Report-first exit**: Exit stops TTS, skips farewell, generates report. End-session plays farewell AFTER report/PDF.
- **Session lifecycle**: `_prepare_next_subject()` for cleanup. `_start_new_session()` only when next subject confirms.
- **LLM duplicate output**: `|||` split handles reversed format. Duplicate analysis tags truncated.
- **Stop sequences**: Only `["User:", "Visitor:", "用户:", "来访者:", "Human:"]`.

## Key Configuration

All tunable parameters live in `config.py`:
- `OLLAMA_MODEL`, `OLLAMA_HOST` — LLM backend
- `AGENT_ROUTE_ENABLED` — enable/disable AgentRoute
- `AGENT_TIMEOUT` — agent call timeout (3s)
- `ENABLE_SCALE_HARD_TRIGGER` — deterministic trigger (disabled)
- `MIN_ROUNDS_BEFORE_SCALE` — rounds before scale (5)
- `MIN_ROUNDS_FOR_RELAXATION` — rounds before relaxation (8)
- `MAX_CONVERSATION_MINUTES` — hard time limit (45)
- `CRISIS_HOTLINES` — emergency contacts
- `SYSTEM_PROMPT` — MI counseling rules, output format, ASR tolerance

## Knowledge Base Format

JSON entries in `knowledge_base/` follow:
```json
{"keywords": ["失眠", "睡眠"], "title": "失眠干预方案", "content": "..."}
```

RAG service performs weighted keyword matching against these entries, injecting relevant context into the LLM system prompt.

## Development Rules

- Run `python -m pytest tests/ -v` after changes
- Agent only decides WHEN to enter scale; Pipeline controls WHICH item
- Relaxation: once per session, not during waiting_scale_answer
- Never expose clinical jargon to participant-facing UI
- TTS must never block pipeline return
- Auto-push to GitHub after every task (use `-c http.proxy="" -c https.proxy=""`)
