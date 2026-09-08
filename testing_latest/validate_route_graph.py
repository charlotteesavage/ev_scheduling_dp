"""
Check the road graph against the matrices the Sheffield notebook produced.

The notebook (Sheffield_Project_model_input/routedistance/distance_time_cal.ipynb)
routed the first 100 locations with OSMnx and NetworkX and saved the answers.
This script routes those same 100 locations over the graph built by
build_road_graph.py and compares, pair by pair.

That comparison is the whole point: the graph here is built by different code,
from a different source (a downloaded extract rather than a live Overpass query),
and stored in a different structure. If it reproduces the notebook's numbers, the
rewrite is faithful and the population-scale table can be trusted. If it does
not, the difference shows up here rather than silently inside 127,000 schedules.

Exact agreement is not expected. The two graphs were downloaded at different
times, so roads have been added, closed and retagged in between, and the
notebook's bounding box is smaller, which can force a detour where this graph
takes a through road. A median difference of a few percent is the target; a
median difference of tens of percent means something is wrong.

Usage
-----
    python3 testing_latest/validate_route_graph.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
SAMPLE_DIR = REPO_ROOT / "Sheffield_Project_model_input" / "routedistance"
GRAPH = REPO_ROOT / "Sheffield_Project_model_input" / "routedistance" / "osm" / "road_graph.npz"

LABEL = re.compile(r"^(\d+):(.+) \((-?\d+),(-?\d+)\)$")


def sample_matrices():
    """The notebook's answers, plus the coordinates they were computed for."""
    dist = pd.read_csv(SAMPLE_DIR / "distance_matrix_route_m.csv", index_col=0)
    time = pd.read_csv(SAMPLE_DIR / "time_matrix_route_min.csv", index_col=0)
    coords = np.array([[float(m.group(3)), float(m.group(4))]
                       for m in (LABEL.match(s) for s in dist.index)])
    return coords, dist.to_numpy(), time.to_numpy()


def route_here(coords):
    """Route the same points over the graph this repo builds."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    with np.load(GRAPH, allow_pickle=False) as z:
        node_x, node_y = z["node_x"], z["node_y"]
        indptr, indices = z["indptr"], z["indices"]
        length = z["length_m"].astype(np.float64)
        time_s = z["time_s"].astype(np.float64)

    n = len(node_x)
    g_dist = csr_matrix((length, indices, indptr), shape=(n, n))
    g_time = csr_matrix((time_s, indices, indptr), shape=(n, n))

    tree = cKDTree(np.column_stack([node_x, node_y]))
    snap_m, nodes = tree.query(coords, k=1, workers=-1)
    print(f"Snapped {len(coords)} points; median {np.median(snap_m):.0f} m, "
          f"worst {snap_m.max():.0f} m from a road")

    uniq, inverse = np.unique(nodes, return_inverse=True)
    d = dijkstra(g_dist, directed=True, indices=uniq)[:, uniq]
    t = dijkstra(g_time, directed=True, indices=uniq)[:, uniq] / 60.0
    return d[inverse][:, inverse], t[inverse][:, inverse], snap_m


def compare(name, mine, theirs, unit):
    off = ~np.eye(len(mine), dtype=bool)
    a, b = mine[off], theirs[off]
    ok = np.isfinite(a) & np.isfinite(b) & (b > 0)
    a, b = a[ok], b[ok]

    ratio = a / b
    pct = 100 * np.abs(ratio - 1)
    corr = np.corrcoef(a, b)[0, 1]

    print(f"\n{name} ({unit}) -- {len(a):,} comparable pairs")
    print(f"  correlation with the notebook : {corr:.5f}")
    print(f"  median ratio (mine / theirs)  : {np.median(ratio):.4f}")
    print(f"  median absolute difference    : {np.median(pct):.2f}%")
    print(f"  90th percentile difference    : {np.percentile(pct, 90):.2f}%")
    print(f"  pairs differing by over 25%   : {(pct > 25).sum()} "
          f"({100 * (pct > 25).mean():.1f}%)")
    return corr, float(np.median(pct))


def main():
    if not GRAPH.exists():
        raise SystemExit(f"No road graph at {GRAPH}. Run build_road_graph.py first.")

    coords, dist_ref, time_ref = sample_matrices()
    print(f"Notebook sample: {len(coords)} locations, "
          f"{np.isfinite(dist_ref).sum() - len(coords):,} routed pairs")

    dist_mine, time_mine, snap_m = route_here(coords)

    unreachable = ~np.isfinite(dist_mine)
    if unreachable.any():
        print(f"WARNING: {unreachable.sum()} pairs unreachable on this graph")

    d_corr, d_pct = compare("Distance", dist_mine, dist_ref, "metres")
    t_corr, t_pct = compare("Travel time", time_mine, time_ref, "minutes")

    print("\nVerdict:")
    if d_corr > 0.98 and d_pct < 10 and t_corr > 0.95 and t_pct < 15:
        print("  The graph reproduces the notebook's routing. Good to build the "
              "population table on.")
    else:
        print("  The graph does NOT reproduce the notebook closely enough. "
              "Check the road-type filter, the speed table and the one-way "
              "handling in build_road_graph.py before going further.")


if __name__ == "__main__":
    main()
