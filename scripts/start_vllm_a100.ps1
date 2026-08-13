<#
Start a production vLLM language-model server on the Linux/WSL A100 host.

This script is deliberately separate from the PySide client: each vLLM process
owns a fixed language model and exposes an OpenAI-compatible /v1 endpoint.
Run it once per service on the Python environment where vLLM is installed.
The ``a100_80g`` profile uses: 8000 = 72B dialogue, 8001 = 3B Agent,
8002 = Qwen3Guard.  It never calls Ollama.  Example:
  .\scripts\start_vllm_a100.ps1 -Model Qwen/Qwen2.5-3B-Instruct-AWQ -Port 8001 -GpuMemoryUtilization 0.08 -MaxModelLen 4096
  .\scripts\start_vllm_a100.ps1 -Model Qwen/Qwen3Guard-Gen-4B -Port 8002 -GpuMemoryUtilization 0.10 -MaxModelLen 4096
#>

[CmdletBinding()]
param(
    [string]$Model = $(if ($env:VOICECHAT_VLLM_MODEL) { $env:VOICECHAT_VLLM_MODEL } else { "Qwen/Qwen2.5-72B-Instruct-AWQ" }),
    [int]$Port = $(if ($env:VOICECHAT_VLLM_PORT) { [int]$env:VOICECHAT_VLLM_PORT } else { 8000 }),
    [double]$GpuMemoryUtilization = $(if ($env:VOICECHAT_VLLM_GPU_MEMORY_UTILIZATION) { [double]$env:VOICECHAT_VLLM_GPU_MEMORY_UTILIZATION } else { 0.90 }),
    [int]$MaxModelLen = $(if ($env:VOICECHAT_VLLM_MAX_MODEL_LEN) { [int]$env:VOICECHAT_VLLM_MAX_MODEL_LEN } else { 8192 })
)

$ErrorActionPreference = "Stop"

vllm serve $Model `
    --host 0.0.0.0 `
    --port $Port `
    --dtype auto `
    --gpu-memory-utilization $GpuMemoryUtilization `
    --max-model-len $MaxModelLen `
    --max-num-seqs 4 `
    --enable-prefix-caching
