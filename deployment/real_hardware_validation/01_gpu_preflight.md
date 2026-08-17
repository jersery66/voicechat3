# 01 — Windows GPU preflight

Do not start vLLM, FunASR, VoxCPM2, or the GUI before this checkpoint.

## Checkout gate

```powershell
git fetch origin
git checkout --detach pre-hardware-corrected-20260817
git status --short
git rev-parse HEAD
git rev-parse "pre-hardware-corrected-20260817^{commit}"
```

`HEAD` must equal `e4de593321a6334099971ac5a0d26c9141c419b4` and the working
tree must be clean. Do not reset or merge unknown changes.

## Host evidence

```powershell
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsBuildNumber
nvidia-smi
nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,uuid --format=csv
```

Save the raw output with the run artifacts. The target must be one NVIDIA RTX
PRO 6000 Blackwell GPU with approximately 96 GiB (use the live probe's
single-GPU and tolerance rules). Any other identity, multiple NVIDIA GPUs, or
failed `nvidia-smi` is a stop condition under the current contract.

The Windows driver owns CUDA-on-WSL. Do not install a Linux display driver in
WSL and do not modify `.wslconfig` as part of this check.
