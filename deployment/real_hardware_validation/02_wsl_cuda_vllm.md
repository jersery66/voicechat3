# 02 — WSL2, CUDA, PyTorch, and vLLM

Run this only after Windows GPU identity is recorded.

```powershell
wsl --status
wsl --version
wsl -l -v
wsl -- uname -a
wsl -- nvidia-smi
```

If the standard WSL path is unavailable, record the result of:

```powershell
wsl -- /usr/lib/wsl/lib/nvidia-smi
```

The selected distribution must be WSL2 and must expose the same single target
GPU family. Do not assume the distribution is named Ubuntu; pass `--distro`
where necessary.

In the vLLM WSL environment, record:

```bash
python --version
pip show torch
pip show vllm
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda_available:", torch.cuda.is_available())
print("cuda_version:", torch.version.cuda)
if torch.cuda.is_available():
    print("device_count:", torch.cuda.device_count())
    print("device_name:", torch.cuda.get_device_name(0))
    print("total_memory_GB:", torch.cuda.get_device_properties(0).total_memory / 1024**3)
PY
```

`nvidia-smi`, PyTorch CUDA, and the configured vLLM executable must all be
available before model startup. This stage does not start a model server.
