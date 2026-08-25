"""
Run the DP for ONE person and show the input activities next to the output schedule.

This is the small-experiment tool: it uses exactly the same machinery as
run_population.py (same per-person seed, same initial-SoC mixture, same base_id
error-term sharing), so what you see here is what a population run would produce
for that person — no separate code path to drift out of sync.

Examples:
  # list some candidate persons and pick one
  python3 testing_latest/inspect_person.py --list --min-acts 8

  # inspect a specific person
  python3 testing_latest/inspect_person.py --pid 2002000535_3_E02001543_1_2002001259

  # first person with >=10 activities who has a home charger and a real trip
  python3 testing_latest/inspect_person.py --min-acts 10 --home-charger yes --moving

  # try a different scenario file / turn error terms off for a clean look
  python3 testing_latest/inspect_person.py --data path/to/scenario.parquet --sigma 0
"""

import argparse
import sys
from ctypes import CDLL, c_double, c_int
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from testing_check import (
    HORIZON, SPEED, TIME_INTERVAL, TRAVEL_TIME_PENALTY,
    compile_code, extract_schedule, initialize_utility,
)
from run_population import (
    DEFAULT_DATA, SocMixture, _make_activities_array, _setup_lib, person_seed,
)


def pick_pid(df, args):
    """Choose a pid matching the filters, or verify the one that was asked for."""
    counts = df.groupby("pid").size()

    if args.pid:
        if args.pid not in counts.index:
            sys.exit(f"pid not found in {args.data.name}: {args.pid}")
        return args.pid

    cand = counts[counts >= args.min_acts].index

    if args.home_charger != "any" and "has_home_charger" in df.columns:
        want = 1 if args.home_charger == "yes" else 0
        hc = df.groupby("pid")["has_home_charger"].first()
        cand = [p for p in cand if hc.get(p, 0) == want]

    if args.moving:
        # Persons whose activities are all at one location produce a flat, dull
        # schedule (no travel, no SoC movement) — usually not what you want to look at.
        span = df.groupby("pid")[["x", "y"]].nunique().sum(axis=1)
        cand = [p for p in cand if span.get(p, 0) > 2]

    cand = list(cand)
    if not cand:
        sys.exit("No person matched those filters — try relaxing --min-acts.")
    return cand[args.index] if args.index < len(cand) else cand[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", help="Exact pid to inspect")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--index", type=int, default=0,
                    help="Which matching person to take (default: first)")
    ap.add_argument("--min-acts", type=int, default=1,
                    help="Only consider persons with at least this many activity rows")
    ap.add_argument("--home-charger", choices=["any", "yes", "no"], default="any")
    ap.add_argument("--moving", action="store_true",
                    help="Skip persons whose activities are all at one location")
    ap.add_argument("--list", action="store_true",
                    help="List matching persons and exit (no DP run)")
    ap.add_argument("--sigma", type=float, default=1.0,
                    help="Utility error std dev; 0 makes the run deterministic")
    ap.add_argument("--soc", type=float, default=None,
                    help="Override initial SoC instead of using the mixture")
    ap.add_argument("--home-soc-mean", type=float, default=0.95)
    ap.add_argument("--home-soc-std", type=float, default=0.05)
    ap.add_argument("--nohome-soc-mean", type=float, default=0.40)
    ap.add_argument("--nohome-soc-std", type=float, default=0.10)
    ap.add_argument("--out", type=Path, default=None, help="Write the schedule to CSV")
    args = ap.parse_args()

    if not args.data.exists():
        sys.exit(f"Prepared data not found: {args.data}\nRun prepare_sheffield_data.py first.")

    df_all = (pd.read_parquet(args.data) if args.data.suffix == ".parquet"
              else pd.read_csv(args.data, low_memory=False))

    if args.list:
        counts = df_all.groupby("pid").size()
        counts = counts[counts >= args.min_acts].sort_values(ascending=False)
        home_charger = (df_all.groupby("pid")["has_home_charger"].first()
              if "has_home_charger" in df_all.columns else None)
        print(f"{len(counts):,} persons with >= {args.min_acts} activities. First 25:")
        for pid, n in counts.head(25).items():
            flag = "" if home_charger is None else f"  home_charger={int(home_charger.get(pid, 0))}"
            print(f"  {pid}   acts={n}{flag}")
        return

    pid = pick_pid(df_all, args)
    df = df_all[df_all.pid == pid].reset_index(drop=True)

    has_home_charger = bool(df["has_home_charger"].iloc[0]) if "has_home_charger" in df.columns else None
    if args.soc is not None:
        soc = args.soc
        soc_src = "--soc override"
    else:
        mix = SocMixture(args.home_soc_mean, args.home_soc_std,
                         args.nohome_soc_mean, args.nohome_soc_std)
        soc = mix.draw(pid, bool(has_home_charger))
        soc_src = f"mixture (home_charger={has_home_charger})"

    lib = CDLL(compile_code())
    _setup_lib(lib)
    p = initialize_utility()
    lib.set_general_parameters(
        HORIZON, SPEED, TRAVEL_TIME_PENALTY, TIME_INTERVAL,
        *[(c_double * len(p[k]))(*p[k]) for k in ("asc", "early", "late", "long", "short")],
    )
    lib.set_utility_error_std_dev(c_double(args.sigma))
    lib.set_random_seed(c_int(person_seed(pid)))
    lib.set_fixed_initial_soc(c_double(soc))

    arr, n_acts = _make_activities_array(df)
    lib.set_activities(arr, n_acts)
    lib.main(0, None)
    best = lib.get_final_schedule()

    pd.set_option("display.width", 220, "display.max_columns", 60)

    print("=" * 100)
    print(f"PERSON {pid}")
    print(f"  data        : {args.data.name}")
    print(f"  activities  : {n_acts} rows  "
          f"({int((df.base_id != df.id).sum()) if 'base_id' in df else 0} no-charge twins)")
    print(f"  initial SoC : {soc:.2%}   [{soc_src}]")
    print(f"  sigma       : {args.sigma}   seed: {person_seed(pid)}")
    print("=" * 100)

    show = [c for c in ["id", "base_id", "act_type", "group", "earliest_start",
                        "latest_start", "min_duration", "max_duration",
                        "des_start_time", "des_duration", "charge_mode",
                        "is_charging", "is_service_station", "x", "y"]
            if c in df.columns]
    print("\nINPUT ACTIVITIES (the choice set the DP was given)")
    print(df[show].to_string(index=False))

    if not best:
        print("\nNo feasible solution for this person.")
        return

    sched = extract_schedule(best, arr, df).sort_values("start_time").reset_index(drop=True)
    print("\nOUTPUT SCHEDULE (what the DP chose)")
    print(sched.to_string(index=False))

    chosen = set(sched["act_id"])
    if "base_id" in df.columns:
        skipped = df[~df["id"].isin(chosen)]
        if len(skipped):
            print(f"\nNot chosen: {sorted(skipped['id'].tolist())}"
                  f"  (includes the twin of anything that was chosen)")

    print(f"\nFinal utility : {sched['utility'].iloc[-1]:.2f}")
    print(f"SoC {soc:.2%} → {sched['soc_end'].iloc[-1]:.2%}   "
          f"total charge cost {sched['charge_cost'].max():.2f}")
    lib.free_bucket()

    if args.out:
        sched.to_csv(args.out, index=False)
        print(f"\nWritten → {args.out}")


if __name__ == "__main__":
    main()
