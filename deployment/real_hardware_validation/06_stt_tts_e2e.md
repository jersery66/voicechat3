# 06 — STT, TTS, and end-to-end audio

Do not add microphones or audio devices until the text-only Agent/dialogue
path and both Phase 5 gates are stable. Add components in this order:

1. FSMN-VAD + FunASR with synthetic/non-participant utterances;
2. VoxCPM2 + playback sink;
3. Windows GUI;
4. microphone-to-playback end to end.

## STT semantic fixtures

Record raw ASR, normalized text, semantic flags, and the resulting decision for
these fixed utterances:

```text
我晚上中途不醒
我晚上中途会醒
大概一周两三天
几乎每天
已经两个星期了
发生过两次
我也不知道多久
频率我记不太清
```

Clear negation, duration, frequency, and quantity must remain literal. Only
genuine malformed or explicitly unknown input should reach clarification.
The fixture contract is not real FunASR evidence.

## TTS and E2E

Measure, when available:

```text
LLM TTFT
first complete sentence
TTS first-audio
speech end -> first text
speech end -> first audio
full turn duration
```

Confirm that a blocked Pre-Delivery Guard sentence is not spoken and that its
safe fallback is delivered exactly once. Record real TTS/device results
separately from fixture evidence; do not claim acoustic acceptance from a
mock provider.
