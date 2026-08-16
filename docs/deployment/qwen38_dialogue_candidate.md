# Qwen3.8 dialogue deployment candidate

## Status

This is an explicit, reversible candidate profile. It does not replace the
production `a100_80g` profile.

Production promotion: **NOT APPROVED**.

| Profile | Dialogue model | Router/Agent | Status |
| --- | --- | --- | --- |
| `a100_80g` | `Qwen/Qwen2.5-72B-Instruct-AWQ` | `Qwen/Qwen2.5-3B-Instruct-AWQ` | production baseline |
| `a100_80g_qwen38_candidate` | `Qwen/Qwen3.8-27B-FP8` | `Qwen/Qwen2.5-3B-Instruct-AWQ` | explicit opt-in candidate |

Select the candidate only deliberately, for example with
`VOICECHAT_DEPLOYMENT_PROFILE=a100_80g_qwen38_candidate`. No automatic GPU or
model detection promotes it, and model/environment overrides cannot replace
the pinned A100 dialogue or router models.

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

The existing Qwen2.5 profiles do not receive Qwen3.8-specific
`chat_template_kwargs` or `top_k` parameters unless their profile explicitly
sets those typed fields.

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

- Candidate/profile/request contract: **15 passed**.
- Full regression after adding the candidate: **728 passed / 0 failed**.
- `git diff --check`: PASS.
- Production promotion: **NOT APPROVED** pending real A100 validation.
