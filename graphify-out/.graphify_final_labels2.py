import json
from pathlib import Path
from networkx.readwrite import json_graph
import networkx as nx
from graphify.report import generate
from graphify.export import to_json, to_html
from graphify.analyze import god_nodes, surprising_connections

# Load graph directly from graph.json
data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

detection  = json.loads(Path('graphify-out/.graphify_detect_utf8.json').read_text(encoding='utf-8-sig'))
analysis   = json.loads(Path('graphify-out/.graphify_analysis.json').read_text(encoding='utf-8'))

communities = {int(k): v for k, v in analysis['communities'].items()}
cohesion = {int(k): v for k, v in analysis['cohesion'].items()}
tokens = {'input': 0, 'output': 0}

# Final community labels
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

gods = god_nodes(G)
surprises = surprising_connections(G, communities)

report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, '.', suggested_questions=analysis.get('questions', []))
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
Path('graphify-out/.graphify_labels.json').write_text(json.dumps({str(k): v for k, v in labels.items()}, ensure_ascii=False), encoding='utf-8')

to_html(G, communities, 'graphify-out/graph.html', community_labels=labels)
to_json(G, communities, 'graphify-out/graph.json')

print('=== Final Report ===')
print('Graph: {} nodes, {} edges, {} communities'.format(G.number_of_nodes(), G.number_of_edges(), len(communities)))
print()
for cid, nodes in sorted(communities.items()):
    coh = cohesion.get(cid, 0)
    print('C{} (n={}, coh={:.3f}) {}: {}'.format(cid, len(nodes), coh, labels.get(cid, '?'), ', '.join(nodes[:5]) + ('...' if len(nodes) > 5 else '')))
