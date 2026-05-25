import json
from pathlib import Path
from networkx.readwrite import json_graph

data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections

# resolution=2.0 to force fine-grained communities
communities = cluster(G, resolution=2.0)
cohesion = score_all(G, communities)

# Show only multi-node communities
print('=== Multi-node Communities (res=2.0) ===')
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

# Check if agent_service is still in a large community
for cid, nodes in communities.items():
    if 'agent_service' in nodes and len(nodes) > 2:
        internal = sum(1 for u, v in G.edges() if u in nodes and v in nodes)
        external = sum(1 for u, v in G.edges() if (u in nodes) != (v in nodes))
        print('\nAgent community C{}: {} nodes, {} int, {} ext, coh={:.3f}'.format(
            cid, len(nodes), internal, external, cohesion.get(cid, 0)))
        print('  Nodes:', nodes)

# Save
analysis = {
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {str(k): v for k, v in cohesion.items()},
    'gods': god_nodes(G),
    'surprises': surprising_connections(G, communities),
}
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding='utf-8')
