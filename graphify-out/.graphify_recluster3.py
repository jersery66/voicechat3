import json
from pathlib import Path
from networkx.readwrite import json_graph

data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections

# Try resolution=1.5, no hub exclusion
communities = cluster(G, resolution=1.5)
cohesion = score_all(G, communities)

# Show only multi-node communities
print('=== Multi-node Communities ===')
for cid, nodes in sorted(communities.items()):
    if len(nodes) < 2:
        continue
    coh = cohesion.get(cid, 0)
    label = ', '.join(nodes[:6])
    if len(nodes) > 6:
        label += '...'
    print('C{} (n={}, coh={:.3f}): {}'.format(cid, len(nodes), coh, label))

singletons = sum(1 for v in communities.values() if len(v) == 1)
print('\nTotal: {} communities ({} singletons)'.format(len(communities), singletons))

# Check Community 0
if 0 in communities:
    c0 = communities[0]
    internal = sum(1 for u, v in G.edges() if u in c0 and v in c0)
    external = sum(1 for u, v in G.edges() if (u in c0) != (v in c0))
    print('\nC0: {} nodes, {} internal, {} external edges'.format(len(c0), internal, external))
    print('  Nodes:', c0)

# Save
analysis = {
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {str(k): v for k, v in cohesion.items()},
    'gods': god_nodes(G),
    'surprises': surprising_connections(G, communities),
}
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding='utf-8')
