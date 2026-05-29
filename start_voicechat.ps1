# Voice Chat Startup Script
# Sets model and host environment variables, then launches the application.

$env:OLLAMA_MODEL = "qwen3.6:35b"
$env:OLLAMA_HOST = "http://localhost:11434"

Write-Host "Starting Voice Chat with model: $env:OLLAMA_MODEL" -ForegroundColor Green
python main.py
