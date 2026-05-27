# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**薇薇老师 (WeiWei Teacher)** — an AI psychological counseling voice system for mandatory drug rehabilitation centers. Conducts real-time voice conversations using Motivational Interviewing (MI) techniques, monitors emotional states, triggers relaxation training videos, and generates clinical assessment reports.

**Language**: Python 100%. **Platform**: Windows with NVIDIA GPU (12GB+ VRAM recommended).

## Running the Application

```bash
pip install -r requirements.txt        # Full install (or split: requirements-core.txt + requirements-gpu.txt)
ollama pull qwen2.5:72b
python main.py                         # Main entry (PySide6 UI)
```

### Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v             # Unit tests (no external model deps required)
```

### Config Health Check

```bash
python Scripts/check_config.py         # Validates Ollama, model paths, knowledge base
```

## Architecture

### Dual-Model Design

The system uses two Ollama models simultaneously:
- **qwen2.5:72b** (`LLMService`) — primary counseling conversation, streaming responses
- **qwen3:8b** (`AgentService`) — lightweight classifier for intent, emotion, crisis risk, RAG routing. Falls back to keyword-based classification when the 3B model is unavailable. Availability cached for 60s before re-probe.

### Conversation Pipeline

```
Microphone → STTService (FunASR) → AgentService (intent + emotion, parallel 3B calls)
  → RAGService (knowledge lookup) → LLMService (Ollama streaming, qwen2.5:72b)
  → Response split on "|||" → Tag detection (end/relaxation/scale)
  → TTSService (VoxCPM2 streaming + voice cloning)
  → DataManager (save audio/text) → VideoService (relaxation videos) → ReportGenerator (PDF)
```

Orchestrated by `services/pipeline.py` (`ConversationPipeline`) which accepts a `PipelineConfig` controlling STT/TTS on/off and an `emit` callback for thread-safe UI updates. No Qt dependency in the pipeline itself.

### Key Files

- **`main.py`** — PySide6 application entry point. Runs config health check, then loads models and launches the main window.
- **`config.py`** — All configuration: model paths, Ollama settings, 250+ line system prompt with MI rules, agent system messages (intent/emotion/crisis/RAG routing classifiers), audio params, UI dimensions, session limits, crisis hotlines, media scene mappings.
- **`services/pipeline.py`** — Unified conversation pipeline. Single source of truth for tag constants (`END_PATTERNS`, `REC_TAGS`), pre-compiled regexes, `PipelineResult`/`PipelineConfig` dataclasses, and `ConversationPipeline.execute()`.
- **`services/agent_service.py`** — 3B model wrapper via OpenAI SDK (`/v1/chat/completions`). Handles intent classification, emotion detection, crisis assessment, RAG routing, relaxation classification. Keyword fallback for each when 3B is down.
- **`services/llm_service.py`** — Ollama streaming chat with conversation history management (auto-summarize at 20 turns).
- **`services/tts_service.py`** — Redirects to `tts_service_voxcpm.py` (VoxCPM2 TTS with voice cloning from a reference audio prompt).
- **`services/stt_service.py`** — FunASR speech-to-text with VAD (voice activity detection) and drug-term ASR correction (e.g., "西毒"→"吸毒").
- **`services/_ollama_pool.py`** — Shared `ollama.Client` singleton pool per host, avoiding repeated handshakes.
- **`services/report_service.py`** — Session lifecycle (round counting, time limits, `EndType` enum) and emotion tracking.
- **`services/session_orchestrator.py`** / **`session_end_controller.py`** — Session state machine and end-of-session flow (dialog, report generation, data save).
- **`services/rag_service.py`** — Weighted keyword matching against knowledge base, injects relevant context into LLM system suffix.
- **`services/scales.py`** — Clinical scale administration (PHQ-9, GAD-7, etc.) embedded in conversation via `[SCALE:name:Q#:S#]` tags.
- **`services/emotion_tracker.py`** — Tracks emotion trajectory across session, provides intervention hints.
- **`data/data_manager.py`** — Hierarchical storage: user profiles, session data, reports organized by date/subject ID.
- **`knowledge_base/*.json`** — Clinical psychology knowledge entries in `{keywords, title, content}` format.
- **`ui/`** — PySide6 UI (frosted glass theme, left-right split layout). `MainWindow` orchestrates service init, model loading, and session flow.
- **`game/`** — Pygame-based mini-game for entertainment breaks. `GameEngine` runs fullscreen with `ClinicalTracker` for gameplay metrics.
- **`Scripts/check_config.py`** — Pre-launch health check (Ollama, model paths, knowledge base, data directory).
- **`tests/`** — Unit tests (pytest): pipeline tag detection, RAG scoring, data manager, report service, session end controller.

### Critical Patterns

- **`|||` delimiter**: LLM responses split at `|||` — left side is clinical analysis (never shown/voiced), right side is the spoken reply played via TTS.
- **Session end detection**: Regex tags in LLM output (`[END_GOAL_ACHIEVED]`, `[END_TIME_LIMIT]`, `[END_SAFETY]`, `[END_QUIT]`, `[END_INVALID]`) trigger session termination and report generation.
- **Relaxation triggers**: Tags `[REC_BREATHING]`, `[REC_MUSCLE]`, `[REC_MEDITATION]`, `[REC_GAME]` in LLM output cause fullscreen video playback or game launch.
- **Scale tags**: `[SCALE:name:Q#:S#]` tags record clinical assessment answers embedded in conversation.
- **CosyVoice tags in TTS**: Native tags `[breath]` and `[laughter]` are preserved for TTS but stripped from UI display text. Two cleaning functions: `clean_for_display()` strips all tags, `clean_for_tts()` keeps breath/laughter.
- **Streaming pipeline**: LLM streams chunk-by-chunk; TTS uses producer-consumer with pre-buffering before audio playback starts.
- **Streaming exception recovery**: If LLM streaming fails mid-response, partial output is persisted to conversation history so UI and context stay consistent.
- **Pre-compiled regexes**: All tag-stripping patterns in `pipeline.py` are compiled once at module level (`_RE_REC_TAG`, `_RE_END_TAG`, etc.) for hot-path performance.
- **Drug-term correction**: `stt_service.py` corrects common ASR errors for drug-related terms.
- **Thread safety**: Pipeline uses a long-lived `ThreadPoolExecutor` (2 workers) for parallel intent/emotion classification. UI updates go through a `queue.Queue` consumed by a QTimer.

### External Dependencies (not in requirements.txt)

- **Ollama** server at `http://localhost:11434` with `qwen2.5:72b` (counseling) and `qwen3:8b` (agent classification)
- **VoxCPM2** model files (auto-detected under `models/VoxCPM2/` or sibling directories)
- **FunASR** model files (auto-detected under `models/funasr/` or sibling directories)
- **Voice prompt audio** — reference audio for TTS voice cloning (auto-detected from `data/` directory)

## Key Configuration

All tunable parameters live in `config.py`:
- `OLLAMA_MODEL`, `OLLAMA_HOST` — LLM backend selection
- `AGENT_MODEL`, `AGENT_MODEL_SERVER` — 3B classifier backend
- System prompt (~250 lines) — MI counseling rules, OARS technique constraints, crisis protocols, CosyVoice tag specs, session end conditions
- `MAX_CONVERSATION_ROUNDS`, `MAX_CONVERSATION_MINUTES` — session boundaries
- `CRISIS_HOTLINES` — emergency contact numbers
- `MIN_ROUNDS_FOR_RELAXATION` — minimum dialogue rounds before recommending relaxation
- Audio params: `SAMPLE_RATE`, `CHANNELS`, `CHUNK_SIZE`, `TTS_SAMPLE_RATE`
- `DATA_ROOT` — session data storage (overridable via `VOICECHAT_DATA_DIR` env var)

## Knowledge Base Format

JSON entries in `knowledge_base/` follow:
```json
{"keywords": ["失眠", "睡眠"], "title": "失眠干预方案", "content": "..."}
```

RAG service performs weighted keyword matching against these entries, injecting relevant context into the LLM system prompt.
