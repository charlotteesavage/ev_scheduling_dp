#!/usr/bin/env python3
"""
Compare finished population runs side by side.

Usage:
  python3 testing_latest/compare_scenarios.py hc1 hc5 hc10
  python3 testing_latest/compare_scenarios.py --dir some/other/dir hc1 hc5

Reads schedules_<tag>.parquet and run_log_<tag>.csv from the results directory
and prints one row per scenario. Charging figures are per person per day.
"""
import argparse
from pathlib import Path
import pandas as pd

REPO = Path(__file__).parent.parent
DEFAULT_DIR = REPO / "testing_latest" / "population_results" / "sweep"
BATTERY_KWH = 60.0   # matches battery_capacity in src/scheduling.c

MODE_NAMES = {1: "public slow 7kW", 2: "fast 22kW", 3: "rapid 50kW",
              4: "free slow", 5: "free fast", 6: "free rapid",
              7: "home slow 7kW"}


def load(results_dir: Path, tag: str):
    sched = results_dir / f"schedules_{tag}.parquet"
    log = results_dir / f"run_log_{tag}.csv"
    if not sched.exists():
        raise SystemExit(f"missing {sched} -- has that scenario been run?")
    return pd.read_parquet(sched), (pd.read_csv(log) if log.exists() else None)


def summarise(tag, sched, log):
    persons = sched["pid"].nunique()
    charging = sched[sched["is_charging"] == 1]
    kwh = ((charging["soc_end"] - charging["soc_start"]) * BATTERY_KWH)
    nonhome = sched[sched["act_type"] != "home"]
    row = {
        "scenario": tag,
        "persons": persons,
        "% charging": 100 * charging["pid"].nunique() / persons,
        "events/1000 ppl": 1000 * len(charging) / persons,
        "MWh/day": kwh.sum() / 1000,
        "kWh/event": kwh.mean() if len(charging) else 0.0,
        "acts/person": len(nonhome) / persons,
    }
    if log is not None and "final_soc" in log:
        row["mean final SoC %"] = 100 * log["final_soc"].mean()
    return row, charging


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tags", nargs="+", help="scenario tags, e.g. hc1 hc5 hc10")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                    help="results directory (default: %(default)s)")
    args = ap.parse_args()

    rows, by_mode = [], {}
    for tag in args.tags:
        sched, log = load(args.dir, tag)
        row, charging = summarise(tag, sched, log)
        rows.append(row)
        by_mode[tag] = (charging["charge_mode"].map(MODE_NAMES)
                        .value_counts().rename(tag))

    df = pd.DataFrame(rows).set_index("scenario")
    print("\n=== Scenario comparison ===")
    print(df.to_string(float_format=lambda v: f"{v:,.2f}"))

    print("\n=== Charging events by mode ===")
    modes = pd.concat(by_mode.values(), axis=1).fillna(0).astype(int)
    print(modes.to_string())

    if len(rows) > 1:
        base, last = rows[0], rows[-1]
        print(f"\n{base['scenario']} -> {last['scenario']}: "
              f"charging {base['% charging']:.2f}% -> {last['% charging']:.2f}%  "
              f"({last['% charging'] - base['% charging']:+.2f} pp), "
              f"energy {base['MWh/day']:.1f} -> {last['MWh/day']:.1f} MWh/day")


if __name__ == "__main__":
    main()
