# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**薇薇老师 (WeiWei Teacher)** — an AI psychological counseling voice system for mandatory drug rehabilitation centers. Conducts real-time voice conversations using Motivational Interviewing (MI) techniques, monitors emotional states, triggers relaxation training videos, and generates clinical assessment reports.

**Language**: Python 100%. **Platform**: Windows with NVIDIA GPU (12GB+ VRAM recommended).

## Running the Application

```bash
pip install -r requirements.txt              # Full dependencies
pip install -r requirements-core.txt          # Lightweight (no GPU)
pip install -r requirements-gpu.txt           # GPU/model inference only
pip install -r requirements-media.txt         # Media download tools (optional)
ollama pull qwen2.5:72b
python main.py                                # Main entry (PySide6 UI)
# or: start_voicechat3.bat                    # Conda-based launcher
```

### Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v                    # Unit tests (no external deps required)
python scripts/test_rag.py                    # RAG matching smoke test (14 cases)
python scripts/test_conversation.py           # End-to-end RAG + LLM test (requires Ollama)
python debug_text_pipeline.py                 # Text-only pipeline integration test
```

### Config Health Check

```bash
python scripts/check_config.py   # Validates Ollama, model paths, knowledge base
```

## Architecture

### Layered Architecture

```
┌─────────────────────────────────────────────────────────┐
│  UI Layer (PySide6)                                     │
│  main_window → control_panel | chat_panel | dialogs      │
│  media_panel | session_review | stats_panel | widgets    │
├─────────────────────────────────────────────────────────┤
│  Pipeline Layer                                         │
│  pipeline.ConversationPipeline.execute()                │
│  STT → RAG → LLM → tag detection → TTS → post-process  │
├─────────────────────────────────────────────────────────┤
│  Session Layer                                          │
│  session_orchestrator (state machine)                   │
│  session_end_controller (guard state)                   │
│  emotion_tracker (sliding window)                       │
├─────────────────────────────────────────────────────────┤
│  Service Layer (singletons)                             │
│  llm | stt | tts | rag | agent | report | game | stats │
├─────────────────────────────────────────────────────────┤
│  Tool Layer (Protocol-based)                            │
│  video_tool | relaxation_tool | report_tool             │
├─────────────────────────────────────────────────────────┤
│  Data Layer                                             │
│  data_manager (hierarchical JSON) | treatment_progress  │
└─────────────────────────────────────────────────────────┘
```

### Conversation Pipeline

```
Microphone → STTService (FunASR) → RAGService (intent routing + knowledge lookup)
  → LLMService (Ollama streaming, qwen2.5:72b) → Response split on "|||"
  → AgentService (3B model: intent/emotion/relaxation detection)
  → ReportService (emotion tracking, session lifecycle)
  → TTSService (CosyVoice3/VoxCPM streaming + voice cloning)
  → DataManager (save audio/text) → VideoService (relaxation videos)
  → ReportGenerator (PDF) → GameService (therapeutic game)
```

### Session State Machine

```
IDLE → CHATTING → RELAXATION_RECOMMENDED → VIDEO_PLAYING
  → POST_RELAXATION → SESSION_ENDING → SESSION_ENDED
```

Managed by `SessionOrchestrator` with transition validation. `SessionEndController` prevents duplicate report generation, with support for relaxation deferral.

### Key Files

- **`main.py`** — PySide6 application entry point. Sets up logging, error monitor, High DPI scaling, loads MainWindow maximized.
- **`config.py`** — Central configuration (822 lines): path setup, model auto-detection (FunASR/CosyVoice), Ollama settings, 140+ line MI system prompt, audio params, UI dimensions, session limits, crisis hotlines, agent system messages, report generation prompts.
- **`services/pipeline.py`** — Unified conversation pipeline. Single source of truth for all tag constants (END_PATTERNS, REC_TAGS, SCALE_TAGS), text cleaning functions (`clean_for_display`, `clean_for_tts`), and `ConversationPipeline.execute()`.
- **`services/llm_service.py`** — Ollama-based LLM with streaming, in-memory conversation history, RAG/profile context injection, and graceful summarization/truncation when history grows too long.
- **`services/stt_service.py`** — FunASR speech-to-text with real-time mic recording (sounddevice), VAD-based auto-stop, drug-term correction (e.g., "西毒"→"吸毒", "冰读"→"冰毒").
- **`services/tts_service.py`** — Switcher that redirects to VoxCPM implementation. Alternative: `tts_service_cosyvoice.py` (CosyVoice3 with voice cloning).
- **`services/rag_service.py`** — RAG knowledge retrieval with jieba Chinese keyword extraction, synonym expansion for colloquial terms, weighted keyword matching against JSON knowledge base.
- **`services/agent_service.py`** — 3B model (qwen2.5:3b via OpenAI-compatible API) for intent classification, emotion detection, relaxation inference, report generation. Auto-fallback to keyword-based classification if 3B model unavailable. 60s availability cache with re-probe.
- **`services/report_service.py`** — Session lifecycle management: emotion tracking, round counting, time limits, composite ending judgment via EndType enum, dual-audience report generation (visitor feedback + researcher report).
- **`services/report_generator.py`** — PDF report generator using ReportLab with Chinese font support, structured tables, clinical formatting.
- **`services/emotion_tracker.py`** — Sliding window emotion trend tracking; detects 3+ consecutive rounds of escalating negative emotion.
- **`services/scales.py`** — Standard psychological scales: PHQ-9 (depression), GAD-7 (anxiety), PCL-5 (PTSD).
- **`services/session_orchestrator.py`** — Session lifecycle state machine (IDLE→CHATTING→RELAXATION_RECOMMENDED→VIDEO_PLAYING→POST_RELAXATION→SESSION_ENDING→SESSION_ENDED).
- **`services/session_end_controller.py`** — Guard-state controller preventing duplicate report generation, with relaxation deferral support.
- **`services/stats_service.py`** — Treatment effectiveness statistics aggregation across subjects and sessions from `*_progress.json` files.
- **`services/game_service.py`** — Therapeutic game wrapper that launches Pygame fullscreen and returns clinical summary metrics.
- **`services/video_service.py`** — Fullscreen video player (Pygame+moviepy) in kiosk mode; blocks until video finishes or Win+Esc.
- **`services/error_monitor.py`** — WARNING+ log aggregation to `logs/errors.jsonl` with in-memory ring buffer (200 entries).
- **`services/metrics.py`** — Performance metrics with `@measure()` decorator and `Metrics.timer()` context manager (200 samples per metric).
- **`services/_ollama_pool.py`** — Thread-safe singleton `ollama.Client` pool, shared by llm_service and report_service.
- **`services/logger.py`** — Unified logging: console at INFO, daily rotating file at DEBUG under `logs/`.
- **`services/tools/`** — Protocol-based tool layer: `video_tool` (relaxation videos), `relaxation_tool` (3B agent recommends type), `report_tool` (full report pipeline).
- **`data/data_manager.py`** — Hierarchical JSON storage: user profiles, session data, reports organized by date/subject ID under `voice_chat_data/`.
- **`data/treatment_progress.py`** — Cross-session longitudinal tracking; persists `{subject_id}_progress.json`.
- **`ui/main_window.py`** — Main window (1173 lines): background image, left-right split, loading-to-main transition, queue-based pipeline event processing, conversation flow, session coordination.
- **`ui/control_panel.py`** — Left panel: user info form (subject ID, age, gender), recording button, relaxation buttons (breathing/muscle/meditation/game/media).
- **`ui/chat_panel.py`** — Right panel: scrollable messages, text input (Enter send, Shift+Enter newline), AI status indicator.
- **`ui/dialogs.py`** — Modal dialogs: SessionEndDialog, CrisisDialog, ContinueOrEndDialog, WarningDialog.
- **`ui/media_panel.py`** — Media selector dialog organized by therapeutic scene (anxiety, depression, anger, sleep, etc.).
- **`ui/session_review.py`** — Researcher session replay with conversation history, emotion charts (matplotlib), audio playback.
- **`ui/stats_panel.py`** — Treatment effectiveness dashboard with tables and charts.
- **`ui/styles.py`** — Chinese ink-wash color palette and frosted glass QSS generation.
- **`ui/widgets.py`** — Custom widgets: FrostedPanel, RecordButton (pulse animation), BlinkButton, MessageBubble, StatusIndicator.
- **`game/`** — Standalone Pygame therapeutic game: Go/No-Go resource collection, storm-triggered 4-7-8 breathing exercises, 5-tier camp building system, Dynamic Difficulty Adjustment (DDA), clinical event tracking for CSV export.
- **`knowledge_base/*.json`** — Clinical psychology datasets: `knowledge.json` (core), `cpsycounr_converted.json` (15MB), `psyqa_converted.json` (84MB), `emollm_*.json` (emotional counseling). Format: `{keywords, title, content}`.
- **`media_library/`** — Therapeutic media asset library with 10 scene categories. Use `scripts/download_media.py` to populate.
- **`scripts/check_config.py`** — Pre-launch health check (Ollama, model paths, knowledge base, data directory).
- **`scripts/preprocess_knowledge_base.py`** — Enriches knowledge base keywords using jieba with custom psychology dictionary.
- **`scripts/download_media.py`** — Batch download relaxation music/videos from public-domain sources.
- **`offline_deploy/`** — Portable deployment bundle builder.

### Critical Patterns

- **`|||` delimiter**: LLM responses split at `|||` — left side is clinical analysis (never shown/voiced), right side is the spoken reply played via TTS.
- **Session end detection**: Regex tags embedded in LLM output (`[END_GOAL_ACHIEVED]`, `[END_TIME_LIMIT]`, `[END_SAFETY]`, etc.) trigger session termination and report generation.
- **Scale triggers**: Tags like `[SCALE_PHQ9]`, `[SCALE_GAD7]`, `[SCALE_PCL5]` in LLM output trigger psychological assessment questionnaires.
- **Relaxation triggers**: Tags like `[REC_BREATHING]`, `[REC_MUSCLE]`, `[REC_MEDITATION]` in LLM output cause fullscreen video playback via Pygame.
- **CosyVoice tags in TTS**: Native tags `[breath]` and `[laughter]` are embedded in text and processed by CosyVoice3 during synthesis. Tags are stripped from UI display text.
- **Streaming pipeline**: LLM streams chunk-by-chunk; TTS uses producer-consumer with 5-chunk pre-buffering before audio playback starts.
- **Streaming exception recovery**: If LLM streaming fails mid-response, partial output is persisted to conversation history so UI and context stay consistent.
- **Agent service recheck**: 3B model availability is cached for 60s, then re-probed — temporary failures don't permanently disable the agent.
- **Pre-compiled regexes**: All tag-stripping patterns in `pipeline.py` are compiled once at module level (`_RE_REC_TAG`, `_RE_END_TAG`, etc.) for hot-path performance.
- **Ollama client pool**: `services/_ollama_pool.py` provides a process-wide singleton `ollama.Client` per host, shared by llm_service and report_service.
- **Drug-term correction**: `stt_service.py` corrects common ASR errors for drug-related terms (e.g., "西毒"→"吸毒", "冰读"→"冰毒").
- **Model auto-detection**: `config.py` auto-discovers FunASR and CosyVoice model directories via environment variables, extensive search paths, and glob-based fallback.
- **Dual TTS backend**: `tts_service.py` is a switcher — defaults to VoxCPM (`tts_service_voxcpm.py`), alternative is CosyVoice3 (`tts_service_cosyvoice.py`).
- **Emotion escalation detection**: `emotion_tracker.py` uses a sliding window to detect 3+ consecutive rounds of escalating negative emotion, injecting intervention awareness into the LLM context.
- **EndType enum**: `report_service.py` uses composite ending judgment — different EndType values produce different report templates and session outcomes.
- **Dual-audience reports**: Every session generates two reports: visitor feedback (for the subject) and researcher report (for clinical staff), both via Ollama.
- **Therapeutic game**: `game/` implements a Go/No-Go resource collection game with 4-7-8 breathing exercises during "storm" events, 5-tier camp building (delayed gratification), DDA, and clinical event CSV export.

### External Dependencies (not in requirements.txt)

- **Ollama** server at `http://localhost:11434` with `qwen2.5:72b` (main LLM) and `qwen2.5:3b` (agent model, configurable)
- **CosyVoice3** model files — auto-detected by `config.py`, typical path: `../CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B-2512/`
- **FunASR** model files — auto-detected by `config.py`, typical path: `../qwen/CosyVoice/pretrained_models/Fun-ASR-Nano-2512/`

## Key Configuration

All tunable parameters live in `config.py` (822 lines):
- **Paths** (lines 1–27): `APP_ROOT`, `PROGRAM_ROOT`, `OFFLINE_MODELS_ROOT`, `OLLAMA_HOST`
- **Model auto-detection** (lines 28–100): FunASR/CosyVoice directory search with env var overrides (`FUNASR_MODEL_DIR`, `COSYVOICE_MODEL_DIR`) and glob fallback
- **LLM config** (lines 100–140): `OLLAMA_MODEL`, `OLLAMA_BASE_URL`, agent model selection
- **System prompt** (lines 140–280): MI counseling rules, OARS technique constraints, crisis protocols, CosyVoice tag specs
- **Audio/session/UI** (lines 280–500): `SAMPLE_RATE`, `AUDIO_CHANNELS`, `TTS_*` settings, `MAX_CONVERSATION_ROUNDS`, `SESSION_TIME_LIMIT_MINUTES`, `CRISIS_HOTLINES`, UI dimensions, greeting messages, relaxation parameters
- **Agent prompts** (lines 500–822): Intent classification, RAG routing, relaxation recommendation, emotion detection, crisis detection, report generation prompts, session ending prompt

## Knowledge Base Format

JSON entries in `knowledge_base/` follow:
```json
{"keywords": ["失眠", "睡眠"], "title": "失眠干预方案", "content": "..."}
```

RAG service performs weighted keyword matching with jieba extraction and synonym expansion. Use `scripts/preprocess_knowledge_base.py` to enrich keywords.

## Build & Deploy

```bash
# Create desktop shortcut with Ctrl+Alt+V hotkey
powershell -ExecutionPolicy Bypass -File create_shortcut.ps1

# Build portable offline deployment bundle
python offline_deploy/build.py
```
