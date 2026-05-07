# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**心医生 (Heart Doctor)** — an AI psychological counseling voice system for mandatory drug rehabilitation centers. Conducts real-time voice conversations using Motivational Interviewing (MI) techniques, monitors emotional states, triggers relaxation training videos, and generates clinical assessment reports.

**Language**: Python 100%. **Platform**: Windows with NVIDIA GPU (12GB+ VRAM recommended).

## Running the Application

```bash
pip install -r requirements.txt
ollama pull qwen2.5:72b
python main.py                # Main entry (Tkinter UI)
python debug_launch.py        # With crash logging to crash_debug.log
```

No test suite or CI/CD exists.

## Architecture

### Conversation Pipeline

```
Microphone → STTService (FunASR) → RAGService (intent routing + knowledge lookup)
  → LLMService (Ollama streaming, qwen2.5:72b) → Response split on "|||"
  → ReportService (emotion tracking, session lifecycle) → TTSService (FireRedTTS2 streaming)
  → DataManager (save audio/text) → VideoService (relaxation videos) → ReportGenerator (PDF)
```

### Key Files

- **`main.py`** — Monolithic Tkinter app (`VoiceChatApp`, ~2900 lines). UI + orchestration + threading.
- **`config.py`** — All configuration: model paths, Ollama settings, 140+ line system prompt with MI rules, audio params, UI dimensions, session limits, crisis hotlines.
- **`services/`** — Core service layer, each module uses singleton pattern (`_service = None` + `get_service()`).
- **`data/data_manager.py`** — Hierarchical storage: user profiles, session data, reports organized by date/subject ID.
- **`knowledge_base/*.json`** — Clinical psychology knowledge entries in `{keywords, title, content}` format.
- **`ui/`** — PyQt6 alternative UI (dark glassmorphism theme). **Not used in the main flow**; main app uses Tkinter.

### Critical Patterns

- **`|||` delimiter**: LLM responses split at `|||` — left side is clinical analysis (never shown/voiced), right side is the spoken reply played via TTS.
- **Session end detection**: Regex tags embedded in LLM output (`[END_SESSION:GOAL_ACHIEVED]`, `[END_SESSION:SAFETY]`, etc.) trigger session termination and report generation.
- **Relaxation triggers**: Tags like `[REC_BREATHING]`, `[REC_MUSCLE]`, `[REC_MEDITATION]` in LLM output cause fullscreen video playback via Pygame.
- **Emotion tags in TTS**: Tags like `<|emotion_comfort|>`, `<|breath|>` are inserted into text for FireRedTTS2 to synthesize with emotional prosody.
- **Streaming pipeline**: LLM streams chunk-by-chunk; TTS uses producer-consumer with 5-chunk pre-buffering before audio playback starts.
- **Drug-term correction**: `stt_service.py` corrects common ASR errors for drug-related terms (e.g., "西毒"→"吸毒", "冰读"→"冰毒").

### External Dependencies (not in requirements.txt)

- **Ollama** server at `http://localhost:11434` with `qwen2.5:72b` (or other model, configurable in `config.py`)
- **FireRedTTS2** model files at sibling directory `../FireRedTTS2/`
- **FunASR** model files at `../qwen/CosyVoice/pretrained_models/Fun-ASR-Nano-2512/`

## Key Configuration

All tunable parameters live in `config.py`:
- `OLLAMA_MODEL`, `OLLAMA_BASE_URL` — LLM backend selection
- System prompt (lines ~39-183) — MI counseling rules, OARS technique constraints, crisis protocols, TTS emotion tag specs
- `MAX_CONVERSATION_TURNS`, `SESSION_TIME_LIMIT_MINUTES` — session boundaries
- `CRISIS_HOTLINES` — emergency contact numbers
- Audio params: `SAMPLE_RATE`, `AUDIO_CHANNELS`, `TTS_*` settings

## Knowledge Base Format

JSON entries in `knowledge_base/` follow:
```json
{"keywords": ["失眠", "睡眠"], "title": "失眠干预方案", "content": "..."}
```

RAG service performs weighted keyword matching against these entries, injecting relevant context into the LLM system prompt.
