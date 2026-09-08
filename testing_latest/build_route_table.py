"""
Route every origin-destination pair the Sheffield population actually needs, and
store the answers in a lookup table the DP can read.

The idea
--------
The DP asks "how far, and how long, from activity i to activity j?" for one
person at a time. Routing on demand inside the DP would be far too slow, and
routing every location against every other is out of the question: 130,265
locations is 17 billion pairs.

But a person only ever travels between their own handful of locations. Taken
across the whole population that is 1.78 million distinct ordered pairs -- four
orders of magnitude fewer than the full matrix, and small enough to precompute
once, store in 30 MB, and look up in constant time forever after.

This is the same idea as the notebook's 100x100 matrix, minus the assumption that
every location needs a distance to every other one.

How the routing is done
-----------------------
Each location is snapped to its nearest node in the road graph
(build_road_graph.py), exactly as the notebook's ox.distance.nearest_nodes does.
Pairs are then grouped by origin node, and each origin gets one Dijkstra search
that reaches all of its destinations at once. That is why grouping matters: a
person's five destinations cost one search, not five.

Two searches actually run per origin -- one weighted by distance, one by travel
time -- because the shortest route and the quickest route are not the same route.
This matches the notebook, which also runs single_source_dijkstra_path_length
twice.

Searches are capped at a distance a little beyond the farthest destination that
origin needs. Without a cap, every search would explore the entire 230 km graph
to answer a 3 km question.

What is not covered
-------------------
The graph covers a box around Sheffield (100 km by default). Around 7% of pairs
have an endpoint outside it -- these are genuine long-distance trips, some of
them 500 km. They are recorded as "off-graph" and the skim falls back to the
calibrated detour approximation for them. `--report` prints exactly how many.

Usage
-----
    python3 testing_latest/build_route_table.py                 # full population
    python3 testing_latest/build_route_table.py --limit 2000    # first 2000 people
    python3 testing_latest/build_route_table.py --report        # coverage only
"""

import argparse
import multiprocessing as mp
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
OSM_DIR = REPO_ROOT / "Sheffield_Project_model_input" / "routedistance" / "osm"
DEFAULT_GRAPH = OSM_DIR / "road_graph.npz"
DEFAULT_DATA = REPO_ROOT / "testing_latest" / "sheffield_activities_prepared.parquet"
DEFAULT_OUT = REPO_ROOT / "testing_latest" / "skim_cache" / "route_table.npz"
MMAP_DIR = OSM_DIR / "graph_mmap"

# How far past the straight-line distance a search is allowed to look. Road
# routes are longer than straight lines, so a cap at exactly the straight-line
# distance would miss almost everything. The measured detour factor tops out
# around 1.55 on short trips (see travel_skim.DETOUR_BANDS), so 1.8x plus 2 km
# leaves headroom while keeping searches local. A search that overruns its cap is
# retried with a wider one, so the cap costs accuracy nothing -- only time.
CUTOFF_FACTOR = 1.8
CUTOFF_PAD_M = 2_000.0

# Slowest average speed assumed when turning a distance budget into a time
# budget. Lower is safer (a bigger budget). No car route averages under 15 km/h.
SLOWEST_KPH = 15.0

# A location further than this from any road is treated as unroutable rather
# than silently snapped to a road on the far side of a river or a moor.
MAX_SNAP_M = 2_000.0

_G = None   # per-worker graph handle, set by _worker_init


class Graph:
    """The road graph as scipy sparse matrices, loaded through memory maps."""

    def __init__(self, mmap_dir):
        d = Path(mmap_dir)
        self.node_x = np.load(d / "node_x.npy", mmap_mode="r")
        self.node_y = np.load(d / "node_y.npy", mmap_mode="r")
        indptr = np.load(d / "indptr.npy", mmap_mode="r")
        indices = np.load(d / "indices.npy", mmap_mode="r")
        length = np.load(d / "length_m.npy", mmap_mode="r")
        time_s = np.load(d / "time_s.npy", mmap_mode="r")

        from scipy.sparse import csr_matrix
        n = len(self.node_x)
        # csr_matrix does not copy when the dtypes already match, so all eight
        # worker processes share one physical copy of the graph through the page
        # cache. This is the whole reason the arrays are written out as .npy
        # first rather than passed to the workers directly.
        self.dist = csr_matrix((length, indices, indptr), shape=(n, n))
        self.time = csr_matrix((time_s, indices, indptr), shape=(n, n))


def write_mmap_arrays(graph_npz, mmap_dir):
    """
    Unpack the compressed .npz into individual .npy files.

    An .npz is a zip archive, so every process reading it would decompress its
    own private copy -- roughly 1 GB each, eight times over. Plain .npy files can
    be memory-mapped and shared.
    """
    mmap_dir = Path(mmap_dir)
    stamp = mmap_dir / "built_from.txt"
    if stamp.exists() and stamp.read_text() == str(graph_npz.stat().st_mtime):
        return mmap_dir

    mmap_dir.mkdir(parents=True, exist_ok=True)
    with np.load(graph_npz, allow_pickle=False) as z:
        np.save(mmap_dir / "node_x.npy", z["node_x"].astype(np.float64))
        np.save(mmap_dir / "node_y.npy", z["node_y"].astype(np.float64))
        np.save(mmap_dir / "indptr.npy", z["indptr"].astype(np.int32))
        np.save(mmap_dir / "indices.npy", z["indices"].astype(np.int32))
        # float64 because scipy's Dijkstra wants float64 and would otherwise
        # convert -- which means copying, which means no sharing.
        np.save(mmap_dir / "length_m.npy", z["length_m"].astype(np.float64))
        np.save(mmap_dir / "time_s.npy", z["time_s"].astype(np.float64))
        np.save(mmap_dir / "bbox.npy", z["bbox"])
    stamp.write_text(str(graph_npz.stat().st_mtime))
    return mmap_dir


def collect_pairs(data_path, limit=None):
    """
    Every distinct ordered (origin, destination) pair the population needs.

    Returns (locations DataFrame with x/y, pair array of shape (m, 2) holding
    location indices).
    """
    df = pd.read_parquet(data_path, columns=["pid", "x", "y"])
    if limit:
        keep = pd.unique(df["pid"])[:limit]
        df = df[df["pid"].isin(set(keep))]

    locs, inverse = np.unique(df[["x", "y"]].to_numpy(), axis=0, return_inverse=True)
    df = df.assign(loc=inverse)

    pair_keys = []
    n_loc = len(locs)
    for _, person in df.groupby("pid", sort=False):
        ids = np.unique(person["loc"].to_numpy())
        if len(ids) < 2:
            continue
        a = np.repeat(ids, len(ids))
        b = np.tile(ids, len(ids))
        sel = a != b
        pair_keys.append(a[sel].astype(np.int64) * n_loc + b[sel])
    keys = np.unique(np.concatenate(pair_keys)) if pair_keys else np.zeros(0, np.int64)
    pairs = np.stack([keys // n_loc, keys % n_loc], axis=1)
    return pd.DataFrame(locs, columns=["x", "y"]), pairs


def snap(locations, graph, bbox):
    """
    Nearest road node for every location, plus a flag for the ones we cannot use.

    A location is unusable when it lies outside the graph's box (its true route
    would run through roads that were never loaded) or when the nearest road is
    implausibly far away.
    """
    from scipy.spatial import cKDTree

    tree = cKDTree(np.column_stack([np.asarray(graph.node_x),
                                    np.asarray(graph.node_y)]))
    xy = locations[["x", "y"]].to_numpy()
    snap_m, node = tree.query(xy, k=1, workers=-1)

    minx, miny, maxx, maxy = bbox
    inside = ((xy[:, 0] >= minx) & (xy[:, 0] <= maxx) &
              (xy[:, 1] >= miny) & (xy[:, 1] <= maxy))
    usable = inside & (snap_m <= MAX_SNAP_M)
    return node.astype(np.int64), snap_m, usable


def _worker_init(mmap_dir):
    global _G
    _G = Graph(mmap_dir)


def _route_origin(job):
    """
    One origin node, all of its destination nodes.

    Returns (origin_node, dest_nodes, metres, seconds); unreachable entries come
    back as inf and the caller falls back for them.
    """
    from scipy.sparse.csgraph import dijkstra

    o_node, d_nodes, cutoff_m = job
    d_nodes = np.asarray(d_nodes)

    limit_m = cutoff_m
    for attempt in range(3):
        dist = dijkstra(_G.dist, directed=True, indices=o_node, limit=limit_m)
        m = dist[d_nodes]
        if np.isfinite(m).all():
            break
        # Something sat outside the cap: widen it, and on the last attempt drop
        # it entirely, which is slow but correct.
        limit_m = np.inf if attempt == 1 else limit_m * 3

    # The time search gets its budget from what the distance search actually
    # found, rather than from the (deliberately generous) distance cap. The
    # quickest route can be no slower than the shortest route, and no route
    # averages under SLOWEST_KPH, so this bound is safe -- and it is typically
    # three times tighter than one derived from the cap, which is most of the
    # cost of this function.
    reach_m = m[np.isfinite(m)]
    budget_m = float(reach_m.max()) if len(reach_m) else cutoff_m
    limit_s = budget_m / (SLOWEST_KPH / 3.6)
    for attempt in range(3):
        secs = dijkstra(_G.time, directed=True, indices=o_node, limit=limit_s)
        s = secs[d_nodes]
        if np.isfinite(s).all():
            break
        limit_s = np.inf if attempt == 1 else limit_s * 3
    return o_node, d_nodes, m, s


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=None,
                    help="Only the first N persons (for a quick trial run)")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--report", action="store_true",
                    help="Print coverage and exit without routing")
    args = ap.parse_args()

    if not args.graph.exists():
        raise SystemExit(f"Road graph not found: {args.graph}\n"
                         f"Build it first: python3 testing_latest/build_road_graph.py")

    print("Collecting the pairs the population needs...")
    t0 = time.time()
    locations, pairs = collect_pairs(args.data, args.limit)
    print(f"    {len(locations):,} locations, {len(pairs):,} distinct ordered pairs "
          f"({time.time() - t0:.0f}s)")

    print("Loading the road graph...")
    mmap_dir = write_mmap_arrays(args.graph, MMAP_DIR)
    graph = Graph(mmap_dir)
    bbox = np.load(mmap_dir / "bbox.npy")
    print(f"    {len(graph.node_x):,} nodes, {graph.dist.nnz:,} edges")

    node, snap_m, usable = snap(locations, graph, bbox)
    print(f"    {usable.sum():,} of {len(locations):,} locations are on the graph "
          f"(median snap {np.median(snap_m[usable]):.0f} m)")

    ok = usable[pairs[:, 0]] & usable[pairs[:, 1]]
    print(f"    {ok.sum():,} of {len(pairs):,} pairs routable "
          f"({100 * ok.mean():.1f}%); {(~ok).sum():,} fall back to the detour "
          f"approximation")
    if args.report:
        return

    routable = pairs[ok]
    o_nodes = node[routable[:, 0]]
    d_nodes = node[routable[:, 1]]
    xy = locations[["x", "y"]].to_numpy()
    straight = np.hypot(xy[routable[:, 1], 0] - xy[routable[:, 0], 0],
                        xy[routable[:, 1], 1] - xy[routable[:, 0], 1])

    # Group by origin node: one search serves every destination from that origin.
    order = np.argsort(o_nodes, kind="stable")
    o_sorted = o_nodes[order]
    starts = np.flatnonzero(np.r_[True, o_sorted[1:] != o_sorted[:-1]])
    ends = np.r_[starts[1:], len(o_sorted)]

    jobs = []
    for s, e in zip(starts, ends):
        idx = order[s:e]
        cutoff = float(straight[idx].max()) * CUTOFF_FACTOR + CUTOFF_PAD_M
        jobs.append((int(o_sorted[s]), np.unique(d_nodes[idx]), cutoff))
    print(f"    {len(jobs):,} distinct origin nodes to search")

    # Results are collected per node pair first, then expanded back to location
    # pairs, because many locations share a node.
    n_nodes = len(graph.node_x)
    node_pair_key = {}

    print(f"Routing on {args.workers} workers...")
    t0 = time.time()
    done = 0
    ctx = mp.get_context("spawn")
    with ctx.Pool(args.workers, initializer=_worker_init, initargs=(str(mmap_dir),)) as pool:
        for o, dn, m, s in pool.imap_unordered(_route_origin, jobs, chunksize=16):
            base = int(o) * n_nodes
            for dnode, metres, secs in zip(dn, m, s):
                node_pair_key[base + int(dnode)] = (metres, secs)
            done += 1
            if done % 5000 == 0 or done == len(jobs):
                rate = done / (time.time() - t0)
                print(f"    {done:,}/{len(jobs):,} origins "
                      f"({rate:.0f}/s, {(len(jobs) - done) / rate / 60:.0f} min left)",
                      flush=True)

    print("Expanding node results back to location pairs...")
    keys = np.empty(len(routable), dtype=np.int64)
    dist_m = np.empty(len(routable), dtype=np.float32)
    time_min = np.empty(len(routable), dtype=np.float32)
    n_loc = len(locations)
    unreachable = 0
    for i, (o_loc, d_loc) in enumerate(routable):
        metres, secs = node_pair_key[int(node[o_loc]) * n_nodes + int(node[d_loc])]
        if not (np.isfinite(metres) and np.isfinite(secs)):
            unreachable += 1
            metres, secs = np.nan, np.nan
        keys[i] = int(o_loc) * n_loc + int(d_loc)
        dist_m[i] = metres
        time_min[i] = secs / 60.0

    good = np.isfinite(dist_m) & np.isfinite(time_min)
    order = np.argsort(keys[good])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        loc_x=xy[:, 0], loc_y=xy[:, 1],
        keys=keys[good][order],
        dist_m=dist_m[good][order],
        time_min=time_min[good][order],
        n_loc=np.int64(n_loc),
    )
    print(f"Saved {args.out} ({args.out.stat().st_size / 1e6:.0f} MB)")
    print(f"    {good.sum():,} pairs routed; {unreachable:,} unreachable on the "
          f"road graph; {(~ok).sum():,} outside it. Both fall back to the detour "
          f"approximation at lookup time.")


if __name__ == "__main__":
    main()
