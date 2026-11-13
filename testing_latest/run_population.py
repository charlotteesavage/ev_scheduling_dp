"""
Population-scale DP runner for the Sheffield EV charging model.

Usage:
  python3 run_population.py                      # all persons, all CPU cores
  python3 run_population.py --limit 100          # test run: first 100 persons
  python3 run_population.py --workers 4          # cap parallelism
  python3 run_population.py --data path/to/file  # override prepared data path

Prerequisites:
  1. Run prepare_sheffield_data.py to generate the prepared activities file.
  2. C library must be compilable (gcc + src/ present).

Output (in testing_latest/population_results/):
  schedules.parquet   – all optimal schedules, one row per activity visit
  run_log.csv         – per-person success/failure, timing, final SOC/utility
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import random
import subprocess
import sys
import time
from ctypes import CDLL, POINTER, c_char, c_double, c_int
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Make testing_check importable from any working directory
sys.path.insert(0, str(Path(__file__).parent))
from testing_check import (
    HORIZON, SPEED, TIME_INTERVAL, TRAVEL_TIME_PENALTY,
    Activity, Label,
    DEFAULT_PARAM_SET, PARAM_SETS,
    compile_code, extract_schedule, initialize_utility,
)

REPO_ROOT    = Path(__file__).parent.parent
DEFAULT_DATA = REPO_ROOT / "testing_latest" / "data" / "sheffield_activities_prepared.parquet"
RESULTS_DIR  = REPO_ROOT / "testing_latest" / "population_results"

# ── Worker-process global state ───────────────────────────────────────────────
# Each worker loads these once in worker_init(); they persist for the lifetime
# of the worker process so the .so is not reloaded per person.
_lib      = None
_pid_data  = None   # dict: pid (str) → (start_row, end_row) into _pid_frame
_pid_frame = None   # one pid-sorted DataFrame holding every person's activities
_soc_mix  = None   # SocMixture: per-person initial SoC draw
_skim     = None   # TravelSkim, or None for straight-line distance


def _setup_lib(lib):
    """Declare ctypes argtypes/restypes on a freshly loaded CDLL."""
    lib.set_general_parameters.argtypes = [
        c_int, c_double, c_double, c_int,
        POINTER(c_double), POINTER(c_double), POINTER(c_double),
        POINTER(c_double), POINTER(c_double),
    ]
    lib.set_activities.argtypes = [POINTER(Activity), c_int]
    lib.main.argtypes            = [c_int, POINTER(POINTER(c_char))]
    lib.main.restype             = c_int
    lib.get_final_schedule.restype = POINTER(Label)
    lib.get_count.restype        = c_int
    lib.free_bucket.restype      = None
    lib.set_random_seed.argtypes = [c_int]
    lib.set_random_seed.restype  = None
    lib.set_utility_error_std_dev.argtypes = [c_double]
    lib.set_utility_error_std_dev.restype  = None
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype  = None
    lib.set_travel_skim.argtypes = [POINTER(c_double), POINTER(c_double), c_int]
    lib.set_travel_skim.restype  = None
    lib.clear_travel_skim.argtypes = []
    lib.clear_travel_skim.restype  = None


def worker_init(lib_path: str, data_path: str, params: dict, soc_mix=None,
                skim_backend: str = None):
    """
    Called once per worker process by multiprocessing.Pool.
    Loads the shared library, sets constant parameters, and indexes the
    prepared activity data so per-person lookups are O(1).
    """
    global _lib, _pid_data, _pid_frame, _soc_mix, _skim

    _soc_mix = soc_mix

    # Each worker gets its own TravelSkim. They share the on-disk pair cache for
    # reads, but each keeps its own in-memory dict — workers are separate
    # processes, so there is nothing to synchronise and nothing to race on.
    # Autosave is off here: several processes writing one pickle would corrupt it.
    # main() warms the cache before forking, so misses in the workers are rare.
    _skim = None
    if skim_backend and skim_backend != "none":
        from travel_skim import TravelSkim
        _skim = TravelSkim.from_name(skim_backend, autosave_every=0)

    _lib = CDLL(lib_path)
    _setup_lib(_lib)

    # Pre-build ctypes arrays for utility parameters (same for all persons).
    # params also carries "param_set", the name of the set these numbers came from;
    # it is metadata for the run log, not a coefficient vector, so it is skipped here.
    c = {k: (c_double * len(v))(*v) for k, v in params.items()
         if k != "param_set"}
    _lib.set_general_parameters(
        HORIZON, SPEED, TRAVEL_TIME_PENALTY, TIME_INTERVAL,
        c["asc"], c["early"], c["late"], c["long"], c["short"],
    )
    _lib.set_utility_error_std_dev(c_double(1.0))

    # Load prepared data and index by pid for fast per-person lookup.
    #
    # This reads the COMPACT file main() builds, not the raw prepared data. Doing
    # the sort and the dtype narrowing here instead cost about 800 MB of resident
    # memory per worker: the intermediate copies (sort, then one copy per column
    # cast) push the heap up to that mark, and the allocator does not hand the
    # space back once the frame shrinks to its real 66 MB. Reading a file that is
    # already sorted and already narrow never creates those copies, so a worker
    # settles at roughly 240 MB instead. With several workers that difference is
    # the difference between a run that fits in memory and one that does not.
    table = pq.read_table(data_path)

    # Row offsets per person. The pid column is dictionary-encoded in the compact
    # file, so the run boundaries can be found from the integer codes -- turning
    # all 950k pids into Python strings just to group them would itself cost more
    # than the frame does.
    pid_col = table.column("pid").combine_chunks()
    if pa.types.is_dictionary(pid_col.type):
        codes = pid_col.indices.to_numpy(zero_copy_only=False)
        values = pid_col.dictionary.to_pylist()
        starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
        ends = np.r_[starts[1:], len(codes)]
        pid_at_start = [values[c] for c in codes[starts]]
    else:
        pid_values = pid_col.to_numpy(zero_copy_only=False)
        starts = np.flatnonzero(np.r_[True, pid_values[1:] != pid_values[:-1]])
        ends = np.r_[starts[1:], len(pid_values)]
        pid_at_start = list(pid_values[starts])
    # A person's rows have to be contiguous for a row range to describe them. That
    # holds by construction for the compact file (it is written sorted by pid), but
    # if some other file were passed here a split person would silently lose every
    # block of activities but the last — a wrong schedule, not an error.
    if len(set(pid_at_start)) != len(pid_at_start):
        raise ValueError(
            f"{data_path} is not grouped by pid: some person's rows are split "
            "across the file. Pass the prepared data file and let main() build "
            "the compact copy, rather than pointing workers at a file directly.")
    _pid_data = dict(zip(pid_at_start, zip(starts, ends)))
    del pid_col

    # pid itself is not needed downstream -- run_one_person addresses people by row
    # offset -- and as plain strings it would be the single largest column.
    table = table.drop_columns(["pid"])

    # self_destruct releases each Arrow column as soon as pandas has taken it over,
    # so the two representations are never both fully resident.
    _pid_frame = table.to_pandas(split_blocks=True, self_destruct=True)
    del table


# ── Compact dataset ───────────────────────────────────────────────────────────
# Every worker loads the whole activity table, so its size is multiplied by the
# worker count. Preparing it once, on disk, in the shape the workers actually want
# means they can load it without building any intermediate copies.

COMPACT_SUFFIX = ".compact.parquet"


def compact_path_for(data_path: Path) -> Path:
    """Where the compact copy of `data_path` lives (beside the original)."""
    return data_path.with_name(data_path.stem + COMPACT_SUFFIX)


def _write_compact(src: str, dst: str):
    """
    Write a sorted, narrowed copy of the prepared data.

    Three changes, all of which save the workers memory rather than disk:
      - sorted by pid, so a worker can index people by row range instead of
        building a dict of 127k small frames
      - int64 -> int32 for every integer column (ids, 5-minute intervals and 0/1
        flags; none of them need 64 bits)
      - strings dictionary-encoded, which is what makes pid cheap to group on and
        act_type cost ~1 MB instead of ~60 MB

    Run in its own process by build_compact(): the sort needs about 600 MB, and
    doing it here means that memory goes back to the operating system when the
    process exits rather than sitting in the parent for the rest of the run.
    """
    src, dst = Path(src), Path(dst)
    if src.suffix == ".parquet":
        table = pq.read_table(src)
    else:
        table = pa.Table.from_pandas(
            pd.read_csv(src, low_memory=False), preserve_index=False)

    table = table.sort_by("pid")

    columns, names = [], []
    for field in table.schema:
        col = table.column(field.name)
        if pa.types.is_int64(field.type) or pa.types.is_uint64(field.type):
            col = col.cast(pa.int32())
        elif pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
            col = col.dictionary_encode()
        columns.append(col)
        names.append(field.name)

    tmp = dst.with_suffix(".tmp")
    pq.write_table(pa.table(columns, names=names), tmp, compression="zstd")
    tmp.replace(dst)   # atomic: an interrupted build cannot leave a half-written
                       # file that the next run would happily load


def build_compact(data_path: Path) -> Path:
    """
    Return the compact copy of `data_path`, building it if it is missing or stale.

    Staleness is by modification time, so re-running prepare_sheffield_data.py
    automatically invalidates it and no one has to remember to delete anything.
    """
    dst = compact_path_for(data_path)
    if dst.exists() and dst.stat().st_mtime >= data_path.stat().st_mtime:
        return dst

    print(f"Building compact copy of {data_path.name} (one-off, ~30 s)...")
    t0 = time.time()
    proc = mp.Process(target=_write_compact, args=(str(data_path), str(dst)))
    proc.start()
    proc.join()
    if proc.exitcode != 0 or not dst.exists():
        raise SystemExit(
            f"Failed to build {dst.name} (exit code {proc.exitcode}).")
    print(f"        {dst.name}  {dst.stat().st_size / 1e6:.0f} MB  "
          f"in {time.time() - t0:.1f}s")
    return dst


# ── Run manifest ──────────────────────────────────────────────────────────────

def write_manifest(out_dir: Path, suffix: str, args, params, data_path: Path,
                   n_persons: int, n_workers: int):
    """
    Record what produced a set of results, beside the results themselves.

    A schedules file on its own says nothing about which utility parameters, which
    travel backend or which initial-SoC mixture made it, and those are exactly the
    things that move the headline numbers -- switching the parameter set alone took
    charging participation from 2.6% to 8.1% on an otherwise identical population.
    Written before the DP starts, so it exists even for a run that is later killed.

    Includes the git commit and whether the tree was dirty. A dirty tree means the
    commit does not fully describe the code that ran, which is worth knowing when a
    result is surprising months later.
    """
    def git(*cmd):
        try:
            return subprocess.run(["git", *cmd], cwd=Path(__file__).parent,
                                  capture_output=True, text=True, timeout=5,
                                  check=True).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None

    manifest = {
        "written_at":      datetime.now().astimezone().isoformat(timespec="seconds"),
        "tag":             args.tag,
        "param_set":       params["param_set"],
        "param_set_is_default": params["param_set"] == DEFAULT_PARAM_SET,
        "utility_parameters": {k: v for k, v in params.items() if k != "param_set"},
        "data_file":       str(data_path),
        "travel_backend":  args.travel,
        "persons":         n_persons,
        "workers":         n_workers,
        "initial_soc": {
            "home_mean": args.home_soc_mean, "home_std": args.home_soc_std,
            "nohome_mean": args.nohome_soc_mean, "nohome_std": args.nohome_soc_std,
        },
        "limit":           args.limit,
        "resume":          args.resume,
        "git_commit":      git("rev-parse", "HEAD"),
        "git_dirty":       bool(git("status", "--porcelain")),
        "command":         " ".join(sys.argv),
    }
    path = out_dir / f"manifest{suffix}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


# ── Per-person DP run ─────────────────────────────────────────────────────────

def default_workers() -> int:
    """
    Sensible default worker count.

    Not cpu_count(): on hybrid CPUs (Apple silicon P+E cores) filling every logical
    core is measurably *slower* than using only the performance cores, because the
    DP is CPU-bound and the slow cores hold up each chunk. Measured on this repo,
    1000 persons: 4 workers = 40.6 ms/person, 8 workers = 59.4 ms/person.
    Falls back to cpu_count() wherever the performance-core count is unavailable.
    """
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            n = int(out.stdout.strip())
            if n > 0:
                return n
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    return mp.cpu_count()


def person_seed(pid, salt: int = 0) -> int:
    """
    Stable per-person RNG seed in the c_int range.

    Pure function of (pid, salt), unlike hash(), so a person draws the same initial
    SoC and the same utility errors in every run. Pass a different salt to draw a
    fresh replication of the whole population while keeping it reproducible.
    """
    digest = hashlib.blake2b(
        f"{salt}|{pid}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF


# ── Initial state of charge ───────────────────────────────────────────────────
# Overnight charging is represented as an initial condition rather than a forced
# dusk activity (dusk charging was removed — see add_dawn_dusk in
# prepare_sheffield_data.py). Someone with a home charger starts the day near full;
# everyone else starts on whatever they had left. That makes home-charger access
# matter at both ends of the day, so the --home-charging-share lever moves both how
# much people can top up *and* what they wake up with.

SOC_SALT = 1  # keeps the SoC stream independent of the C-side error-term seed


class SocMixture:
    """Per-person initial SoC, conditioned on home-charger access."""

    def __init__(self, home_mean, home_std, nohome_mean, nohome_std):
        self.home_mean = home_mean
        self.home_std = home_std
        self.nohome_mean = nohome_mean
        self.nohome_std = nohome_std

    def draw(self, pid, has_home_charger: bool) -> float:
        """
        Reproducible draw in [0, 1]. Clamping (rather than resampling) is deliberate:
        it piles a little mass at exactly 1.0 for home chargers, which is the right
        shape — plenty of people do charge to full overnight — and it keeps every
        draw inside the [0, 1] range the C code assumes.
        """
        mean, std = ((self.home_mean, self.home_std) if has_home_charger
                     else (self.nohome_mean, self.nohome_std))
        rng = random.Random(person_seed(pid, salt=SOC_SALT))
        return min(1.0, max(0.0, rng.gauss(mean, std)))


def _make_activities_array(df: pd.DataFrame):
    """Build a ctypes Activity array from a person's prepared DataFrame."""
    n   = len(df)
    arr = (Activity * n)()
    for _, row in df.iterrows():
        aid = int(row["id"])
        arr[aid].id               = aid
        arr[aid].x                = float(row["x"])
        arr[aid].y                = float(row["y"])
        arr[aid].group            = int(row["group"]) - 1   # CSV 1-indexed → C 0-indexed
        arr[aid].earliest_start   = int(row["earliest_start"])
        arr[aid].latest_start     = int(row["latest_start"])
        arr[aid].min_duration     = int(row["min_duration"])
        arr[aid].max_duration     = int(row["max_duration"])
        arr[aid].des_start_time   = int(row["des_start_time"])   if pd.notna(row["des_start_time"])   else 0
        arr[aid].des_duration     = int(row["des_duration"])     if pd.notna(row["des_duration"])     else 0
        arr[aid].charge_mode      = int(row["charge_mode"])      if pd.notna(row["charge_mode"])      else 0
        arr[aid].is_charging      = int(row["is_charging"])      if pd.notna(row["is_charging"])      else 0
        arr[aid].is_service_station = int(row["is_service_station"]) if pd.notna(row["is_service_station"]) else 0
        # Charge/no-charge twins share a base_id so they share their activity-level
        # error draws (see Activity.base_id in include/scheduling.h).
        arr[aid].base_id          = int(row["base_id"]) if "base_id" in row.index and pd.notna(row["base_id"]) else aid
        arr[aid].memory           = None
    return arr, n


def run_one_person(pid: str) -> dict:
    """
    Run the DP for a single person.  Called inside a worker process.
    Returns a result dict; schedule DataFrame is included on success.
    """
    t0 = time.perf_counter()
    try:
        lo, hi = _pid_data[pid]
        df = _pid_frame.iloc[lo:hi].reset_index(drop=True)
        arr, n_acts = _make_activities_array(df)

        _lib.set_activities(arr, n_acts)

        # Travel skim for this person. Like set_fixed_initial_soc(), this is
        # sticky inside a worker process, so it must be set (or cleared) for
        # EVERY person or the previous person's matrix silently leaks into the
        # next one — and the two people have unrelated locations, so the result
        # would be wrong without being obviously wrong.
        routed = approximated = 0
        if _skim is not None:
            before_hits = getattr(_skim.backend, "hits", 0)
            before_misses = getattr(_skim.backend, "misses", 0)
            dist_m, time_min = _skim.matrices(df)
            routed = getattr(_skim.backend, "hits", 0) - before_hits
            approximated = getattr(_skim.backend, "misses", 0) - before_misses
            _lib.set_travel_skim(
                dist_m.ctypes.data_as(POINTER(c_double)),
                time_min.ctypes.data_as(POINTER(c_double)),
                c_int(dist_m.shape[0]),
            )
        else:
            _lib.clear_travel_skim()

        # Deterministic seed derived from pid so results are reproducible.
        # Must NOT use the built-in hash(): Python salts string hashing per process
        # (PYTHONHASHSEED), so it returns a different value on every run. That would
        # give each person a fresh initial SoC and fresh utility errors each time,
        # and two scenario runs would differ by noise as well as by the intervention.
        seed = person_seed(pid)
        _lib.set_random_seed(c_int(seed))

        # Initial SoC. set_fixed_initial_soc() is sticky — once set it stays on for
        # the whole worker process — so it must be set for EVERY person, or one
        # person's value silently leaks into the next. main() has already verified
        # the has_home_charger column exists, so this is unconditional.
        has_hc = bool(df["has_home_charger"].iloc[0])
        initial_soc = _soc_mix.draw(pid, has_hc)
        _lib.set_fixed_initial_soc(c_double(initial_soc))

        _lib.main(0, None)
        # How many DSSR refinement rounds this person needed. main.c caps this at
        # MAX_DSSR_ITERATIONS (200); a person coming back at the cap means the loop
        # gave up and the schedule may still repeat an activity group.
        dssr_rounds = int(_lib.get_count())
        best = _lib.get_final_schedule()

        if not best:
            _lib.free_bucket()
            return {"pid": pid, "success": False, "error": "no_solution",
                    "time_ms": (time.perf_counter() - t0) * 1e3}

        schedule = extract_schedule(best, arr, df)
        schedule.insert(0, "pid", pid)
        _lib.free_bucket()

        return {
            "pid":            pid,
            "success":        True,
            "schedule":       schedule,
            "n_acts_in":      n_acts,
            "n_acts_out":     len(schedule),
            "final_utility":  float(schedule["utility"].iloc[-1]),
            "final_soc":      float(schedule["soc_end"].iloc[-1]),
            "initial_soc":    initial_soc,
            "has_home_charger": has_hc,
            "legs_routed":    routed,
            "legs_approx":    approximated,
            "dssr_rounds":    dssr_rounds,
            "time_ms":        (time.perf_counter() - t0) * 1e3,
        }
    except Exception as exc:
        return {"pid": pid, "success": False, "error": str(exc),
                "time_ms": (time.perf_counter() - t0) * 1e3}


# ── Main ─────────────────────────────────────────────────────────────────────

def _column_names(path: Path) -> list:
    """Column names for a parquet/CSV file, without reading the data itself."""
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq
        return list(pq.read_schema(path).names)
    return list(pd.read_csv(path, nrows=0).columns)


def main():
    parser = argparse.ArgumentParser(description="Population-scale EV scheduling DP")
    # Both of these default to None so the documented behaviour ("all persons, all
    # CPU cores") is what actually happens. They previously defaulted to 4 and 50,
    # which made `--workers`' own `or mp.cpu_count()` fallback dead code and meant a
    # bare `python run_population.py` quietly ran 50 people and looked like a full
    # population run.
    parser.add_argument("--workers", type=int, default=None,
                        help="Worker processes (default: performance-core count, "
                             "which beats using every logical core on hybrid CPUs)")
    parser.add_argument("--limit",   type=int, default=None,
                        help="Max persons to run — useful for quick tests "
                             "(default: all persons)")
    parser.add_argument("--data",    type=Path, default=DEFAULT_DATA,
                        help="Path to prepared activities file (.parquet or .csv)")
    # Output naming. Without this every scenario writes schedules.parquet and
    # run_log.csv into the same directory, so a sweep silently overwrites itself
    # and only the last scenario survives.
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR, metavar="DIR",
                        help="Directory for schedules and run log "
                             "(default: %(default)s)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip persons already present in this tag's part files. "
                             "Lets a run that died partway (crash, power loss) pick up "
                             "where it stopped instead of starting over.")
    parser.add_argument("--flush-every", type=int, default=10000, metavar="N",
                        help="Write completed schedules to a part file every N persons "
                             "(default: %(default)s). Bounds both peak memory and how "
                             "much work a crash can destroy.")
    parser.add_argument("--tag", type=str, default=None, metavar="NAME",
                        help="Suffix for output filenames, e.g. --tag hc10 writes "
                             "schedules_hc10.parquet and run_log_hc10.csv. Give each "
                             "scenario its own tag or they overwrite each other.")
    # Initial-SoC mixture. Overnight charging is an initial condition here, not an
    # activity, so home-charger owners wake up near full and everyone else does not.
    parser.add_argument("--home-soc-mean", type=float, default=0.95, metavar="M",
                        help="Mean initial SoC for persons WITH a home charger "
                             "(default: %(default)s)")
    parser.add_argument("--home-soc-std", type=float, default=0.05, metavar="S",
                        help="Std dev of initial SoC with a home charger "
                             "(default: %(default)s)")
    parser.add_argument("--nohome-soc-mean", type=float, default=0.40, metavar="M",
                        help="Mean initial SoC for persons WITHOUT a home charger "
                             "(default: %(default)s)")
    parser.add_argument("--nohome-soc-std", type=float, default=0.10, metavar="S",
                        help="Std dev of initial SoC without a home charger "
                             "(default: %(default)s)")
    # Utility parameters. Defaults to DEFAULT_PARAM_SET in testing_check.py, which
    # is oasis_t12. Anything else has to be typed here and is recorded in the run
    # log and the manifest, so no set of results is ever ambiguous about which
    # coefficients produced it.
    parser.add_argument("--params", choices=list(PARAM_SETS), default=DEFAULT_PARAM_SET,
                        help="Utility parameter set (default: %(default)s). Only "
                             "change this for a deliberate sensitivity comparison.")
    parser.add_argument("--travel", choices=["none", "euclidean", "detour", "route"],
                        default="none", metavar="BACKEND",
                        help="Travel distance/time source (default: %(default)s). "
                             "'route' is the real one: road distance and road time "
                             "looked up in the table built by build_road_graph.py and "
                             "build_route_table.py. 'none' and 'euclidean' both give "
                             "straight-line distance at a flat speed — 'none' does it "
                             "inside the C, 'euclidean' routes it through the skim so "
                             "the plumbing can be checked. 'detour' scales "
                             "straight-line distance by a measured, distance-banded "
                             "factor, and is what 'route' falls back to for pairs "
                             "outside the road graph.")
    args = parser.parse_args()

    # Resolve .parquet/.csv if stem was given
    data_path = args.data
    if not data_path.exists():
        csv_alt = data_path.with_suffix(".csv")
        if csv_alt.exists():
            data_path = csv_alt
        else:
            sys.exit(f"Prepared data not found: {data_path}\n"
                     "Run prepare_sheffield_data.py first.")

    # Initial SoC is drawn conditional on home-charger access, so a missing
    # column would silently simulate a different population. Check once here,
    # before any worker starts — a check inside run_one_person() would be
    # swallowed by its `except Exception` and reported as a per-person failure.
    if "has_home_charger" not in _column_names(data_path):
        raise ValueError(
            f"{data_path.name} has no 'has_home_charger' column. Initial SoC is "
            "drawn conditional on home-charger access, so this run would "
            "silently simulate a different population. "
            "Re-run prepare_sheffield_data.py to add it."
        )

    lib_path  = compile_code()
    params    = initialize_utility(args.params)
    n_workers = args.workers or default_workers()

    # Workers load this, not data_path — see build_compact() for why.
    compact_path = build_compact(data_path)

    # Read just the pid column to get the full list cheaply
    try:
        pid_series = pd.read_parquet(data_path, columns=["pid"])["pid"]
    except Exception:
        pid_series = pd.read_csv(data_path, usecols=["pid"], low_memory=False)["pid"]

    pids = pid_series.unique().tolist()
    if args.limit:
        pids = pids[: args.limit]

    soc_mix = SocMixture(
        args.home_soc_mean, args.home_soc_std,
        args.nohome_soc_mean, args.nohome_soc_std,
    )

    print(f"Persons: {len(pids):,}  |  Workers: {n_workers}  |  Data: {data_path.name}")
    print(f"Utility parameters: {params['param_set']}"
          + ("" if params["param_set"] == DEFAULT_PARAM_SET
             else "   ** NOT the default — this is a sensitivity run **"))
    print(f"Initial SoC: home charger N({soc_mix.home_mean}, {soc_mix.home_std}) | "
              f"no home charger N({soc_mix.nohome_mean}, {soc_mix.nohome_std}) "
              f"[clamped to 0-1]")

    # Warm the travel-skim cache in THIS process, before forking. Workers get a
    # copy of it for free and can then run almost entirely on cache hits. Doing it
    # after the fork would mean every worker routing the same shared legs
    # separately, and only one of them could safely write the cache back.
    if args.travel == "route":
        # Nothing to warm: build_route_table.py already did the routing, and the
        # workers each memory-map the same table.
        from travel_skim import TravelSkim, ROUTE_TABLE
        if not ROUTE_TABLE.exists():
            raise SystemExit(
                f"--travel route needs {ROUTE_TABLE}, which does not exist.\n"
                "Build it with:\n"
                "    python3 testing_latest/build_road_graph.py\n"
                "    python3 testing_latest/build_route_table.py")
        print("Travel: real road distance and time from the route table")
    elif args.travel != "none":
        from travel_skim import TravelSkim
        warm = TravelSkim.from_name(args.travel)
        print(f"Travel: {args.travel} backend, warming pair cache for {len(pids):,} persons...")
        t_warm = time.time()
        # Only the columns matrices() needs — the full frame is large and the
        # workers load it themselves anyway.
        cols = ["pid", "id", "x", "y"]
        if data_path.suffix == ".parquet":
            df_for_warm = pd.read_parquet(data_path, columns=cols)
        else:
            df_for_warm = pd.read_csv(data_path, usecols=cols, low_memory=False)
        subset = df_for_warm[df_for_warm["pid"].isin(set(pids))]
        for _, person in subset.groupby("pid"):
            warm.matrices(person.reset_index(drop=True))
        warm.save()
        print(f"        {warm.hits + warm.misses:,} pair lookups "
              f"({warm.misses:,} routed, {warm.hits:,} cached) in {time.time() - t_warm:.1f}s")
    else:
        print("Travel: straight-line distance at a flat speed (no skim)")

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.tag}" if args.tag else ""

    # Part files. Writing incrementally means a crash costs at most --flush-every
    # persons instead of the whole run, and the parent never holds all 127k
    # schedules at once (that alone was ~0.85 GB at the end of a full run).
    parts_dir = out_dir / f"parts{suffix}"
    parts_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = write_manifest(out_dir, suffix, args, params, data_path,
                                   len(pids), n_workers)
    print(f"Manifest   → {manifest_path}")

    done_pids = set()
    if args.resume:
        for f in sorted(parts_dir.glob("schedules_*.parquet")):
            try:
                done_pids.update(pd.read_parquet(f, columns=["pid"])["pid"].unique())
            except Exception as exc:
                print(f"  ignoring unreadable part {f.name}: {exc}")
        if done_pids:
            before = len(pids)
            pids = [p for p in pids if p not in done_pids]
            print(f"Resuming: {len(done_pids):,} persons already done, "
                  f"{len(pids):,} of {before:,} left to run")
        else:
            print("Resuming: no completed parts found, starting from the beginning")

    schedules  = []
    log_rows   = []
    # Counted as results arrive, not from log_rows: flush() drains that list, so
    # summing it at the end reported 0 succeeded on any run that flushed.
    n_success  = 0
    part_no    = len(list(parts_dir.glob("schedules_*.parquet")))
    t_start    = time.time()

    log_path = out_dir / f"run_log{suffix}.csv"
    log_written = args.resume and log_path.exists()

    def flush(force=False):
        """
        Write buffered schedules to a part file and append the matching log rows.

        The log is appended here rather than only at the end because a crash used to
        take every per-person diagnostic with it (timing, initial SoC, DSSR rounds)
        even though the schedules themselves had already been parted out safely.
        """
        nonlocal schedules, part_no, log_rows, log_written
        if not schedules or (not force and len(schedules) < args.flush_every):
            return
        pd.concat(schedules, ignore_index=True).to_parquet(
            parts_dir / f"schedules_{part_no:04d}.parquet", index=False)
        part_no += 1
        schedules = []
        if log_rows:
            pd.DataFrame(log_rows).to_csv(
                log_path, mode="a" if log_written else "w",
                header=not log_written, index=False)
            log_written = True
            log_rows = []

    # tqdm is optional — fall back to plain counting if not installed
    try:
        from tqdm import tqdm
        progress = tqdm(total=len(pids), unit="person")
    except ImportError:
        progress = None

    with mp.Pool(
        processes=n_workers,
        initializer=worker_init,
        initargs=(lib_path, str(compact_path), params, soc_mix, args.travel),
    ) as pool:
        for result in pool.imap_unordered(run_one_person, pids, chunksize=50):
            log_rows.append({
                "pid":            result["pid"],
                # Stamped per row so a log can never be separated from the
                # parameters that produced it, however it is later filtered,
                # concatenated or resumed.
                "param_set":      params["param_set"],
                "success":        result["success"],
                "n_acts_in":      result.get("n_acts_in"),
                "n_acts_out":     result.get("n_acts_out"),
                "final_utility":  result.get("final_utility"),
                "final_soc":      result.get("final_soc"),
                "initial_soc":    result.get("initial_soc"),
                "has_home_charger": result.get("has_home_charger"),
                "legs_routed":    result.get("legs_routed"),
                "legs_approx":    result.get("legs_approx"),
                "dssr_rounds":    result.get("dssr_rounds"),
                "time_ms":        result.get("time_ms"),
                "error":          result.get("error", ""),
            })
            if result["success"]:
                n_success += 1
                schedules.append(result["schedule"])
                flush()
            if progress is not None:
                progress.update(1)

    if progress is not None:
        progress.close()

    elapsed  = time.time() - t_start
    n_ok     = n_success
    print(f"\nCompleted: {n_ok:,}/{len(pids):,} succeeded "
          f"in {elapsed:.1f} s  ({elapsed / max(len(pids), 1) * 1e3:.1f} ms/person)")

    # ── Save schedules ────────────────────────────────────────────────────────
    flush(force=True)
    part_files = sorted(parts_dir.glob("schedules_*.parquet"))
    if part_files:
        sched_path = out_dir / f"schedules{suffix}.parquet"
        # Streamed part by part rather than pd.concat'd. The concat held every
        # schedule in the population in memory at once (about 0.9 GB for a full
        # run) purely to write them straight back out again — and it did it at the
        # very end, after hours of work, where running out of memory costs the most.
        writer = None
        try:
            for f in part_files:
                part = pq.read_table(f)
                if writer is None:
                    writer = pq.ParquetWriter(sched_path, part.schema)
                writer.write_table(part)
                del part
        finally:
            if writer is not None:
                writer.close()
        print(f"Schedules  → {sched_path}  ({len(part_files)} part files kept in "
              f"{parts_dir.name}/ — delete once you have the combined file)")

    # ── Save run log ──────────────────────────────────────────────────────────
    log_df = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame(log_rows)
    print(f"Run log    → {log_path}")

    # Brief summary statistics
    if n_ok > 0:
        ok = log_df[log_df["success"]]
        print(f"\nMedian time per person : {ok['time_ms'].median():.1f} ms")
        print(f"Mean final SOC         : {ok['final_soc'].mean():.2%}")
        print(f"Mean final utility     : {ok['final_utility'].mean():.2f}")
        if ok["initial_soc"].notna().any():
            print(f"Mean initial SOC       : {ok['initial_soc'].mean():.2%}")
            if ok["has_home_charger"].notna().any():
                by = ok.groupby("has_home_charger")[["initial_soc", "final_soc"]].mean()
                print("Mean SOC by home charger access:")
                print(by.to_string(float_format=lambda v: f"{v:.2%}"))
        if args.travel != "none" and ok["legs_routed"].notna().any():
            routed = float(ok["legs_routed"].sum())
            approx = float(ok["legs_approx"].sum())
            total = routed + approx
            if total > 0:
                print(f"\nTravel legs            : {total:,.0f} "
                      f"({routed / total:.1%} from the {args.travel} source, "
                      f"{approx / total:.1%} approximated)")

    if n_ok > 0 and log_df["dssr_rounds"].notna().any():
        hit = int((log_df["dssr_rounds"] >= 200).sum())
        print(f"Max DSSR rounds        : {int(log_df['dssr_rounds'].max())}"
              + (f"   ** {hit:,} persons hit the cap **" if hit else "   (cap never bound)"))

    if n_ok < len(pids):
        failures = log_df[~log_df["success"]]
        print(f"\nFailure reasons:\n{failures['error'].value_counts().to_string()}")


if __name__ == "__main__":
    main()
