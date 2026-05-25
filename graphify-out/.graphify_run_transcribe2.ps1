$env:GRAPHIFY_WHISPER_PROMPT = "AI psychological counseling voice system for drug rehabilitation centers using Motivational Interviewing techniques. Use proper punctuation and paragraph breaks."
& "C:\Users\Jersery\AppData\Roaming\uv\tools\graphifyy\Scripts\python.exe" -c @"
import json, os
from pathlib import Path
from graphify.transcribe import transcribe_all

detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8-sig'))
video_files = detect.get('files', {}).get('video', [])
prompt = os.environ.get('GRAPHIFY_WHISPER_PROMPT', 'Use proper punctuation and paragraph breaks.')

transcript_paths = transcribe_all(video_files, initial_prompt=prompt)
print(json.dumps(transcript_paths, ensure_ascii=False))
"@
