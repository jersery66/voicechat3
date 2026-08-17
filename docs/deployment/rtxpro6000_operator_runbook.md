# RTX PRO 6000 Blackwell operator runbook

This is the operator procedure for a Windows 11 workstation targeting the
explicit `rtxpro6000_96g` or `rtxpro6000_96g_qwen38_candidate` profile. It is
an installation and acceptance runbook, not a hardware approval. Any version
or capacity statement not backed by a target-machine artifact is
`TO BE VERIFIED ON TARGET HARDWARE`.

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
name. If the default distribution is unsuitable, pass `-Distro` to the
launcher. CUDA passthrough and vLLM compatibility remain target-machine
checks.

## 4. Python, dependencies, and model paths

Create the Windows application environment without committing machine paths:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-core.txt
# Install requirements-gpu.txt / requirements-media.txt only as required by
# the target deployment and record the actual versions.
```

Create the WSL vLLM environment and make the configured executable callable.
Do not auto-install or upgrade vLLM from the launcher. Exact Python, PyTorch,
CUDA, vLLM, FunASR, and VoxCPM2 compatibility is `TO BE VERIFIED ON TARGET
HARDWARE`.

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
  --profile rtxpro6000_96g

.venv\Scripts\python.exe scripts\deployment\manifest.py `
  --profile rtxpro6000_96g

.venv\Scripts\python.exe scripts\deployment\measurement.py `
  --profile rtxpro6000_96g
```

These tools observe or generate derived artifacts. They do not start/stop
services and manifests are not runtime configuration.

## 6. Start and verify the vLLM stack

Use explicit, operator-measured memory arguments; no value is approved by this
repository before real validation:

```powershell
.\scripts\windows\start_blackwell_stack.ps1 `
  -Profile rtxpro6000_96g `
  -DialogueGpuMemoryUtilization <measured-value> `
  -AgentGpuMemoryUtilization <measured-value>
```

The launcher starts Agent `:8001` before dialogue `:8000`, requires exact
profile-owned model identities, runs strict preflight, and starts the Windows
GUI last. To observe without changing state:

```powershell
.\scripts\windows\start_blackwell_stack.ps1 `
  -Profile rtxpro6000_96g `
  -VerifyOnly
```

`-Status` is an alias. An occupied unknown port, wrong model, stale ownership,
or strict-preflight failure is fail-closed. Never kill an unknown process.

## 7. Acceptance sequence

After both exact models are ready:

1. Run the Phase 5 live probe and require its own `PASS` artifact.
2. Run the baseline A/B arm with that exact probe summary.
3. Stop only the owned dialogue service and verify `:8000`/VRAM are released.
4. Start the explicit Qwen3.8 candidate profile.
5. Repeat Phase 5, then the candidate A/B arm.
6. Run `compare`; do not bypass `NOT_COMPARABLE`.
7. Keep blind packets blind and obtain independent human review.
8. Separately validate real STT, TTS, and the full microphone-to-playback path.

The candidate remains `NOT APPROVED` until real evidence and human review
support promotion.

## 8. Stop

```powershell
.\scripts\windows\stop_blackwell_stack.ps1
```

The stop path validates owned PID metadata and command identity before TERM or
KILL. Services are not automatically unloaded when the GUI exits unless the
operator explicitly stops them.

## 9. Evidence and privacy

Runtime artifacts belong under ignored `test_output/` directories. Do not put
participant recordings, transcripts, clinical scores, credentials, or personal
paths in Git. Real hardware results must remain separate from simulated/offline
contract evidence.
