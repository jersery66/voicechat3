import json, sys
from pathlib import Path
from networkx.readwrite import json_graph
import networkx as nx

# Load existing graph
data = json.loads(Path('graphify-out/graph.json').read_text(encoding='utf-8'))
G = json_graph.node_link_graph(data, edges='links')

print('Graph: {} nodes, {} edges'.format(G.number_of_nodes(), G.number_of_edges()))

# Check what clustering functions are available
from graphify import cluster as cluster_mod
import inspect
print('cluster module functions:', [f for f in dir(cluster_mod) if not f.startswith('_')])

# Check cluster function signature
sig = inspect.signature(cluster_mod.cluster)
print('cluster() signature:', sig)

# Check if there's a resolution parameter or weight handling
src = inspect.getsource(cluster_mod.cluster)
print('--- cluster source (first 80 lines) ---')
for i, line in enumerate(src.split('\n')[:80]):
    print('{:3d}: {}'.format(i+1, line))
