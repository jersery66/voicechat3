<#
Start the production dialogue server on the Linux/WSL A100 host.

This script is deliberately separate from the PySide client: vLLM owns the
GPU and exposes an OpenAI-compatible /v1 endpoint, while the desktop app only
uses HTTP. Run it in the Python environment where vLLM is installed.
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
