$env:GRAPHIFY_WHISPER_PROMPT = "AI psychological counseling voice system for drug rehabilitation centers using Motivational Interviewing techniques. Use proper punctuation and paragraph breaks."
& "C:\Users\Jersery\AppData\Roaming\uv\tools\graphifyy\Scripts\python.exe" "graphify-out\.graphify_step_transcribe.py" | Out-File -FilePath "graphify-out\.graphify_transcripts.json" -Encoding utf8
