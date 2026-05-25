$py = "C:\Users\Jersery\AppData\Roaming\uv\tools\graphifyy\Scripts\python.exe"
Set-Location "E:\数据库\代码\Data\PDCH\voicechat"

# Step 3C - Merge AST + semantic
& $py -c @"
import sys, json
from pathlib import Path

ast_path = Path('graphify-out/.graphify_ast.json')
sem_path = Path('graphify-out/.graphify_semantic_new.json')
out_path = Path('graphify-out/.graphify_extract.json')

ast = json.loads(ast_path.read_text(encoding='utf-8-sig')) if ast_path.exists() else {'nodes':[],'edges':[],'hyperedges':[],'input_tokens':0,'output_tokens':0}
sem = json.loads(sem_path.read_text(encoding='utf-8')) if sem_path.exists() else {'nodes':[],'edges':[],'hyperedges':[],'input_tokens':0,'output_tokens':0}

# Merge: AST nodes first, semantic nodes deduplicated by id
seen = {n['id'] for n in ast['nodes']}
merged_nodes = list(ast['nodes'])
for n in sem['nodes']:
    if n['id'] not in seen:
        merged_nodes.append(n)
        seen.add(n['id'])

merged_edges = ast['edges'] + sem['edges']
merged_hyperedges = sem.get('hyperedges', [])
merged = {
    'nodes': merged_nodes,
    'edges': merged_edges,
    'hyperedges': merged_hyperedges,
    'input_tokens': sem.get('input_tokens', 0),
    'output_tokens': sem.get('output_tokens', 0),
}
out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding='utf-8')
total = len(merged_nodes)
edges = len(merged_edges)
print('Merged: {} nodes, {} edges ({} AST + {} semantic)'.format(total, edges, len(ast['nodes']), len(sem['nodes'])))
"@

# Step 4 - Build graph, cluster, analyze
& $py -c @"
import sys, json
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json
from pathlib import Path

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
detection  = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8-sig'))

G = build_from_json(extraction)
communities = cluster(G)
cohesion = score_all(G, communities)
tokens = {'input': extraction.get('input_tokens', 0), 'output': extraction.get('output_tokens', 0)}
gods = god_nodes(G)
surprises = surprising_connections(G, communities)
labels = {cid: 'Community ' + str(cid) for cid in communities}
questions = suggest_questions(G, communities, labels)

report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, '.', suggested_questions=questions)
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
to_json(G, communities, 'graphify-out/graph.json')

analysis = {
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {str(k): v for k, v in cohesion.items()},
    'gods': gods,
    'surprises': surprises,
    'questions': questions,
}
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding='utf-8')
if G.number_of_nodes() == 0:
    print('ERROR: Graph is empty')
    raise SystemExit(1)
print('Graph: {} nodes, {} edges, {} communities'.format(G.number_of_nodes(), G.number_of_edges(), len(communities)))
"@
