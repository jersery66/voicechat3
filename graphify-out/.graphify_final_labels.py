import json
from pathlib import Path
from graphify.build import build_from_json
from graphify.report import generate
from graphify.export import to_json, to_html

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
detection  = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8-sig'))
analysis   = json.loads(Path('graphify-out/.graphify_analysis.json').read_text(encoding='utf-8'))

G = build_from_json(extraction)
communities = {int(k): v for k, v in analysis['communities'].items()}
cohesion = {int(k): v for k, v in analysis['cohesion'].items()}
tokens = {'input': extraction.get('input_tokens', 0), 'output': extraction.get('output_tokens', 0)}

# Final community labels (based on new structure)
labels = {
    0: 'User Interface Layer',
    1: 'Configuration & Agent Prompts',
    2: 'Knowledge Base & Therapy Scenes',
    3: 'Game Engine & Therapeutic Mechanics',
    4: 'Conversation Pipeline (with Agent)',
    5: 'RAG Knowledge Retrieval',
    6: 'Clinical Psychology Framework',
    7: 'UI Design & Documentation',
    8: 'Dialog System',
    9: 'Dependencies',
    10: 'Error Monitoring',
    11: 'Psychotic Symptom Knowledge',
}

from graphify.analyze import god_nodes, surprising_connections
gods = god_nodes(G)
surprises = surprising_connections(G, communities)

# Regenerate report with real labels
questions_data = analysis.get('questions', [])
report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, '.', suggested_questions=questions_data)
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
Path('graphify-out/.graphify_labels.json').write_text(json.dumps({str(k): v for k, v in labels.items()}, ensure_ascii=False), encoding='utf-8')

# Regenerate HTML
to_html(G, communities, 'graphify-out/graph.html', community_labels=labels)
to_json(G, communities, 'graphify-out/graph.json')

print('=== Final Report ===')
print('Graph: {} nodes, {} edges, {} communities'.format(G.number_of_nodes(), G.number_of_edges(), len(communities)))
print()
for cid, nodes in sorted(communities.items()):
    coh = cohesion.get(cid, 0)
    print('C{} (n={}, coh={:.3f}) {}: {}'.format(cid, len(nodes), coh, labels.get(cid, '?'), ', '.join(nodes[:5]) + ('...' if len(nodes) > 5 else '')))
print()
print('C0 (old) split result:')
print('  Knowledge+Scenes -> C2: {} nodes, coh={:.3f}'.format(len(communities.get(2,[])), cohesion.get(2,0)))
print('  Agent -> C4 (Pipeline): {} nodes, coh={:.3f}'.format(len(communities.get(4,[])), cohesion.get(4,0)))
