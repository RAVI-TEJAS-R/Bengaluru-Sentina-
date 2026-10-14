"""Build a central-Bengaluru driving graph from OpenStreetMap using OSMnx.
Run separately: pip install osmnx && python build_graph.py
"""
import os
import pickle
import osmnx as ox

SPEED_KMH = {"motorway": 60, "trunk": 50, "primary": 40, "secondary": 30, "tertiary": 25}
os.makedirs("data", exist_ok=True)
G = ox.graph_from_point((12.95, 77.62), dist=6500, network_type="drive", simplify=True)
P = {node: (data["y"], data["x"]) for node, data in G.nodes(data=True)}
E = []
for u, v, data in G.edges(data=True):
    highway = data.get("highway")
    highway = highway[0] if isinstance(highway, list) else highway
    layer = str(data.get("layer", "0"))
    underpass = bool(data.get("tunnel")) or layer.startswith("-")
    # Graph may be multi-directed; preserve each directed edge as its own edge record.
    E.append((u, v, float(data.get("length", 1)), SPEED_KMH.get(highway, 20), int(underpass)))
with open("data/graph.pkl", "wb") as handle:
    pickle.dump({"P": P, "E": E}, handle, protocol=pickle.HIGHEST_PROTOCOL)
print(f"Wrote data/graph.pkl: {len(P):,} nodes, {len(E):,} directed edges")
