# Voice Chat Startup Script
# Sets model and host environment variables, then launches the application.

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

$env:OLLAMA_MODEL = "qwen2.5:3b"
$env:AGENT_MODEL = "qwen2.5:3b"
$env:OLLAMA_HOST = "http://localhost:11434"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Virtual environment not found: $VenvPython" -ForegroundColor Red
    exit 1
}

Write-Host "Starting Voice Chat with model: $env:OLLAMA_MODEL" -ForegroundColor Green
Set-Location $ProjectDir
& $VenvPython main.py
