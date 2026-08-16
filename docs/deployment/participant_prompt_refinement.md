# Participant-facing prompt refinement

## Scope and baseline

This deployment-hardening change refines participant-facing wording only. It
does not change the delivery pipeline, generation lifecycle, action authority,
scale state, session lifecycle, RAG gate, speech providers, or model profiles.

- Starting HEAD: `0bf0bb067283a842dff2714b03ac086166d1067d`
- Production scope: `config.py` and `services/agent_service.py`
- Test scope: `tests/test_participant_prompt_contract.py`

## Prompt contract

- The system prompt identifies 小薇 as a `心理支持对话助手` and states that the
  current action is already selected by the system.
- The language model is explicitly forbidden from re-deciding chat, scales,
  relaxation, games, or session ending, and from selecting scale items,
  assigning scores, or advancing scale state.
- Scale wording preserves time range, frequency, negation, and core symptom
  meaning. Ambiguity in presence/negation, frequency, duration, numbers,
  medication, or scale answers requires a brief confirmation rather than a
  guess.
- Responses are limited to one primary question per turn, avoid diagnosis or
  treatment/medication decisions, and use neutral non-leading post-relaxation
  wording.
- Static and dynamic participant prompts no longer request audio markers,
  control tags, prior-relationship assumptions, or distress/benefit outcomes.
- The router example now describes the configured minimum-round threshold
  instead of implying that a scale starts on a fixed early round.

## Authority and deployment preservation

TurnPolicy/TurnDecision remains the only per-turn action authority; ScaleRuntime
still owns questionnaire state; SessionEngine still owns session lifecycle;
RAG remains decision-gated; and the model only realizes an already-approved
language task. No STT, FSMN-VAD, TTS, delivery, vLLM, A100 profile, or provider
code was changed.

## Verification

- Participant prompt contract: **15 passed**.
- Phase 6 prompt boundary and frozen TTS acceptance slice: **53 passed**.
- Full regression: **713 passed / 0 failed** (`python -m pytest tests -q`).
- `git diff --check`: PASS.
- Real Qwen/vLLM, FunASR, VoxCPM2/CosyVoice, and microphone validation:
  **NOT RUN / environment unavailable**.

This change intentionally stops at prompt refinement. Dialogue-model migration
and any subsequent deployment experiment are separate work.
