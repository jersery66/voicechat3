# RTX PRO 6000 Blackwell operator runbook

This is a worked installation and acceptance example for a Windows 11
workstation with an RTX PRO 6000 Blackwell GPU. The procedure itself is not
hardware-approving: models, endpoints, and execution options come from
`config/runtime.yaml`, so the same steps apply to any card that can hold the
configured models. Any version or capacity statement not backed by a
target-machine artifact is `TO BE VERIFIED ON TARGET HARDWARE`.

See `docs/deployment/engine_services.md` for the engine service scripts and
`docs/deployment/runtime_config.md` for the configuration reference.

## 1. Protect the approved checkout

```powershell
git fetch origin
git checkout --detach pre-hardware-validation-ready-v2-20260817
git status --short
git rev-parse HEAD
git rev-parse "pre-hardware-validation-ready-v2-20260817^{commit}"
```

The working tree must be clean and `HEAD` must equal the immutable
validation-ready v2 tag. The corrected candidate tag
(`e4de593321a6334099971ac5a0d26c9141c419b4`) must remain an ancestor. The original
`pre-hardware-freeze-20260817` tag is historical and must not be moved or used
for the workstation run. Do not reset or merge an unknown checkout
automatically.

## 2. Windows and NVIDIA preparation

Install the supported Windows NVIDIA driver through the organisation's normal
IT process. Record Windows edition/build, exact GPU name, driver, total/free
VRAM, and the raw `nvidia-smi` output. Target identity and 96 GB capacity are
`TO BE VERIFIED ON TARGET HARDWARE`.

```powershell
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsBuildNumber
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free --format=csv
```

Do not install a Linux display driver inside WSL.

## 3. WSL2 and Linux environment

```powershell
wsl --status
wsl --version
wsl -l -v
wsl -- uname -a
wsl -- nvidia-smi
```

Use a WSL2 distribution selected by the operator. Do not assume a distribution
name; run the engine service scripts inside whichever distribution hosts the
server. CUDA passthrough and vLLM/llama.cpp compatibility remain
target-machine checks.

## 4. Python, dependencies, and model paths

Create the Windows application environment without committing machine paths:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-core.txt
# Install requirements-gpu.txt / requirements-media.txt only as required by
# the target deployment and record the actual versions.
```

Create the WSL inference environment and make the configured executable
callable. Do not auto-install or upgrade the inference engine from the service
scripts. Exact Python, PyTorch, CUDA, vLLM, llama.cpp, FunASR, and VoxCPM2
compatibility is `TO BE VERIFIED ON TARGET HARDWARE`.

`.env.example` is a variable template only; this repository does not
automatically load a local `.env` file. Explicitly export/set the variables
before launch, for example:

```powershell
$env:VOICECHAT_MODELS_DIR = "<operator-model-root>"
$env:FUNASR_MODEL_PATH = "<operator-funasr-path>"
$env:VOXCPM_MODEL_PATH = "<operator-voxcpm-path>"
$env:VOICE_PROMPT_PATH = "<operator-voice-prompt>"
$env:VOICECHAT_DATA_DIR = "<operator-data-root>"
```

Keep models outside Git and record only their existence/readability.

## 5. Read-only checks and derived artifacts

```powershell
.venv\Scripts\python.exe scripts\deployment\doctor.py `
  --config config\runtime.yaml

.venv\Scripts\python.exe scripts\deployment\manifest.py `
  --config config\runtime.yaml

.venv\Scripts\python.exe scripts\deployment\measurement.py `
  --config config\runtime.yaml
```

These tools observe or generate derived artifacts. They do not start/stop
services and manifests are not runtime configuration.

## 6. Start and verify the inference services

Use explicit, operator-measured memory arguments; no value is approved by this
repository before real validation:

```bash
# llama.cpp — one service per model; the alias must equal dialogue.model
scripts/engines/llama_cpp/start_service.sh \
  --model /root/models/Qwen3.8-27B-Q4_K_S.gguf \
  --port 8000 --alias Qwen3.8-27B-Q4_K_S \
  --context-size 8192 --gpu-layers 99 --parallel 1 \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --service-name dialogue

scripts/engines/llama_cpp/start_service.sh \
  --model /root/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  --port 8001 --alias Qwen3-4B-Instruct-2507 \
  --context-size 4096 --gpu-layers 99 --parallel 1 \
  --service-name agent
```

For vLLM, use `scripts/engines/vllm/start_service.sh` with
`--gpu-memory-utilization` and `--max-model-len` instead of the llama.cpp
flags. The scripts refuse to reuse or replace a process they cannot prove they
own, and they do not select models by hardware.

Then verify the deployment against the configuration:

```powershell
.venv\Scripts\python.exe scripts\deployment\doctor.py --config config\runtime.yaml
.venv\Scripts\python.exe scripts\acceptance\blackwell_live_probe.py `
  --config config\runtime.yaml
```

The probe reports a disabled Agent as `SKIPPED`; memory is observed before and
after inference, never enforced against a fixed threshold.

## 7. Acceptance sequence

After both exact models are ready:

1. Run the live probe and require its own `PASS` artifact.
2. Run the baseline A/B arm with that exact probe summary and the baseline
   runtime configuration.
3. Stop only the owned dialogue service and verify the port and VRAM are
   released.
4. Start the candidate service and point at the candidate runtime
   configuration.
5. Repeat the probe, then the candidate A/B arm.
6. Run `compare`; do not bypass `NOT_COMPARABLE`.
7. Keep blind packets blind and obtain independent human review.
8. Separately validate real STT, TTS, and the full microphone-to-playback path.

The candidate remains `NOT APPROVED` until real evidence and human review
support promotion.

## 8. Stop

```bash
scripts/engines/llama_cpp/stop_service.sh --service-name dialogue
scripts/engines/llama_cpp/stop_service.sh --service-name agent
```

The stop path validates owned PID metadata and command identity before TERM or
KILL. Services are not automatically unloaded when the GUI exits unless the
operator explicitly stops them.

## 9. Evidence and privacy

Runtime artifacts belong under ignored `test_output/` directories. Do not put
participant recordings, transcripts, clinical scores, credentials, or personal
paths in Git. Real hardware results must remain separate from simulated/offline
contract evidence.
