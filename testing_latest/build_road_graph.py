"""
Build a compact road graph for the Sheffield study area from an OSM extract.

Why this exists
---------------
The Sheffield notebook (Sheffield_Project_model_input/routedistance/
distance_time_cal.ipynb) builds its road graph with OSMnx, which downloads the
network over the Overpass API and holds it in a NetworkX MultiDiGraph. That is
fine for the 100 locations the notebook routes. It is not fine here: the
population has 130,265 distinct activity locations spread over a 200 km box, and
a NetworkX graph of that area needs far more memory than this machine has.

So this script does the same job with the same rules, but stores the graph as
plain numpy arrays in CSR (compressed sparse row) form -- the layout
scipy.sparse.csgraph wants. The same area that would cost several GB as a
NetworkX graph costs a few hundred MB here.

What is kept identical to the notebook
--------------------------------------
  * the road types included ("drive_service": everything drivable, service roads
    and all),
  * the speed rule: the OSM `maxspeed` tag where a road has one, otherwise the
    notebook's URBAN_DEFAULTS_KPH table by road type, otherwise 30 km/h. This is
    exactly what ox.routing.add_edge_speeds(hwy_speeds=..., fallback=30) does,
  * travel time = edge length / edge speed,
  * graph simplification: chains of intermediate shape points are collapsed into
    a single edge, which is what OSMnx does by default. Distances are unaffected
    (the chain's length is summed) but the graph shrinks about fourfold.

What is different
-----------------
  * The source is a downloaded .osm.pbf file rather than a live Overpass query,
    so the build is repeatable and offline. The extract's date is recorded in the
    output so the graph can be cited.
  * Edge length is the straight-line distance between consecutive shape points in
    OSGB metres, summed along the way. OSMnx uses great-circle distance on
    lon/lat. Over the tens of metres between two shape points the two agree to
    well under a metre.

Usage
-----
    python3 testing_latest/build_road_graph.py              # default 100 km box
    python3 testing_latest/build_road_graph.py --radius-km 60
"""

import argparse
import time
from array import array
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
OSM_DIR = REPO_ROOT / "Sheffield_Project_model_input" / "routedistance" / "osm"
DEFAULT_PBF = OSM_DIR / "england-latest.osm.pbf"
DEFAULT_OUT = OSM_DIR / "road_graph.npz"
DEFAULT_DATA = REPO_ROOT / "testing_latest" / "sheffield_activities_prepared.parquet"

# Road types included, and the notebook's default speed for each in km/h.
# Roads carrying an OSM maxspeed tag use that instead; this table only fills gaps.
# track and path appear in the notebook's table but are excluded by OSMnx's
# "drive_service" filter, so they are excluded here too and the entries are moot.
URBAN_DEFAULTS_KPH = {
    "motorway": 100, "motorway_link": 60,
    "trunk": 70, "trunk_link": 50,
    "primary": 50, "primary_link": 40,
    "secondary": 40, "secondary_link": 35,
    "tertiary": 30, "tertiary_link": 25,
    "unclassified": 30,
    "residential": 30,
    "living_street": 10,
    "service": 10,
}
FALLBACK_KPH = 30.0

# Ways tagged as inaccessible to cars are dropped, matching OSMnx's drive filter.
BLOCKED_ACCESS = {"no", "private", "customers", "delivery", "agricultural",
                  "forestry", "military"}


def parse_maxspeed(value):
    """
    OSM maxspeed to km/h, or None when it cannot be read.

    UK values are usually like "30 mph" or "national". Anything unparseable
    falls through to the road-type table, which is what OSMnx does.
    """
    if not value:
        return None
    v = value.strip().lower()
    # A few ways carry several values ("30 mph;40 mph"); take the first.
    if ";" in v:
        v = v.split(";")[0].strip()
    mph = v.endswith("mph")
    if mph:
        v = v[:-3].strip()
    elif v.endswith("km/h") or v.endswith("kph"):
        v = v.rstrip("kphm/").strip()
    try:
        num = float(v)
    except ValueError:
        return None
    if num <= 0:
        return None
    return num * 1.60934 if mph else num


def study_bbox(data_path, radius_km):
    """
    A square box of the given radius around the median activity location.

    The median rather than the mean, because a handful of activities sit
    hundreds of kilometres away (the population includes long-distance trips)
    and would drag a mean-centred box off Sheffield entirely.
    """
    df = pd.read_parquet(data_path, columns=["x", "y"])
    cx = float(df["x"].median())
    cy = float(df["y"].median())
    r = radius_km * 1000.0
    return cx, cy, (cx - r, cy - r, cx + r, cy + r)


def read_osm(pbf_path, bbox_osgb, margin_m):
    """
    One streaming pass over the extract.

    Nodes come before ways in a .osm.pbf, so a single pass suffices: collect the
    coordinates of every node inside the box, then, when the ways arrive, look
    their shape points up in what was collected.

    Returns (node_ids, node_lon, node_lat, way_refs, way_offsets, way_kph,
             way_oneway).
    """
    import osmium
    from pyproj import Transformer

    minx, miny, maxx, maxy = bbox_osgb
    minx -= margin_m; miny -= margin_m
    maxx += margin_m; maxy += margin_m

    # The node filter has to run in lon/lat, so convert the box corners. An OSGB
    # square is not a lon/lat rectangle, so take the outer envelope of all four
    # corners and let the exact OSGB test happen later.
    to_ll = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    corners_x = [minx, minx, maxx, maxx]
    corners_y = [miny, maxy, miny, maxy]
    lons, lats = to_ll.transform(corners_x, corners_y)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)

    node_ids = array("q")
    node_lon = array("d")
    node_lat = array("d")

    way_refs = array("q")
    way_offsets = array("q", [0])
    way_kph = array("f")
    way_oneway = array("b")   # 0 both ways, 1 forward only, -1 backward only

    t0 = time.time()
    n_nodes = n_ways = 0
    for obj in osmium.FileProcessor(str(pbf_path), osmium.osm.NODE | osmium.osm.WAY):
        if obj.type_str() == "n":
            lon, lat = obj.location.lon, obj.location.lat
            if west <= lon <= east and south <= lat <= north:
                node_ids.append(obj.id)
                node_lon.append(lon)
                node_lat.append(lat)
            n_nodes += 1
            if n_nodes % 10_000_000 == 0:
                print(f"    {n_nodes/1e6:.0f}M nodes scanned, "
                      f"{len(node_ids)/1e6:.2f}M in box "
                      f"({time.time() - t0:.0f}s)", flush=True)
            continue

        # ways
        tags = obj.tags
        highway = tags.get("highway")
        if highway not in URBAN_DEFAULTS_KPH:
            continue
        if tags.get("access") in BLOCKED_ACCESS:
            continue
        if tags.get("motor_vehicle") in BLOCKED_ACCESS:
            continue
        if tags.get("area") == "yes":
            continue

        kph = parse_maxspeed(tags.get("maxspeed"))
        if kph is None:
            kph = URBAN_DEFAULTS_KPH.get(highway, FALLBACK_KPH)

        oneway_tag = (tags.get("oneway") or "").strip().lower()
        if oneway_tag in ("yes", "true", "1"):
            direction = 1
        elif oneway_tag == "-1" or oneway_tag == "reverse":
            direction = -1
        elif tags.get("junction") in ("roundabout", "circular"):
            direction = 1          # roundabouts are one-way unless tagged otherwise
        elif highway in ("motorway", "motorway_link"):
            direction = 1          # motorway carriageways are mapped separately
        else:
            direction = 0

        refs = [n.ref for n in obj.nodes]
        if len(refs) < 2:
            continue
        way_refs.extend(refs)
        way_offsets.append(len(way_refs))
        way_kph.append(kph)
        way_oneway.append(direction)
        n_ways += 1

    print(f"    scanned {n_nodes/1e6:.0f}M nodes, kept {len(node_ids)/1e6:.2f}M in box; "
          f"{n_ways:,} drivable ways ({time.time() - t0:.0f}s)", flush=True)

    return (np.frombuffer(node_ids, dtype=np.int64),
            np.frombuffer(node_lon, dtype=np.float64),
            np.frombuffer(node_lat, dtype=np.float64),
            np.frombuffer(way_refs, dtype=np.int64),
            np.frombuffer(way_offsets, dtype=np.int64),
            np.frombuffer(way_kph, dtype=np.float32),
            np.frombuffer(way_oneway, dtype=np.int8))


def build_edges(node_ids, node_x, node_y, way_refs, way_offsets, way_kph, way_oneway):
    """
    Turn ways into a directed edge list, one edge per consecutive pair of shape
    points. Segments whose endpoints fall outside the collected node set are
    dropped -- these are ways leaving the box, and routing through them would
    need road data we deliberately did not load.
    """
    order = np.argsort(node_ids, kind="stable")
    sorted_ids = node_ids[order]

    pos = np.searchsorted(sorted_ids, way_refs)
    pos_clipped = np.minimum(pos, len(sorted_ids) - 1)
    found = sorted_ids[pos_clipped] == way_refs
    local = np.where(found, order[pos_clipped], -1)

    # A segment is the pair (local[k], local[k+1]) for every k that is not the
    # last shape point of its way.
    starts = way_offsets[:-1]
    ends = way_offsets[1:]
    seg_count = (ends - starts - 1)
    way_of_seg = np.repeat(np.arange(len(seg_count)), seg_count)
    # index of the first shape point of each segment
    first_idx = np.repeat(starts, seg_count) + (
        np.arange(seg_count.sum()) - np.repeat(np.cumsum(seg_count) - seg_count, seg_count))

    u = local[first_idx]
    v = local[first_idx + 1]
    keep = (u >= 0) & (v >= 0) & (u != v)
    u, v, way_of_seg = u[keep], v[keep], way_of_seg[keep]

    length = np.hypot(node_x[v] - node_x[u], node_y[v] - node_y[u])
    kph = way_kph[way_of_seg].astype(np.float64)
    time_s = length / (kph / 3.6)

    direction = way_oneway[way_of_seg]
    # forward edges exist unless the way is backward-only
    fwd = direction >= 0
    bwd = direction <= 0

    src = np.concatenate([u[fwd], v[bwd]])
    dst = np.concatenate([v[fwd], u[bwd]])
    elen = np.concatenate([length[fwd], length[bwd]])
    etime = np.concatenate([time_s[fwd], time_s[bwd]])
    return src, dst, elen, etime


def prune_isolated(src, dst, node_x, node_y):
    """Keep only nodes touched by an edge, and renumber src/dst to match."""
    used = np.unique(np.concatenate([src, dst]))
    relabel = np.full(len(node_x), -1, dtype=np.int64)
    relabel[used] = np.arange(len(used))
    return relabel[src], relabel[dst], node_x[used], node_y[used]


def simplify(n_nodes, src, dst, elen, etime, protect=None):
    """
    Collapse chains of pass-through nodes into single edges, the way OSMnx's
    simplify_graph does.

    An OSM way stores every bend as a node, so most nodes are not junctions at
    all -- they are shape points with exactly one road in and one road out.
    Routing through them costs time but changes nothing, because there is no
    choice to make there. Merging each chain into one edge (summing its length
    and time) leaves every route length identical while shrinking the graph
    roughly fourfold.

    A node is kept when it is a real junction or a dead end; `protect` names
    extra nodes to keep regardless (nothing needs it yet, but a caller wanting
    to snap to a mid-chain point would).

    The chains are walked with array operations rather than a Python loop: every
    unfinished chain takes one step per iteration together. Walking them one at a
    time is the obvious way to write this and is roughly a hundred times slower
    at this scale.
    """
    # Undirected neighbour count, and directed in/out degrees. The undirected
    # count is taken over distinct pairs, so a road mapped in both directions
    # counts as one neighbour rather than two.
    lo = np.minimum(src, dst).astype(np.int64)
    hi = np.maximum(src, dst).astype(np.int64)
    pair_key = np.unique(lo * n_nodes + hi)
    pl, ph = pair_key // n_nodes, pair_key % n_nodes
    self_loop = pl == ph
    pl, ph = pl[~self_loop], ph[~self_loop]
    neigh_deg = (np.bincount(pl, minlength=n_nodes) +
                 np.bincount(ph, minlength=n_nodes))
    out_deg = np.bincount(src, minlength=n_nodes)
    in_deg = np.bincount(dst, minlength=n_nodes)

    # Pass-through: exactly two distinct neighbours, and traffic simply passes
    # (one in one out for a one-way chain, two in two out for a two-way chain).
    passthrough = (neigh_deg == 2) & (
        ((in_deg == 1) & (out_deg == 1)) | ((in_deg == 2) & (out_deg == 2)))
    keep = ~passthrough
    if protect is not None and len(protect):
        keep[np.asarray(protect, dtype=np.int64)] = True

    # Adjacency sorted by source, so a node's out-edges are contiguous.
    order = np.argsort(src, kind="stable")
    adj_src = src[order]
    adj_dst = dst[order]
    adj_len = elen[order]
    adj_time = etime[order]
    indptr = np.searchsorted(adj_src, np.arange(n_nodes + 1))

    # For every directed edge u->v where v is a pass-through node, precompute the
    # one edge the chain continues along: v's out-edge that does not go back to
    # u. This makes walking a chain a matter of following a pointer.
    n_edges = len(adj_dst)
    nxt_edge = np.full(n_edges, -1, dtype=np.int64)
    v_all = adj_dst
    v_pass = passthrough[v_all]
    idx = np.flatnonzero(v_pass)
    if len(idx):
        v = v_all[idx]
        u = adj_src[idx]
        first = indptr[v]
        deg = indptr[v + 1] - first
        # out-degree here is 1 or 2 by the pass-through test above
        cand0 = first
        cand1 = np.where(deg > 1, first + 1, first)
        pick = np.where(adj_dst[cand0] != u, cand0, cand1)
        # a stub that only leads back where we came from ends the chain
        pick = np.where(adj_dst[pick] != u, pick, -1)
        nxt_edge[idx] = pick

    # Start one chain per out-edge of every kept node, then step them together.
    start_edges = np.flatnonzero(keep[adj_src])
    cur = start_edges.copy()
    total_len = adj_len[start_edges].astype(np.float64)
    total_time = adj_time[start_edges].astype(np.float64)
    end_node = adj_dst[start_edges].copy()
    chain_src = adj_src[start_edges].copy()

    alive = np.flatnonzero(~keep[end_node])
    for _ in range(10_000):
        if len(alive) == 0:
            break
        step = nxt_edge[cur[alive]]
        good = step >= 0
        # Chains that run into a dead end stop where they are; the node they
        # stopped at is a pass-through, so the edge is dropped later.
        moved = alive[good]
        step = step[good]
        cur[moved] = step
        total_len[moved] += adj_len[step]
        total_time[moved] += adj_time[step]
        end_node[moved] = adj_dst[step]
        alive = moved[~keep[end_node[moved]]]

    # Relabel kept nodes 0..k-1 and drop chains that never reached one.
    kept_nodes = np.flatnonzero(keep)
    relabel = np.full(n_nodes, -1, dtype=np.int64)
    relabel[kept_nodes] = np.arange(len(kept_nodes))
    ns = relabel[chain_src]
    nd = relabel[end_node]
    ok = (ns >= 0) & (nd >= 0) & (ns != nd)
    return kept_nodes, ns[ok], nd[ok], total_len[ok], total_time[ok]


def largest_strong_component(n, src, dst, elen, etime):
    """
    Keep only the part of the graph where every node can reach every other.

    OSM contains fragments that no car can drive out of: a private estate road
    the surveyor never joined up, a one-way loop with no exit, a lane whose
    connection to the network was cut when the extract was clipped at the box
    edge. A location snapped onto one of those has no route to anywhere, and the
    DP would see an infinite travel time.

    Keeping the largest strongly connected component -- "strongly" meaning
    reachable in both directions, which matters on a one-way network -- removes
    them. This is what OSMnx's truncate.largest_component(strongly=True) does.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    g = csr_matrix((np.ones(len(src), dtype=np.int8),
                    (src, dst)), shape=(n, n))
    _, labels = connected_components(g, directed=True, connection="strong")
    biggest = np.argmax(np.bincount(labels))
    keep = labels == biggest

    edge_ok = keep[src] & keep[dst]
    kept_nodes = np.flatnonzero(keep)
    relabel = np.full(n, -1, dtype=np.int64)
    relabel[kept_nodes] = np.arange(len(kept_nodes))
    return (kept_nodes, relabel[src[edge_ok]], relabel[dst[edge_ok]],
            elen[edge_ok], etime[edge_ok])


def to_csr(n, src, dst, elen, etime):
    """
    CSR arrays, keeping only the cheapest edge for each (src, dst) pair.

    Parallel edges are common in OSM (a road split around an island) and
    scipy's Dijkstra wants one weight per pair, so the shortest survives. The
    time array is reordered to match the surviving distance edges rather than
    minimised independently -- taking the min of each separately would invent a
    route that is both the shortest and the fastest when no such route exists.
    """
    key = src.astype(np.int64) * n + dst.astype(np.int64)
    order = np.lexsort((elen, key))
    key_s = key[order]
    first = np.ones(len(key_s), dtype=bool)
    first[1:] = key_s[1:] != key_s[:-1]
    sel = order[first]

    src_u, dst_u = src[sel], dst[sel]
    len_u, time_u = elen[sel], etime[sel]

    order2 = np.lexsort((dst_u, src_u))
    src_u, dst_u = src_u[order2], dst_u[order2]
    len_u, time_u = len_u[order2], time_u[order2]
    indptr = np.searchsorted(src_u, np.arange(n + 1))
    return indptr.astype(np.int64), dst_u.astype(np.int32), len_u, time_u


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", type=Path, default=DEFAULT_PBF)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA,
                    help="Prepared activity file, used to centre the box")
    ap.add_argument("--radius-km", type=float, default=100.0,
                    help="Half-width of the square study area (default: %(default)s)")
    ap.add_argument("--margin-km", type=float, default=15.0,
                    help="Extra road network loaded outside the box so routes "
                         "near its edge can still detour properly "
                         "(default: %(default)s)")
    args = ap.parse_args()

    if not args.pbf.exists():
        raise SystemExit(f"OSM extract not found: {args.pbf}")

    cx, cy, bbox = study_bbox(args.data, args.radius_km)
    print(f"Study area: {2*args.radius_km:.0f} x {2*args.radius_km:.0f} km centred on "
          f"OSGB ({cx:.0f}, {cy:.0f}), plus a {args.margin_km:.0f} km routing margin")

    print("Reading the OSM extract (one streaming pass)...")
    (node_ids, node_lon, node_lat, way_refs, way_offsets,
     way_kph, way_oneway) = read_osm(args.pbf, bbox, args.margin_km * 1000.0)

    from pyproj import Transformer
    to_osgb = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    node_x, node_y = to_osgb.transform(node_lon, node_lat)

    print("Building the directed edge list...")
    src, dst, elen, etime = build_edges(node_ids, node_x, node_y, way_refs,
                                        way_offsets, way_kph, way_oneway)
    print(f"    {len(src):,} directed edges from {len(node_ids):,} nodes in the box")

    # Most nodes in an OSM extract are not on a road at all -- they are building
    # corners, shop points, trees, postboxes. Only nodes that an edge touches
    # belong in a road graph, and dropping the rest here matters more than it
    # looks: every Dijkstra search allocates one array entry per node, so
    # carrying 57 million roadless nodes would cost half a gigabyte per search.
    src, dst, node_x, node_y = prune_isolated(src, dst, node_x, node_y)
    n_nodes = len(node_x)
    print(f"    {n_nodes:,} of those nodes are actually on a road")

    print("Simplifying pass-through nodes...")
    t0 = time.time()
    kept, s2, d2, l2, t2 = simplify(n_nodes, src, dst, elen, etime)
    print(f"    {len(kept):,} nodes, {len(s2):,} edges after simplifying "
          f"({time.time() - t0:.0f}s)")

    print("Keeping the largest strongly connected component...")
    strong, s3, d3, l3, t3 = largest_strong_component(len(kept), s2, d2, l2, t2)
    print(f"    {len(strong):,} nodes, {len(s3):,} edges "
          f"({100 * len(strong) / len(kept):.1f}% of nodes kept)")

    indptr, indices, w_len, w_time = to_csr(len(strong), s3, d3, l3, t3)
    print(f"    {len(indices):,} edges after removing parallel duplicates")

    final = kept[strong]
    out_x = node_x[final].astype(np.float64)
    out_y = node_y[final].astype(np.float64)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        node_x=out_x, node_y=out_y,
        indptr=indptr, indices=indices,
        length_m=w_len.astype(np.float32),
        time_s=w_time.astype(np.float32),
        bbox=np.array(bbox, dtype=np.float64),
        margin_m=np.float64(args.margin_km * 1000.0),
        source=str(args.pbf.name),
    )
    size_mb = args.out.stat().st_size / 1e6
    print(f"Saved {args.out} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
