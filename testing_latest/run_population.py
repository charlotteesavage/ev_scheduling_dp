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
import multiprocessing as mp
import random
import subprocess
import sys
import time
from ctypes import CDLL, POINTER, c_char, c_double, c_int
from pathlib import Path

import pandas as pd

# Make testing_check importable from any working directory
sys.path.insert(0, str(Path(__file__).parent))
from testing_check import (
    HORIZON, SPEED, TIME_INTERVAL, TRAVEL_TIME_PENALTY,
    Activity, Label,
    compile_code, extract_schedule, initialize_utility,
)

REPO_ROOT    = Path(__file__).parent.parent
DEFAULT_DATA = REPO_ROOT / "testing_latest" / "sheffield_activities_prepared.parquet"
RESULTS_DIR  = REPO_ROOT / "testing_latest" / "population_results"

# ── Worker-process global state ───────────────────────────────────────────────
# Each worker loads these once in worker_init(); they persist for the lifetime
# of the worker process so the .so is not reloaded per person.
_lib      = None
_pid_data = None   # dict: pid (str) → per-person DataFrame
_soc_mix  = None   # SocMixture: per-person initial SoC draw


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
    lib.free_bucket.restype      = None
    lib.set_random_seed.argtypes = [c_int]
    lib.set_random_seed.restype  = None
    lib.set_utility_error_std_dev.argtypes = [c_double]
    lib.set_utility_error_std_dev.restype  = None
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype  = None


def worker_init(lib_path: str, data_path: str, params: dict, soc_mix=None):
    """
    Called once per worker process by multiprocessing.Pool.
    Loads the shared library, sets constant parameters, and indexes the
    prepared activity data so per-person lookups are O(1).
    """
    global _lib, _pid_data, _soc_mix

    _soc_mix = soc_mix

    _lib = CDLL(lib_path)
    _setup_lib(_lib)

    # Pre-build ctypes arrays for utility parameters (same for all persons)
    c = {k: (c_double * len(v))(*v) for k, v in params.items()}
    _lib.set_general_parameters(
        HORIZON, SPEED, TRAVEL_TIME_PENALTY, TIME_INTERVAL,
        c["asc"], c["early"], c["late"], c["long"], c["short"],
    )
    _lib.set_utility_error_std_dev(c_double(1.0))

    # Load prepared data and index by pid for fast per-person lookup
    try:
        df = pd.read_parquet(data_path)
    except Exception:
        df = pd.read_csv(data_path, low_memory=False)

    _pid_data = {pid: grp.reset_index(drop=True)
                 for pid, grp in df.groupby("pid")}


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
        df = _pid_data[pid]
        arr, n_acts = _make_activities_array(df)

        _lib.set_activities(arr, n_acts)

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
    params    = initialize_utility()
    n_workers = args.workers or default_workers()

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
    print(f"Initial SoC: home charger N({soc_mix.home_mean}, {soc_mix.home_std}) | "
              f"no home charger N({soc_mix.nohome_mean}, {soc_mix.nohome_std}) "
              f"[clamped to 0-1]")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    schedules  = []
    log_rows   = []
    t_start    = time.time()

    # tqdm is optional — fall back to plain counting if not installed
    try:
        from tqdm import tqdm
        progress = tqdm(total=len(pids), unit="person")
    except ImportError:
        progress = None

    with mp.Pool(
        processes=n_workers,
        initializer=worker_init,
        initargs=(lib_path, str(data_path), params, soc_mix),
    ) as pool:
        for result in pool.imap_unordered(run_one_person, pids, chunksize=50):
            log_rows.append({
                "pid":            result["pid"],
                "success":        result["success"],
                "n_acts_in":      result.get("n_acts_in"),
                "n_acts_out":     result.get("n_acts_out"),
                "final_utility":  result.get("final_utility"),
                "final_soc":      result.get("final_soc"),
                "initial_soc":    result.get("initial_soc"),
                "has_home_charger": result.get("has_home_charger"),
                "time_ms":        result.get("time_ms"),
                "error":          result.get("error", ""),
            })
            if result["success"]:
                schedules.append(result["schedule"])
            if progress is not None:
                progress.update(1)

    if progress is not None:
        progress.close()

    elapsed  = time.time() - t_start
    n_ok     = sum(r["success"] for r in log_rows)
    print(f"\nCompleted: {n_ok:,}/{len(pids):,} succeeded "
          f"in {elapsed:.1f} s  ({elapsed / max(len(pids), 1) * 1e3:.1f} ms/person)")

    # ── Save schedules ────────────────────────────────────────────────────────
    if schedules:
        all_sched = pd.concat(schedules, ignore_index=True)
        try:
            sched_path = RESULTS_DIR / "schedules.parquet"
            all_sched.to_parquet(sched_path, index=False)
        except ImportError:
            sched_path = RESULTS_DIR / "schedules.csv"
            all_sched.to_csv(sched_path, index=False)
        print(f"Schedules  → {sched_path}")

    # ── Save run log ──────────────────────────────────────────────────────────
    log_df   = pd.DataFrame(log_rows)
    log_path = RESULTS_DIR / "run_log.csv"
    log_df.to_csv(log_path, index=False)
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
    if n_ok < len(pids):
        failures = log_df[~log_df["success"]]
        print(f"\nFailure reasons:\n{failures['error'].value_counts().to_string()}")


if __name__ == "__main__":
    main()
