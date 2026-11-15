# Qwen3.8 dialogue deployment candidate

## Status

This is an explicit, reversible candidate *configuration*. It does not replace
the production runtime configuration.

Production promotion: **NOT APPROVED**.

| Configuration | Dialogue model | Agent | Status |
| --- | --- | --- | --- |
| `config/runtime.yaml` | `Qwen/Qwen2.5-72B-Instruct-AWQ` | `Qwen/Qwen2.5-3B-Instruct-AWQ` | production baseline |
| `config/runtime_qwen38.yaml` (operator copy) | `Qwen/Qwen3.8-27B-FP8` | `Qwen/Qwen2.5-3B-Instruct-AWQ` | explicit opt-in candidate |

Select the candidate only deliberately, by pointing
`VOICECHAT_RUNTIME_CONFIG` at the candidate file. No automatic GPU or model
detection promotes it, and while `immutable_contract: true` the legacy
environment overrides cannot replace the pinned dialogue or agent models.

## Request contract

The candidate keeps the same text-only dialogue path, frozen participant prompt,
TurnPolicy, ScaleRuntime, SessionEngine, RAG gate, sentence delivery, STT, and
TTS. It changes only the dialogue checkpoint and its typed generation options:

- `temperature=0.7`, `top_p=0.8`, and `presence_penalty=1.5`, following the
  official Qwen3.8 non-thinking example as an unvalidated candidate setting;
- vLLM `extra_body.top_k=20`;
- `extra_body.chat_template_kwargs.enable_thinking=False` for both streaming
  and non-streaming dialogue requests;
- no `preserve_thinking` and no participant-facing reasoning-content path;
- the client yields final `delta.content` only.

A configuration that does not set Qwen3.8-specific `chat_template_kwargs` or
`top_k` does not send them; those parameters exist only when the configuration
sets the typed fields explicitly.

## Candidate launch shape

The intended single-A100 text-only shape is conceptually:

```text
vllm serve Qwen/Qwen3.8-27B-FP8 \
  --language-model-only \
  --reasoning-parser qwen3 \
  --port 8000 \
  --max-model-len <acceptance value> \
  --gpu-memory-utilization <acceptance value>

Router remains separately served on 127.0.0.1:8001.
```

The official Qwen3.8/vLLM compatibility basis for this candidate is
`vllm>=0.19.0`. The exact single-GPU `max-model-len`, utilization, and KV-cache
budget are deployment-machine acceptance parameters. The model's advertised
long context is not requested by default; the application should retain its
existing bounded history policy.

## Acceptance boundary

This commit establishes compatibility infrastructure only. The following are
intentionally **NOT RUN / environment unavailable**:

- Qwen3.8 model download;
- vLLM server boot on an A100;
- GPU memory use, first-token latency, and tokens/second;
- 20–30-turn stability;
- participant-facing counselling quality and Qwen2.5 side-by-side comparison;
- real no-thinking leakage, audio, STT, or TTS validation.

Qwen3.8 sampling tuning for this psychological-dialogue application is **NOT
YET VALIDATED**. Promotion requires a separate A100 comparison covering startup/OOM behavior,
latency, long-run stability, no thinking/control-tag leakage, one-question
compliance, scale-question semantics, ASR ambiguity confirmation, interruption
behavior, Chinese naturalness, and resistance handling. This candidate must not
be described as faster, safer, or clinically better before that acceptance.

## Deterministic verification

- Candidate request contract: **TESTED** (`tests/test_qwen38_dialogue_candidate.py`).
- `git diff --check`: PASS.
- Production promotion: **NOT APPROVED** pending real A100 validation.
