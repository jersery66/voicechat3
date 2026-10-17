# Runtime configuration reference

`config/runtime.yaml` is the single deployment input for the application.
Models, endpoints, generation parameters, and speech engines are all named
there; no code path inspects the GPU to choose a model.

```bash
# Default: config/runtime.yaml
python main.py

# Any other configuration (an experiment arm, a second machine)
VOICECHAT_RUNTIME_CONFIG=/path/to/runtime_b.yaml python main.py
```

`deployment/runtime_config.load_runtime_config()` is the only loader. It is
called once by `config.py`, whose module-level constants feed the rest of the
application, so every consumer sees the same immutable snapshot.

## Sections

### `dialogue`

The main conversational model.

| Key | Required | Meaning |
| --- | --- | --- |
| `provider` | yes | `openai_compatible` (llama.cpp, vLLM, any compatible server) or `ollama` |
| `base_url` | yes | HTTP(S) `/v1` endpoint the application calls |
| `model` | yes | Exact model id the server publishes in `GET /v1/models` |
| `api_key` | no | Default `EMPTY`; local servers ignore it |
| `timeout` | no | Request timeout in seconds |
| `max_tokens` | no | Default completion budget |
| `temperature`, `top_p` | no | Sampling parameters |
| `top_k`, `presence_penalty` | no | Optional; forwarded via `extra_body` when set |
| `enable_thinking` | no | `false` disables reasoning output for Qwen3-series models |
| `request_mode` | no | `chat` (chat completions) or `completion` (base model, no template) |
| `system_role_mode` | no | `native` (system role) or `prepend_user` (fold into the first user turn) |
| `system_prompt_override` | no | Replaces `config.SYSTEM_PROMPT` for experiments |

### `agent`

Configured independently of `dialogue`: a different provider, endpoint, and
model are all allowed.

| Key | Required | Meaning |
| --- | --- | --- |
| `enabled` | no | Default `true`; `false` disables the Agent entirely |
| `provider` | yes | Same values as `dialogue.provider` |
| `base_url`, `model` | when enabled | Endpoint and exact published model id |
| `api_key`, `timeout`, `temperature` | no | As above |

When `enabled: false`, `get_agent_service()` returns `None`, the UI shows no
Agent panel, and health checks do not probe the endpoint.

### `asr` / `tts`

| Key | Meaning |
| --- | --- |
| `asr.provider` | Only `funasr` is supported today |
| `tts.provider` | `voxcpm` or `cosyvoice` |
| `model_path` | Absolute path, a path relative to `VOICECHAT_MODELS_DIR`, `auto`, or empty |
| `tts.voice_prompt_path` | Zero-shot voice prompt audio; same path rules |

`model_path: auto` delegates to the detection logic in `config.py`. The
provider is selected at import time; an unknown provider is a hard error, and
the unselected engine is never imported.

### `runtime`

| Key | Default | Meaning |
| --- | --- | --- |
| `strict_preflight` | `false` | Refuse to start when startup checks fail |
| `immutable_contract` | `true` | Ignore legacy environment overrides |
| `manage_model_servers` | `false` | The application does not start or stop model servers |
| `enable_streaming_tts` | `true` | Speak sentences while the response is still streaming |

## Validation

Loading fails loudly — a typo must never silently select a different model or
engine:

- unknown top-level sections and unknown keys within a section;
- unknown `provider` values;
- empty `base_url` or `model` where they are required;
- `base_url` values that are not HTTP(S) URLs;
- out-of-range numbers (negative timeouts, non-positive `max_tokens`, and so
  on);
- `request_mode` / `system_role_mode` outside their allowed values.

Errors raise `RuntimeConfigError` with the file path and the offending key.

## Environment overrides

`immutable_contract: true` — the default, and what every committed
configuration uses — means the file wins and the legacy variables
(`VOICECHAT_DIALOGUE_BASE_URL`, `VOICECHAT_DIALOGUE_MODEL`,
`VOICECHAT_VLLM_MODEL`, `OLLAMA_MODEL`, `VOICECHAT_AGENT_BASE_URL`,
`AGENT_MODEL`) are ignored. Set `immutable_contract: false` only for ad-hoc
development shells.

## How the configuration is verified

Nothing below changes state; all three read the configuration and compare it
with what is actually running.

| Command | What it proves |
| --- | --- |
| `python scripts/check_config.py` | Paths, model files, and the selected ASR/TTS engine are present |
| `python scripts/deployment/doctor.py --config config/runtime.yaml` | Files, endpoints, and model identity agree with the configuration |
| `python scripts/acceptance/blackwell_live_probe.py --config config/runtime.yaml` | Live inference, exact model identity, Agent JSON, and thinking-leak checks |

The dialogue A/B harness uses the same mechanism: each arm is one runtime
configuration file, so two models are compared by comparing two YAML files.

## Switching engines

The deployment shape is a configuration change plus the matching engine
service (see `docs/deployment/engine_services.md`):

| Target | Configuration change | Service |
| --- | --- | --- |
| llama.cpp | `provider: openai_compatible`, `model` = `--alias` value | `scripts/engines/llama_cpp/start_service.sh` |
| vLLM | `provider: openai_compatible`, `model` = `--served-model-name` | `scripts/engines/vllm/start_service.sh` |
| Ollama | `provider: ollama`, `model` = local model name | `ollama serve` |

Only `base_url` and `model` normally change; generation parameters stay put
unless the new model needs different sampling.

## What the configuration does not do

- It does not choose a model based on detected hardware.
- It does not start, stop, or restart model servers.
- It does not relax memory limits: an insufficient-VRAM failure surfaces as
  the engine's own out-of-memory error, not as a configured threshold.
