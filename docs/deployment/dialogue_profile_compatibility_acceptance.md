# Dialogue model profile compatibility acceptance

## Scope and commits

This is the final deterministic compatibility freeze for dialogue deployment
profiles. It is not Qwen3.8 A100 promotion or real-model acceptance.

- Baseline HEAD: `ddcce89970204141fff0f0ded0519110b92e421c`
- Narrow endpoint fix discovered by the gate: `2a4d9ed3b94cfe82821670734980e62fae0c9a7c`
- Production baseline remains `a100_80g` with
  `Qwen/Qwen2.5-72B-Instruct-AWQ`.
- Candidate remains explicit opt-in `a100_80g_qwen38_candidate` with
  `Qwen/Qwen3.8-27B-FP8`.

## Supported profile matrix

| Profile | Dialogue | Router/Agent | Endpoint behavior |
| --- | --- | --- | --- |
| `dev_6g` | `qwen2.5:3b` | local compatibility | Ollama/dev overrides remain supported |
| `dev_vllm_6g` | `gemma-2b-awq` | `qwen2.5:3b` | explicit vLLM endpoint override allowed |
| `a100_80g` | `Qwen/Qwen2.5-72B-Instruct-AWQ` | `Qwen/Qwen2.5-3B-Instruct-AWQ` | pinned `127.0.0.1:8000`, Agent `:8001` |
| `a100_80g_qwen38_candidate` | `Qwen/Qwen3.8-27B-FP8` | `Qwen/Qwen2.5-3B-Instruct-AWQ` | pinned `127.0.0.1:8000`, Agent `:8001` |

Both `inference.factory.build_dialogue_client()` and
`services.llm_factory.build_llm_service()` now apply the same A100 endpoint
immutability rule. Model and router environment overrides cannot replace the
pinned A100 roles. Dev profiles retain their deliberate override behavior.

## Generation contracts

- Qwen2.5 baseline requests retain `temperature=0.35` and `top_p=0.8` and do
  not receive candidate-only `top_k`, `presence_penalty`, or chat-template
  options.
- Qwen3.8 candidate requests use profile-owned `temperature=0.7`,
  `top_p=0.8`, `top_k=20`, and `presence_penalty=1.5`.
- Candidate streaming and non-streaming requests both send
  `chat_template_kwargs.enable_thinking=False` through `extra_body`.
- The client preserves complete message history and consumes only
  participant-facing `delta.content`; reasoning fields are not output or
  written into dialogue history.
- The Qwen3.8 candidate does not enable `preserve_thinking`; that behavior was
  not independently verified and is not part of this application contract.

## Frozen boundaries

Participant prompts, Router/TurnPolicy authority, ScaleRuntime,
SessionEngine, RAG, STT/FSMN-VAD, TTS/delivery, and provider architecture are
unchanged. The production Qwen2.5 profile remains available and is not
redirected to Qwen3.8.

## Verification

- Compatibility matrix: **17 passed**.
- Existing vLLM backend regression: **10 passed**.
- Full regression after the freeze: **745 passed / 0 failed**
  (`python -m pytest tests -q`).
- Production promotion: **NOT APPROVED**.
- Qwen3.8 A100 runtime acceptance: **PENDING / NOT RUN**.
- Model download, vLLM boot, VRAM, TTFT, tokens/sec, 20–30-turn stability,
  real non-thinking behavior, dialogue quality, and real STT/TTS interaction:
  **NOT RUN / environment unavailable**.
