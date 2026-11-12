#!/usr/bin/env python3
"""
Check a finished population run for correctness.

  python3 testing_latest/check_population_output.py --tag hc1
  python3 testing_latest/check_population_output.py --tag hc1 --dir population_results/latest
  python3 testing_latest/check_population_output.py --tag hc1 --data sheffield_hc1.parquet

Why this exists
---------------
validation_tests/run_validation_tests.py checks the same constraints, but on nine
hand-built fixtures of a few activities each. Those fixtures cannot contain the
awkward cases a real population does: a person with 30 activities, a person whose
time windows barely admit a schedule, a person the DSSR loop gave up on. This
script applies the constraints to every schedule an actual run produced, so
"the tests pass" and "this run is sound" become two separate claims.

It checks structure and internal consistency, NOT optimality. A schedule can pass
every check here and still not be the best schedule available -- proving that needs
the brute-force comparison in run_validation_tests.py, which is only tractable on
tiny fixtures. What this rules out is a run that is silently broken: people missing,
batteries below empty, activities outside their time windows, charging that appears
from nowhere.

Units, which are easy to get wrong here:
  start_time      hours (float)
  duration        FIVE-MINUTE INTERVALS (so hours = duration * 5 / 60)
  soc_*           fraction of battery capacity, 0..1
  earliest_start / latest_start / min_duration / max_duration   5-minute intervals
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent
DEFAULT_DIR = REPO / "testing_latest" / "population_results" / "latest"
INTERVAL_HOURS = 5 / 60.0
BATTERY_KWH = 60.0   # battery_capacity in src/scheduling.c
TOL = 1e-6          # schedules carry float hours; exact equality is not available


class Report:
    """Collects pass/fail lines so one bad check does not hide the rest."""

    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((bool(ok), name, detail))
        return ok

    def show(self):
        width = max(len(n) for _, n, _ in self.rows)
        for ok, name, detail in self.rows:
            mark = "PASS" if ok else "FAIL"
            print(f"  {mark}  {name:<{width}}  {detail}")
        failed = sum(1 for ok, _, _ in self.rows if not ok)
        print(f"\n{len(self.rows) - failed} passed, {failed} failed")
        return failed


def load(results_dir: Path, tag: str, data_path: Path):
    suffix = f"_{tag}" if tag else ""
    sched_path = results_dir / f"schedules{suffix}.parquet"
    log_path = results_dir / f"run_log{suffix}.csv"
    if not sched_path.exists():
        sys.exit(f"No schedules at {sched_path}")
    sched = pd.read_parquet(sched_path)
    log = pd.read_csv(log_path) if log_path.exists() else None
    acts = pd.read_parquet(data_path) if data_path.suffix == ".parquet" \
        else pd.read_csv(data_path, low_memory=False)
    return sched, log, acts, sched_path, log_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="", help="scenario tag, e.g. hc1")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, dest="results_dir",
                    help="results directory (default: %(default)s)")
    ap.add_argument("--data", type=Path, default=None,
                    help="prepared activities file the run used "
                         "(default: sheffield_<tag>.parquet)")
    ap.add_argument("--horizon-hours", type=float, default=24.0)
    ap.add_argument("--expect-params", default="oasis_t12", metavar="NAME",
                    help="Fail unless the run used this parameter set "
                         "(default: %(default)s). Pass '' to skip the check.")
    args = ap.parse_args()

    data_path = args.data
    if data_path is None:
        guess = REPO / "testing_latest" / f"sheffield_{args.tag}.parquet"
        data_path = guess if guess.exists() else \
            REPO / "testing_latest" / "sheffield_activities_prepared.parquet"
    if not data_path.exists():
        sys.exit(f"Prepared data not found: {data_path}  (pass --data)")

    sched, log, acts, sched_path, log_path = load(args.results_dir, args.tag, data_path)
    r = Report()

    suffix = f"_{args.tag}" if args.tag else ""
    manifest_path = args.results_dir / f"manifest{suffix}.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    limit = manifest.get("limit")

    print(f"schedules : {sched_path}")
    print(f"run log   : {log_path if log is not None else '(none)'}")
    print(f"input     : {data_path}")
    print(f"\n{len(sched):,} schedule rows | {sched.pid.nunique():,} persons | "
          f"{acts.pid.nunique():,} persons in input\n")

    sched = sched.sort_values(["pid", "start_time"], kind="stable")
    end_hr = sched.start_time + sched.duration * INTERVAL_HOURS

    # ── 1. Coverage ──────────────────────────────────────────────────────────
    # A run that quietly dropped people still produces a plausible-looking file.
    in_pids = set(acts.pid.unique())
    out_pids = set(sched.pid.unique())
    if limit:
        # A --limit run covers only the first N persons on purpose, so comparing it
        # against the whole input would report a failure for doing what was asked.
        # The population it should cover is the one the run itself logged.
        r.check("every person the run attempted has a schedule",
                not (set(log.pid) - out_pids) if log is not None else True,
                f"limit={limit:,}; "
                f"{len(set(log.pid) - out_pids) if log is not None else 0:,} missing")
    else:
        r.check("every input person has a schedule", not (in_pids - out_pids),
                f"{len(in_pids - out_pids):,} missing")
    r.check("no unexpected persons in output", not (out_pids - in_pids),
            f"{len(out_pids - in_pids):,} extra")

    if log is not None:
        r.check("every person succeeded", log.success.all(),
                f"{(~log.success).sum():,} failures"
                + (f" | {log.loc[~log.success, 'error'].value_counts().to_dict()}"
                   if not log.success.all() else ""))
        # A person appearing twice means two part files overlapped -- most likely a
        # --resume that re-ran work it should have skipped.
        r.check("no duplicated persons in the log",
                not log.pid.duplicated().any(),
                f"{log.pid.duplicated().sum():,} duplicates")
        r.check("DSSR loop never hit its cap", (log.dssr_rounds < 200).all(),
                f"{(log.dssr_rounds >= 200).sum():,} at the cap, max {log.dssr_rounds.max()}")

    # ── 1b. Provenance ───────────────────────────────────────────────────────
    # Which coefficients produced these numbers is not visible in the schedules.
    # Switching parameter set alone moved charging participation from 2.6% to 8.1%
    # on an identical population, so a results file whose parameters are unknown is
    # a results file that cannot be compared with any other.
    if args.expect_params:
        stamped = set()
        if log is not None and "param_set" in log.columns:
            stamped = set(log.param_set.dropna().unique())
        if manifest.get("param_set"):
            stamped.add(manifest["param_set"])

        if not stamped:
            # Pre-dates the stamping, so the parameters genuinely are not recorded.
            # Report that rather than passing: an unverifiable run is not a verified one.
            r.check(f"run used {args.expect_params} parameters", False,
                    "NOT RECORDED — this run predates parameter stamping, so which "
                    "coefficients produced it cannot be established from the output. "
                    "Re-run it to get a manifest.")
        else:
            r.check(f"run used {args.expect_params} parameters",
                    stamped == {args.expect_params},
                    f"recorded: {sorted(stamped)}")

    # ── 2. Battery ───────────────────────────────────────────────────────────
    soc = sched[["soc_start", "soc_end"]]
    r.check("SoC never below 0", (soc >= -TOL).all().all(),
            f"min {soc.min().min():.4f}")
    r.check("SoC never above 1", (soc <= 1 + TOL).all().all(),
            f"max {soc.max().max():.4f}")

    charging = sched[sched.is_charging == 1]
    idle = sched[sched.is_charging == 0]
    r.check("SoC rises only where charging",
            not (idle.soc_end > idle.soc_start + TOL).any(),
            f"{(idle.soc_end > idle.soc_start + TOL).sum():,} rows gained charge "
            "without charging")
    r.check("charging rows actually add charge",
            not (charging.soc_end < charging.soc_start - TOL).any(),
            f"{(charging.soc_end < charging.soc_start - TOL).sum():,} lost charge")
    r.check("charge_duration fits inside the activity",
            (charging.charge_duration <= charging.duration + TOL).all()
            if len(charging) else True,
            f"{(charging.charge_duration > charging.duration + TOL).sum():,} overrun")
    # Energy delivered must match the charger's rate. This is the strongest check
    # here: it ties the SoC the DP reported to the physics it claims to be using,
    # so a mis-set rate, a wrong charge_mode or a unit slip in charge_duration all
    # show up. Delivered energy can be LESS than rate x time (the battery fills up
    # and charging stops) but never more.
    #
    #   charge_duration is in hours, soc is a fraction of a 60 kWh battery.
    POWER_KW = {0: 0.0, 1: 7.0, 2: 22.0, 3: 50.0,
                4: 7.0, 5: 22.0, 6: 50.0, 7: 7.0}
    if len(charging):
        delivered = (charging.soc_end - charging.soc_start) * BATTERY_KWH
        possible = charging.charge_mode.map(POWER_KW) * charging.charge_duration
        # One interval of headroom: SoC is stepped in 5-minute blocks, so the last
        # block can deliver slightly more than the elapsed time implies.
        headroom = charging.charge_mode.map(POWER_KW) * INTERVAL_HOURS + 1e-6
        over = delivered > possible + headroom
        r.check("energy delivered never exceeds rate x time", not over.any(),
                f"{over.sum():,} rows over, worst by "
                f"{(delivered - possible).max():.2f} kWh")
        r.check("charging rows have a charging mode",
                (charging.charge_mode > 0).all(),
                f"{(charging.charge_mode == 0).sum():,} rows charging on mode 0")
        r.check("charge modes are all known",
                charging.charge_mode.isin(POWER_KW).all(),
                f"unknown: {sorted(set(charging.charge_mode) - set(POWER_KW))}")

    r.check("charging costs are non-negative",
            (sched.charge_cost >= -TOL).all(), f"min {sched.charge_cost.min():.4f}")

    # ── 3. Time ──────────────────────────────────────────────────────────────
    r.check("nothing ends after the horizon",
            (end_hr <= args.horizon_hours + TOL).all(),
            f"{(end_hr > args.horizon_hours + TOL).sum():,} rows past "
            f"{args.horizon_hours}h, latest {end_hr.max():.4f}h")
    r.check("no negative or zero durations", (sched.duration > 0).all(),
            f"{(sched.duration <= 0).sum():,} rows")

    # Activities must not overlap: the next one starts no earlier than this one
    # ends. Travel makes the gap larger, never smaller, so this is a one-sided test.
    nxt_start = sched.start_time.shift(-1)
    same_person = sched.pid == sched.pid.shift(-1)
    overlap = same_person & (nxt_start < end_hr - TOL)
    r.check("activities never overlap", not overlap.any(),
            f"{overlap.sum():,} overlapping pairs")

    # ── 4. Against the person's own input rows ───────────────────────────────
    key = ["pid", "act_id"]
    ref = acts.rename(columns={"id": "act_id"})[
        ["pid", "act_id", "group", "earliest_start", "latest_start",
         "min_duration", "max_duration"]]
    m = sched.merge(ref, on=key, how="left", validate="many_to_one")
    r.check("every scheduled activity exists in the input",
            m.group.notna().all(), f"{m.group.isna().sum():,} unmatched rows")

    m = m[m.group.notna()]
    start_iv = m.start_time / INTERVAL_HOURS
    # Windows are inclusive and expressed in intervals; allow half an interval of
    # float slack so a boundary case is not reported as a breach.
    early = start_iv < m.earliest_start - 0.5
    late = start_iv > m.latest_start + 0.5
    r.check("starts respect earliest_start", not early.any(), f"{early.sum():,} too early")
    r.check("starts respect latest_start", not late.any(), f"{late.sum():,} too late")
    short = m.duration < m.min_duration - 0.5
    long_ = m.duration > m.max_duration + 0.5
    r.check("durations respect min_duration", not short.any(), f"{short.sum():,} too short")
    r.check("durations respect max_duration", not long_.any(), f"{long_.sum():,} too long")

    # Elementarity: DSSR exists to stop a person doing the same activity group
    # twice. dawn/dusk (group 0 in the input, 1-indexed) are the exception.
    real = m[m.group > 1]
    repeats = real.groupby(["pid", "group"]).size()
    n_bad = int((repeats > 1).sum())
    r.check("no activity group visited twice", n_bad == 0,
            f"{n_bad:,} person-groups repeated")

    # ── 5. Log agrees with the schedules ─────────────────────────────────────
    if log is not None:
        last = sched.groupby("pid").tail(1).set_index("pid")
        j = log.set_index("pid").join(last[["soc_end", "utility"]], how="inner")
        soc_gap = (j.final_soc - j.soc_end).abs()
        util_gap = (j.final_utility - j.utility).abs()
        r.check("log final SoC matches the schedule", (soc_gap < 1e-6).all(),
                f"worst gap {soc_gap.max():.2e}")
        r.check("log final utility matches the schedule", (util_gap < 1e-6).all(),
                f"worst gap {util_gap.max():.2e}")

    failed = r.show()

    # ── Descriptives, for eyeballing rather than passing ─────────────────────
    print("\nFigures to sanity-check by eye (not pass/fail):")
    persons = sched.pid.nunique()
    print(f"  persons charging          {charging.pid.nunique():,} "
          f"({charging.pid.nunique()/persons:.2%})")
    print(f"  charging events           {len(charging):,}")
    if len(charging):
        kwh = (charging.soc_end - charging.soc_start) * BATTERY_KWH
        print(f"  kWh per event             mean {kwh.mean():.2f}, max {kwh.max():.2f}")
        print(f"  charge modes used         "
              f"{charging.charge_mode.value_counts().to_dict()}")
    print(f"  activities per person     {len(sched)/persons:.2f}")
    print(f"  mean final SoC            {sched.groupby('pid').soc_end.last().mean():.2%}")
    if log is not None:
        print(f"  home-charger share        {log.has_home_charger.mean():.2%}")
        tot = log.legs_routed.sum() + log.legs_approx.sum()
        if tot:
            print(f"  travel legs approximated  {log.legs_approx.sum()/tot:.2%} "
                  "(rest from the route table)")
        print(f"  median time per person    {log.time_ms.median():.1f} ms")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
