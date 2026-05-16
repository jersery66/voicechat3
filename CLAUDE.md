# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**薇薇老师 (WeiWei Teacher)** — an AI psychological counseling voice system for mandatory drug rehabilitation centers. Conducts real-time voice conversations using Motivational Interviewing (MI) techniques, monitors emotional states, triggers relaxation training videos, and generates clinical assessment reports.

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
python -m pytest tests/ -v    # 66 unit tests (no external deps required)
```

### Config Health Check

```bash
python scripts/check_config.py   # Validates Ollama, model paths, knowledge base
```

## Architecture

### Conversation Pipeline

```
Microphone → STTService (FunASR) → RAGService (intent routing + knowledge lookup)
  → LLMService (Ollama streaming, qwen2.5:72b) → Response split on "|||"
  → ReportService (emotion tracking, session lifecycle) → TTSService (CosyVoice3 streaming + voice cloning)
  → DataManager (save audio/text) → VideoService (relaxation videos) → ReportGenerator (PDF)
```

### Key Files

- **`main.py`** — PySide6 application entry point. Runs config health check, then loads models and launches the main window.
- **`config.py`** — All configuration: model paths, Ollama settings, 140+ line system prompt with MI rules, audio params, UI dimensions, session limits, crisis hotlines.
- **`services/`** — Core service layer, each module uses singleton pattern (`_service = None` + `get_service()`).
- **`services/_ollama_pool.py`** — Shared `ollama.Client` singleton pool (avoids repeated handshakes across llm_service and report_service).
- **`services/error_monitor.py`** — WARNING+ log aggregation to `logs/errors.jsonl` with in-memory ring buffer.
- **`services/metrics.py`** — Performance metrics with `@measure()` decorator and `Metrics.timer()` context manager.
- **`services/logger.py`** — Unified logging configuration.
- **`data/data_manager.py`** — Hierarchical storage: user profiles, session data, reports organized by date/subject ID. Uses `_read_json`/`_write_json` abstractions.
- **`knowledge_base/*.json`** — Clinical psychology knowledge entries in `{keywords, title, content}` format.
- **`ui/`** — PySide6 UI (frosted glass theme, left-right split layout). Main application UI.
- **`scripts/check_config.py`** — Pre-launch health check (Ollama, model paths, knowledge base, data directory).
- **`tests/`** — Unit tests (pytest): pipeline tag detection, RAG scoring, data manager, report service.

### Critical Patterns

- **`|||` delimiter**: LLM responses split at `|||` — left side is clinical analysis (never shown/voiced), right side is the spoken reply played via TTS.
- **Session end detection**: Regex tags embedded in LLM output (`[END_GOAL_ACHIEVED]`, `[END_TIME_LIMIT]`, `[END_SAFETY]`, etc.) trigger session termination and report generation.
- **Relaxation triggers**: Tags like `[REC_BREATHING]`, `[REC_MUSCLE]`, `[REC_MEDITATION]` in LLM output cause fullscreen video playback via Pygame.
- **CosyVoice tags in TTS**: Native tags `[breath]` and `[laughter]` are embedded in text and processed by CosyVoice3 during synthesis. Tags are stripped from UI display text.
- **Streaming pipeline**: LLM streams chunk-by-chunk; TTS uses producer-consumer with 5-chunk pre-buffering before audio playback starts.
- **Streaming exception recovery**: If LLM streaming fails mid-response, partial output is persisted to conversation history so UI and context stay consistent.
- **Agent service recheck**: 3B model availability is cached for 60s, then re-probed — temporary failures don't permanently disable the agent.
- **Pre-compiled regexes**: All tag-stripping patterns in `pipeline.py` are compiled once at module level (`_RE_REC_TAG`, `_RE_END_TAG`, etc.) for hot-path performance.
- **Ollama client pool**: `services/_ollama_pool.py` provides a process-wide singleton `ollama.Client` per host, shared by llm_service and report_service.
- **Drug-term correction**: `stt_service.py` corrects common ASR errors for drug-related terms (e.g., "西毒"→"吸毒", "冰读"→"冰毒").

### External Dependencies (not in requirements.txt)

- **Ollama** server at `http://localhost:11434` with `qwen2.5:72b` (or other model, configurable in `config.py`)
- **CosyVoice3** model files at `../CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B-2512/`
- **FunASR** model files at `../qwen/CosyVoice/pretrained_models/Fun-ASR-Nano-2512/`

## Key Configuration

All tunable parameters live in `config.py`:
- `OLLAMA_MODEL`, `OLLAMA_BASE_URL` — LLM backend selection
- System prompt (lines ~143-268) — MI counseling rules, OARS technique constraints, crisis protocols, CosyVoice tag specs
- `MAX_CONVERSATION_TURNS`, `SESSION_TIME_LIMIT_MINUTES` — session boundaries
- `CRISIS_HOTLINES` — emergency contact numbers
- Audio params: `SAMPLE_RATE`, `AUDIO_CHANNELS`, `TTS_*` settings

## Knowledge Base Format

JSON entries in `knowledge_base/` follow:
```json
{"keywords": ["失眠", "睡眠"], "title": "失眠干预方案", "content": "..."}
```

RAG service performs weighted keyword matching against these entries, injecting relevant context into the LLM system prompt.
