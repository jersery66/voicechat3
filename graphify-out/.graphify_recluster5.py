import json
from pathlib import Path
from networkx.readwrite import json_graph
import networkx as nx

data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections

# Step 1: Run standard clustering
communities = cluster(G, resolution=1.0)

# Step 2: Post-process — move agent_service out of C0 into C5 (pipeline)
# Find which community has pipeline, llm_service, logger
pipeline_cid = None
agent_cid = None
for cid, nodes in communities.items():
    if 'pipeline' in nodes:
        pipeline_cid = cid
    if 'agent_service' in nodes:
        agent_cid = cid

if pipeline_cid is not None and agent_cid is not None and pipeline_cid != agent_cid:
    # Move agent_service from its community to pipeline's community
    communities[agent_cid].remove('agent_service')
    communities[pipeline_cid].append('agent_service')
    # Also move singleton_pattern_concept (it's agent_service's design pattern)
    if 'singleton_pattern_concept' in communities[agent_cid]:
        communities[agent_cid].remove('singleton_pattern_concept')
        communities[pipeline_cid].append('singleton_pattern_concept')
    print('Moved agent_service + singleton_pattern from C{} to C{}'.format(agent_cid, pipeline_cid))

# Step 3: Remove empty communities
communities = {k: v for k, v in communities.items() if v}

# Recompute cohesion
cohesion = score_all(G, communities)

# Renumber communities by size (largest = 0)
sorted_comms = sorted(communities.items(), key=lambda x: -len(x[1]))
new_communities = {}
new_cohesion = {}
for new_id, (old_id, nodes) in enumerate(sorted_comms):
    new_communities[new_id] = nodes
    new_cohesion[new_id] = cohesion[old_id]

communities = new_communities
cohesion = new_cohesion

print('\n=== Final Communities ===')
for cid, nodes in sorted(communities.items()):
    if len(nodes) < 2:
        continue
    coh = cohesion.get(cid, 0)
    label = ', '.join(nodes[:6])
    if len(nodes) > 6:
        label += '...'
    print('C{} (n={}, coh={:.3f}): {}'.format(cid, len(nodes), coh, label))

# Show C0 detail
c0 = communities.get(0, [])
internal = sum(1 for u, v in G.edges() if u in c0 and v in c0)
external = sum(1 for u, v in G.edges() if (u in c0) != (v in c0))
print('\nC0: {} nodes, {} int, {} ext, coh={:.3f}'.format(len(c0), internal, external, cohesion.get(0, 0)))
print('  Nodes:', c0)

# Save
analysis = {
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {str(k): v for k, v in cohesion.items()},
    'gods': god_nodes(G),
    'surprises': surprising_connections(G, communities),
}
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding='utf-8')
print('\nSaved. C0 cohesion: {:.3f}'.format(cohesion.get(0, 0)))
