"""
Per-person travel skims (road distance and road time) for the scheduling DP.

Adapted from compute_tmat() in the OASIS reference implementation
(`OASIS EV TRD paper code/data_utils.py`). What was kept from it:

  * one small matrix per person, not one huge matrix for the population
  * a dict-of-dicts keyed by location, computed on a cache miss
  * results persisted so a second run is free

What was changed, and why:

  * BACKEND. compute_tmat() called the Google Directions API once per
    origin-destination pair. This population needs ~1,000,000 distinct pairs,
    which is roughly £4,000 of Google quota and needs the network for every one.
    The Sheffield notebook (Sheffield_Project_model_input/routedistance/
    distance_time_cal.ipynb) already does the same job locally with OSMnx and a
    NetworkX Dijkstra, for free and offline, so that is the backend here.
  * CACHE KEY. compute_tmat() cached per household, so a leg shared by two
    households was routed twice. Here the cache is keyed on the location PAIR, so
    every distinct pair is routed once for the whole population. Persons overlap
    heavily (everyone drives to the same town centres), so this matters.
  * DISTANCE AS WELL AS TIME. compute_tmat() returned time only, and
    compute_distances_from_tmat() then took straight-line distances between the
    same points. The DP needs true road distance too, because distance drives
    battery consumption while time drives feasibility. Both come out of the same
    Dijkstra here, so returning only one would be wasteful as well as wrong.

Backends
--------
euclidean : straight-line distance, flat speed. Reproduces the model's original
            behaviour exactly -- used to prove the skim plumbing is correct.
detour    : straight-line distance scaled by a distance-banded factor calibrated
            against the 100x100 sample matrices. An approximation, but a
            measured one, and it needs no new data.
route     : real road distance and time, looked up in the table built by
            build_road_graph.py + build_route_table.py. Pairs outside the road
            graph's box fall back to `detour`.

Usage
-----
    skim = TravelSkim.from_name("route")
    dist_m, time_min = skim.matrices(person_df)   # both n x n, activity-id indexed
"""

import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / "testing_latest" / "skim_cache"
SAMPLE_DIR = REPO_ROOT / "Sheffield_Project_model_input" / "routedistance"
ROUTE_TABLE = CACHE_DIR / "route_table.npz"

# Flat speed used by the euclidean backend, matching testing_check.SPEED.
# 20.4 mph -> km/h -> metres per minute.
DEFAULT_SPEED_M_PER_MIN = 20.4 * 1.60934 * 16.667


# ── Detour factors ────────────────────────────────────────────────────────────
# Calibrated from distance_matrix_route_m.csv against straight-line distance
# between the same 100 locations (9,900 ordered pairs). The factor falls with
# trip length -- short trips are dominated by street layout, long ones follow
# trunk roads that run closer to straight.
#
# calibrate_detour_factors() below regenerates these from the sample matrices;
# they are inlined so the module works without the sample files present.
DETOUR_BANDS = [
    (0.0, 1_000.0, 1.554),
    (1_000.0, 5_000.0, 1.263),
    (5_000.0, 20_000.0, 1.195),
    (20_000.0, math.inf, 1.166),
]

# Implied road speed by band, from time_matrix_route_min.csv over the same pairs.
# Short urban trips are slow; long trips pick up trunk roads and motorways. A
# single flat speed cannot represent both, which is the main reason the euclidean
# backend understates travel time in town and overstates it between towns.
SPEED_BANDS_KPH = [
    (0.0, 1_000.0, 32.2),
    (1_000.0, 5_000.0, 44.2),
    (5_000.0, 20_000.0, 54.1),
    (20_000.0, math.inf, 68.0),
]


def _band_value(bands, distance_m):
    for lo, hi, value in bands:
        if lo <= distance_m < hi:
            return value
    return bands[-1][2]


# ── Backends ──────────────────────────────────────────────────────────────────

class _Backend:
    """A backend maps a batch of (x1, y1, x2, y2) to (metres, minutes)."""

    name = "base"
    cacheable = True   # results are worth pickling; a table-backed one is not

    def route(self, origins, destinations):
        raise NotImplementedError


class EuclideanBackend(_Backend):
    """Straight-line distance at a flat speed -- the model's original behaviour."""

    name = "euclidean"

    def __init__(self, speed_m_per_min=DEFAULT_SPEED_M_PER_MIN):
        self.speed = speed_m_per_min

    def route(self, origins, destinations):
        d = np.hypot(destinations[:, 0] - origins[:, 0],
                     destinations[:, 1] - origins[:, 1])
        return d, d / self.speed


class DetourBackend(_Backend):
    """
    Straight-line distance scaled by a measured, distance-banded detour factor,
    with a banded speed rather than one flat number.

    This is an approximation and should be described as one. It exists so the
    model can stop using raw Pythagoras today, without waiting for a full routing
    job, and so the skim plumbing has a second backend to prove it is not
    silently hard-wired to Euclidean.
    """

    name = "detour"

    def route(self, origins, destinations):
        straight = np.hypot(destinations[:, 0] - origins[:, 0],
                            destinations[:, 1] - origins[:, 1])
        factors = np.array([_band_value(DETOUR_BANDS, d) for d in straight])
        metres = straight * factors
        kph = np.array([_band_value(SPEED_BANDS_KPH, d) for d in straight])
        minutes = np.divide(metres / 1000.0, kph / 60.0,
                            out=np.zeros_like(metres), where=kph > 0)
        return metres, minutes


class RouteTableBackend(_Backend):
    """
    Real road distance and time, read from the precomputed table.

    build_route_table.py routes every pair the population needs over an OSM road
    graph and stores the answers; this backend just looks them up. That keeps the
    expensive part (routing) out of the DP entirely -- by the time a schedule is
    being optimised, every distance it can ask for has already been computed.

    Pairs outside the road graph's box, and the rare pair the graph cannot
    connect, fall back to the detour approximation. `misses` counts them so a run
    can report honestly how much of it was routed and how much was approximated.
    """

    name = "route"
    cacheable = False   # the table is already the cache; re-pickling it would
                        # just cost every worker a second copy in memory

    def __init__(self, table_path=None):
        path = Path(table_path) if table_path else ROUTE_TABLE
        if not path.exists():
            raise FileNotFoundError(
                f"No route table at {path}.\n"
                "Build one with:\n"
                "    python3 testing_latest/build_road_graph.py\n"
                "    python3 testing_latest/build_route_table.py")

        with np.load(path, allow_pickle=False) as z:
            self._keys = z["keys"]
            self._dist = z["dist_m"]
            self._time = z["time_min"]
            self._n_loc = int(z["n_loc"])
            loc_x, loc_y = z["loc_x"], z["loc_y"]

        # Coordinates are the identity of a location here, so they are rounded to
        # the centimetre before being used as dictionary keys. Comparing raw
        # floats would work today (both sides come from the same file) but would
        # break silently the moment anything re-derives a coordinate.
        self._loc_of = {(round(float(x), 2), round(float(y), 2)): i
                        for i, (x, y) in enumerate(zip(loc_x, loc_y))}
        self._fallback = DetourBackend()
        self.hits = 0
        self.misses = 0

    def route(self, origins, destinations):
        n = len(origins)
        metres = np.full(n, np.nan)
        minutes = np.full(n, np.nan)

        # Two activities at the same coordinates are zero apart. The table does
        # not store those pairs, so without this they would look like misses and
        # make the coverage figure below look far worse than it is.
        same = (origins[:, 0] == destinations[:, 0]) & \
               (origins[:, 1] == destinations[:, 1])
        metres[same] = 0.0
        minutes[same] = 0.0

        o_loc = np.array([self._loc_of.get((round(float(x), 2), round(float(y), 2)), -1)
                          for x, y in origins], dtype=np.int64)
        d_loc = np.array([self._loc_of.get((round(float(x), 2), round(float(y), 2)), -1)
                          for x, y in destinations], dtype=np.int64)

        known = (o_loc >= 0) & (d_loc >= 0) & ~same
        if known.any() and len(self._keys):
            wanted = o_loc[known] * self._n_loc + d_loc[known]
            pos = np.searchsorted(self._keys, wanted)
            pos_safe = np.minimum(pos, len(self._keys) - 1)
            found = self._keys[pos_safe] == wanted
            idx = np.flatnonzero(known)[found]
            metres[idx] = self._dist[pos_safe[found]]
            minutes[idx] = self._time[pos_safe[found]]

        missing = ~np.isfinite(metres)
        self.hits += int((~missing).sum())
        self.misses += int(missing.sum())
        if missing.any():
            m, t = self._fallback.route(origins[missing], destinations[missing])
            metres[missing] = m
            minutes[missing] = t
        return metres, minutes


BACKENDS = {
    "euclidean": EuclideanBackend,
    "detour": DetourBackend,
    "route": RouteTableBackend,
}


# ── The skim ──────────────────────────────────────────────────────────────────

class TravelSkim:
    """
    Builds per-person origin-destination matrices, caching every distinct
    location pair so it is routed once for the whole population.
    """

    def __init__(self, backend, cache_path=None, autosave_every=50_000):
        self.backend = backend
        self.cache_path = Path(cache_path) if cache_path else (
            CACHE_DIR / f"pairs_{backend.name}.pickle")
        self.autosave_every = autosave_every
        self._pairs = {}          # (x1, y1, x2, y2) -> (metres, minutes)
        self._since_save = 0
        self.hits = 0
        self.misses = 0
        if self.backend.cacheable:
            self.load()

    @classmethod
    def from_name(cls, name, **kwargs):
        if name not in BACKENDS:
            raise ValueError(
                f"Unknown backend {name!r}. Available: {sorted(BACKENDS)}")
        backend_kwargs = {k: kwargs.pop(k) for k in ("table_path",)
                          if k in kwargs}
        return cls(BACKENDS[name](**backend_kwargs), **kwargs)

    # ── cache ────────────────────────────────────────────────────────────────
    def load(self):
        if self.cache_path.exists():
            with open(self.cache_path, "rb") as fh:
                self._pairs = pickle.load(fh)

    def save(self):
        if not self.backend.cacheable:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(self._pairs, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(self.cache_path)   # atomic, so an interrupted run cannot
        self._since_save = 0           # leave a half-written cache behind

    # ── lookup ───────────────────────────────────────────────────────────────
    def _fill(self, wanted):
        """Route every pair in `wanted` that is not already cached."""
        missing = [p for p in wanted if p not in self._pairs]
        self.hits += len(wanted) - len(missing)
        if not missing:
            return
        self.misses += len(missing)


        origins = np.array([[p[0], p[1]] for p in missing], dtype=float)
        destinations = np.array([[p[2], p[3]] for p in missing], dtype=float)
        metres, minutes = self.backend.route(origins, destinations)
        for pair, m, t in zip(missing, metres, minutes):
            self._pairs[pair] = (float(m), float(t))

        self._since_save += len(missing)
        if self.autosave_every and self._since_save >= self.autosave_every:
            self.save()

    def matrices(self, person_df):
        """
        Return (distance_m, time_min) as n x n float64 arrays indexed by activity
        id, ready to hand to set_travel_skim().

        Indexed by activity id rather than by location so that charge/no-charge
        twin rows, which share a location, need no special handling -- they simply
        carry identical rows and columns.
        """
        n = len(person_df)
        ids = person_df["id"].to_numpy(dtype=int)
        xs = person_df["x"].to_numpy(dtype=float)
        ys = person_df["y"].to_numpy(dtype=float)

        # Activity ids index the C arrays directly, so the matrix must be big
        # enough for the largest id, not merely for the row count.
        size = max(n, int(ids.max()) + 1) if n else 0
        distance_m = np.zeros((size, size), dtype=np.float64)
        time_min = np.zeros((size, size), dtype=np.float64)
        if n < 2:
            return distance_m, time_min

        # Every ordered pair of distinct rows, as two flat index arrays.
        i_idx, j_idx = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        off_diagonal = i_idx != j_idx
        i_flat = i_idx[off_diagonal]
        j_flat = j_idx[off_diagonal]

        origins = np.column_stack([xs[i_flat], ys[i_flat]])
        destinations = np.column_stack([xs[j_flat], ys[j_flat]])

        if self.backend.cacheable:
            metres, minutes = self._cached_route(origins, destinations)
        else:
            # A table-backed backend does its own lookups, and they are already
            # cheap; going through the pickle cache would only duplicate them.
            metres, minutes = self.backend.route(origins, destinations)
            self.hits += len(origins)

        distance_m[ids[i_flat], ids[j_flat]] = metres
        time_min[ids[i_flat], ids[j_flat]] = minutes
        return distance_m, time_min

    def _cached_route(self, origins, destinations):
        """Route a batch through the pickle pair cache, computing only misses."""
        keys = [(o[0], o[1], d[0], d[1]) for o, d in zip(origins, destinations)]
        self._fill(set(keys))
        metres = np.empty(len(keys))
        minutes = np.empty(len(keys))
        for k, key in enumerate(keys):
            metres[k], minutes[k] = self._pairs[key]
        return metres, minutes


# ── Calibration helper ────────────────────────────────────────────────────────

def calibrate_detour_factors(sample_dir=SAMPLE_DIR, verbose=True):
    """
    Recompute DETOUR_BANDS and SPEED_BANDS_KPH from the sample route matrices.

    Only 100 locations were ever routed (the notebook caps at MAX_ROWS), so these
    factors rest on 9,900 pairs drawn from one corner of the study area. Re-run
    this once a fuller matrix exists, and prefer the osmnx backend outright once
    routing covers the population.
    """
    import re

    dist = pd.read_csv(sample_dir / "distance_matrix_route_m.csv", index_col=0)
    time = pd.read_csv(sample_dir / "time_matrix_route_min.csv", index_col=0)

    pattern = re.compile(r"^(\d+):(.+) \((-?\d+),(-?\d+)\)$")
    coords = np.array([[float(m.group(3)), float(m.group(4))]
                       for m in (pattern.match(s) for s in dist.index)])

    straight = np.hypot(coords[:, None, 0] - coords[None, :, 0],
                        coords[:, None, 1] - coords[None, :, 1])
    off = ~np.eye(len(coords), dtype=bool)

    route_m = dist.to_numpy()[off]
    route_min = time.to_numpy()[off]
    straight_m = straight[off]

    ok = straight_m > 0
    detour, speed = [], []
    for lo, hi, _ in DETOUR_BANDS:
        sel = ok & (straight_m >= lo) & (straight_m < hi)
        if not sel.any():
            detour.append((lo, hi, 1.0))
            speed.append((lo, hi, 30.0))
            continue
        f = float(np.median(route_m[sel] / straight_m[sel]))
        good = sel & (route_min > 0)
        kph = float(np.median((route_m[good] / 1000.0) / (route_min[good] / 60.0)))
        detour.append((lo, hi, round(f, 3)))
        speed.append((lo, hi, round(kph, 1)))
        if verbose:
            label = f"{lo/1000:.0f}-{hi/1000:.0f} km" if hi < math.inf else f">{lo/1000:.0f} km"
            print(f"  {label:>12}: n={sel.sum():>5}  detour x{f:.3f}  speed {kph:.1f} km/h")
    return detour, speed


if __name__ == "__main__":
    print("Recalibrating detour and speed bands from the sample matrices:")
    detour, speed = calibrate_detour_factors()
    print("\nDETOUR_BANDS = [")
    for lo, hi, v in detour:
        print(f"    ({lo:.1f}, {'math.inf' if hi == math.inf else f'{hi:.1f}'}, {v}),")
    print("]")
    print("\nSPEED_BANDS_KPH = [")
    for lo, hi, v in speed:
        print(f"    ({lo:.1f}, {'math.inf' if hi == math.inf else f'{hi:.1f}'}, {v}),")
    print("]")
