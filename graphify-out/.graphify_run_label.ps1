$py = "C:\Users\Jersery\AppData\Roaming\uv\tools\graphifyy\Scripts\python.exe"
Set-Location "E:\数据库\代码\Data\PDCH\voicechat"

& $py -c @"
import sys, json
from graphify.build import build_from_json
from graphify.cluster import score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from pathlib import Path

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
detection  = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8-sig'))
analysis   = json.loads(Path('graphify-out/.graphify_analysis.json').read_text(encoding='utf-8'))

G = build_from_json(extraction)
communities = {int(k): v for k, v in analysis['communities'].items()}
cohesion = {int(k): v for k, v in analysis['cohesion'].items()}
tokens = {'input': extraction.get('input_tokens', 0), 'output': extraction.get('output_tokens', 0)}

# Community labels
labels = {
    0: 'Knowledge & Therapy Scenes',
    1: 'User Interface Layer',
    2: 'Configuration & Game Core',
    3: 'Game Engine & Mechanics',
    4: 'RAG Knowledge Retrieval',
    5: 'Conversation Pipeline',
    6: 'Clinical Psychology Framework',
    7: 'UI Design & Documentation',
    8: 'Dialog System',
    9: 'Dependencies',
    10: 'Error Monitoring',
    11: 'Psychotic Symptom Knowledge',
    12: 'Data Package Init',
    13: 'Game Package Init',
    14: 'Game Entities Init',
    15: 'Game Systems Init',
    16: 'Clinical Cases KB',
    17: 'Multi-Turn Dialogues KB',
    18: 'Claude Code Config',
    19: 'Family Support KB',
    20: 'Assessment Tools KB',
    21: 'Animated Button Widget',
    22: 'Intent-Scene Mapping',
    23: 'Agent Intent Prompt',
    24: 'Agent RAG Routing Prompt',
    25: 'Agent Summary Prompt',
    26: 'Crisis Hotlines',
    27: 'Session Summary Prompt',
    28: 'Visitor Feedback Prompt',
    29: 'Suggestions Prompt',
    30: 'Pipe Delimiter Pattern',
    31: 'Session End Tags',
}

questions = suggest_questions(G, communities, labels)
report = generate(G, communities, cohesion, labels, analysis['gods'], analysis['surprises'], detection, tokens, '.', suggested_questions=questions)
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
Path('graphify-out/.graphify_labels.json').write_text(json.dumps({str(k): v for k, v in labels.items()}, ensure_ascii=False), encoding='utf-8')
print('Report updated with community labels')
"@

# Step 6 - Generate HTML
& $py -c @"
import sys, json
from graphify.build import build_from_json
from graphify.export import to_html
from pathlib import Path

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
analysis   = json.loads(Path('graphify-out/.graphify_analysis.json').read_text(encoding='utf-8'))
labels_raw = json.loads(Path('graphify-out/.graphify_labels.json').read_text(encoding='utf-8')) if Path('graphify-out/.graphify_labels.json').exists() else {}

G = build_from_json(extraction)
communities = {int(k): v for k, v in analysis['communities'].items()}
labels = {int(k): v for k, v in labels_raw.items()}

if G.number_of_nodes() > 5000:
    print('Graph has {} nodes - too large for HTML viz'.format(G.number_of_nodes()))
else:
    to_html(G, communities, 'graphify-out/graph.html', community_labels=labels or None)
    print('graph.html written - open in any browser, no server needed')
"@

# Step 8 - Token reduction benchmark (total_words > 5000)
& $py -c @"
import json
from graphify.benchmark import run_benchmark, print_benchmark
from pathlib import Path

detection = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8-sig'))
result = run_benchmark('graphify-out/graph.json', corpus_words=detection['total_words'])
print_benchmark(result)
"@

# Step 9 - Save manifest, cost tracker, cleanup
& $py -c @"
import json
from pathlib import Path
from datetime import datetime, timezone
from graphify.detect import save_manifest

detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8-sig'))
save_manifest(detect['files'])

extract = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
input_tok = extract.get('input_tokens', 0)
output_tok = extract.get('output_tokens', 0)

cost_path = Path('graphify-out/cost.json')
if cost_path.exists():
    cost = json.loads(cost_path.read_text(encoding='utf-8'))
else:
    cost = {'runs': [], 'total_input_tokens': 0, 'total_output_tokens': 0}

cost['runs'].append({
    'date': datetime.now(timezone.utc).isoformat(),
    'input_tokens': input_tok,
    'output_tokens': output_tok,
    'files': detect.get('total_files', 0),
})
cost['total_input_tokens'] += input_tok
cost['total_output_tokens'] += output_tok
cost_path.write_text(json.dumps(cost, indent=2, ensure_ascii=False), encoding='utf-8')

print('This run: {:,} input tokens, {:,} output tokens'.format(input_tok, output_tok))
print('All time: {:,} input, {:,} output ({} runs)'.format(cost['total_input_tokens'], cost['total_output_tokens'], len(cost['runs'])))
"@

# Cleanup temp files
Remove-Item -ErrorAction SilentlyContinue graphify-out\.graphify_detect.json, graphify-out\.graphify_extract.json, graphify-out\.graphify_ast.json, graphify-out\.graphify_semantic.json, graphify-out\.graphify_analysis.json, graphify-out\.graphify_labels.json
Remove-Item -ErrorAction SilentlyContinue graphify-out/.needs_update
