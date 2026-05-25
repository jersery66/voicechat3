import json, sys
from pathlib import Path
from networkx.readwrite import json_graph
import networkx as nx

# Load existing graph
data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections

# Strategy: exclude hub nodes (agent_service, rag_service, etc.) from partitioning
# so they don't pull unrelated subsystems into their community
# resolution=1.3 for slightly finer-grained communities
communities = cluster(G, resolution=1.3, exclude_hubs_percentile=85)
cohesion = score_all(G, communities)

print('=== New Communities ===')
for cid, nodes in sorted(communities.items()):
    coh = cohesion.get(cid, 0)
    print('C{} (n={}, coh={:.3f}): {}'.format(cid, len(nodes), coh, ', '.join(nodes[:8]) + ('...' if len(nodes) > 8 else '')))

print()
print('=== Cohesion Summary ===')
low_coh = [(cid, coh) for cid, coh in cohesion.items() if coh < 0.3 and len(communities[cid]) > 1]
low_coh.sort(key=lambda x: x[1])
for cid, coh in low_coh:
    print('  C{}: coh={:.3f} nodes={}'.format(cid, coh, len(communities[cid])))

# Check Community 0 specifically
if 0 in communities:
    c0 = communities[0]
    print()
    print('=== Community 0 detail ===')
    print('Nodes ({}): {}'.format(len(c0), c0))
    internal = sum(1 for u, v in G.edges() if u in c0 and v in c0)
    external = sum(1 for u, v in G.edges() if (u in c0) != (v in c0))
    print('Internal edges: {}, External edges: {}'.format(internal, external))

# Save for later steps
analysis = {
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {str(k): v for k, v in cohesion.items()},
    'gods': god_nodes(G),
    'surprises': surprising_connections(G, communities),
}
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding='utf-8')
print('\nSaved analysis to .graphify_analysis.json')
