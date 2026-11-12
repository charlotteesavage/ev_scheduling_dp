#!/usr/bin/env python3
"""
How long the DP takes: per-person solve time, how it scales, and throughput.

  python3 testing_latest/plot_timing.py
  python3 testing_latest/plot_timing.py --dirs population_results/latest population_results/equal_soc

Produces solve_time.png in the first results directory.

Where the numbers come from
---------------------------
Per-person solve time is measured inside the worker and written to `time_ms` on
every run-log row. It spans the whole of run_one_person(), not the DP alone: the
DataFrame slice and ctypes array build, the travel-skim lookup, the DP, and the
schedule extraction. Measured over 2,000 people at 30% share, the C DP is 88% of
it (11.9 of 13.5 ms); Python-side setup and extraction are the other 12%. It
excludes the parent process's I/O, so it is per-person work, not per-person
wall-clock.
Wall-clock is the gap between the manifest (written just before the pool starts)
and the combined schedules file (written at the end), so it INCLUDES the compact
build, the part writes and the final concatenation -- it is what the run actually
cost, not a best case.

A run whose recorded wall-clock implies a parallel speed-up below 1.5x is shown
but excluded from the throughput comparison: it means the run did not have the
machine to itself, used a different worker count, or predates a fix. Averaging it
in would understate the others rather than exposing the anomaly.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
from plot_scenario_comparison import (
    style, INK, INK_2, SURFACE, RAMP, CAT, label_for, share_of,
)

REPO = Path(__file__).parent.parent
RESULTS = REPO / "testing_latest" / "population_results"
MIN_SPEEDUP = 1.5


def collect(dirs):
    runs = []
    for d in dirs:
        for log_path in sorted(d.glob("run_log_*.csv")):
            tag = log_path.stem[len("run_log_"):]
            sched = d / f"schedules_{tag}.parquet"
            if not sched.exists():
                continue
            have = set(pd.read_csv(log_path, nrows=0).columns)
            want = [c for c in ("time_ms", "n_acts_in", "dssr_rounds") if c in have]
            log = pd.read_csv(log_path, usecols=want)

            manifest_path = d / f"manifest_{tag}.json"
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            wall = None
            if manifest.get("written_at"):
                t0 = dt.datetime.fromisoformat(manifest["written_at"]).timestamp()
                wall = sched.stat().st_mtime - t0
            cpu = log.time_ms.sum() / 1000
            runs.append({
                "dir": d.name, "tag": tag, "log": log, "n": len(log),
                "cpu_s": cpu, "wall_s": wall,
                "workers": manifest.get("workers"),
                "speedup": (cpu / wall) if wall and wall > 0 else None,
            })
    # Glob order is alphabetical, which puts hc10 before hc5 and scatters the two
    # directories. Order by configuration, then by home-charger share, so the bars
    # read the way the scenarios do everywhere else.
    order = {d.name: i for i, d in enumerate(dirs)}
    runs.sort(key=lambda r: (order.get(r["dir"], 99), share_of(r["tag"])))
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", nargs="*", type=Path,
                    default=[RESULTS / "latest", RESULTS / "equal_soc"])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    runs = collect(args.dirs)
    if not runs:
        sys.exit("No run logs found.")
    ok = [r for r in runs if r["speedup"] and r["speedup"] >= MIN_SPEEDUP]
    excluded = [r for r in runs if r not in ok]

    # Per-person distribution: pooled over the comparable runs. It is very nearly
    # the same shape in each, and pooling puts every measured person in one curve.
    times = pd.concat([r["log"].time_ms for r in (ok or runs)])

    fig = plt.figure(figsize=(14, 4.6), facecolor=SURFACE)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.15, 1.05, 1.0])

    # ── (a) distribution ────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[0])
    bins = np.logspace(np.log10(max(times.min(), 0.2)), np.log10(times.max()), 60)
    ax.hist(times, bins=bins, color=RAMP[2], edgecolor=SURFACE, linewidth=0.4,
            zorder=3)
    ax.set_xscale("log")
    style(ax)
    med, p99 = times.median(), times.quantile(0.99)
    for v, lab, col in ((med, f"median {med:.1f} ms", INK),
                        (p99, f"99th pct {p99:.0f} ms", CAT[1])):
        ax.axvline(v, color=col, linewidth=1.6, linestyle=(0, (4, 3)), zorder=4)
        ax.annotate(lab, (v, 0.94), xycoords=("data", "axes fraction"),
                    rotation=90, ha="right", va="top", fontsize=9, color=col)
    ax.set_xlabel("Solve time per person (ms, log scale)", color=INK_2, fontsize=10)
    ax.set_ylabel("Number of people", color=INK_2, fontsize=10)
    ax.set_title("Most days are solved in milliseconds",
                 color=INK, fontsize=11.5, loc="left", pad=10)
    share = times.nlargest(max(len(times) // 10, 1)).sum() / times.sum()
    ax.annotate(f"the slowest 10% of people\nuse {share:.0%} of all compute",
                (0.97, 0.72), xycoords="axes fraction", ha="right", fontsize=9,
                color=INK_2)

    # ── (b) scaling with problem size ───────────────────────────────────────
    ax = fig.add_subplot(gs[1])
    pooled = pd.concat([r["log"] for r in (ok or runs)])
    g = pooled.groupby("n_acts_in").time_ms.agg(["median", "size"])
    g = g[(g["size"] >= 200) & (g.index >= 3)]
    ax.plot(g.index, g["median"], color=RAMP[3], linewidth=2, marker="o",
            markersize=6, markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=4)
    # Power-law fit. A DP over a time-expanded graph grows with the number of
    # activities faster than linearly, and naming the exponent is more useful on a
    # poster than a curve the reader has to eyeball.
    b, a = np.polyfit(np.log(g.index), np.log(g["median"]), 1)
    xs = np.array([g.index.min(), g.index.max()], dtype=float)
    ax.plot(xs, np.exp(a) * xs ** b, color=INK_2, linewidth=1.2,
            linestyle=(0, (5, 4)), zorder=3)
    ax.set_xscale("log"); ax.set_yscale("log")
    style(ax)
    ax.set_xticks([4, 6, 8, 12, 16, 24])
    ax.set_xticklabels([4, 6, 8, 12, 16, 24])
    ax.set_xlabel("Activities in the person's day", color=INK_2, fontsize=10)
    ax.set_ylabel("Median solve time (ms)", color=INK_2, fontsize=10)
    ax.set_title("Cost grows with the size of the day",
                 color=INK, fontsize=11.5, loc="left", pad=10)
    ax.annotate(f"time ∝ activities$^{{{b:.2f}}}$", (0.05, 0.88),
                xycoords="axes fraction", fontsize=11, color=INK)

    # ── (c) throughput ──────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[2])
    show = ok or runs
    labels = [f"{label_for(r['tag'])}\n{'95/40' if r['dir'] != 'equal_soc' else '40 flat'}"
              for r in show]
    walls = [r["wall_s"] / 60 for r in show]
    xs = np.arange(len(show))
    ax.bar(xs, walls, 0.6, color=RAMP[1], edgecolor=SURFACE, linewidth=2, zorder=3)
    for x, r, w in zip(xs, show, walls):
        ax.annotate(f"{w:.1f} min", (x, w), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=9, color=INK_2)
        ax.annotate(f"{r['n'] / r['wall_s']:.0f}/s", (x, 0),
                    textcoords="offset points", xytext=(0, 8), ha="center",
                    fontsize=9, color="#ffffff", zorder=4)
    style(ax)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylim(0, max(walls) * 1.25)
    ax.set_ylabel("Wall-clock per run (minutes)", color=INK_2, fontsize=10)
    ax.set_title("A full population in minutes", color=INK, fontsize=11.5,
                 loc="left", pad=10)
    eff = np.mean([r["speedup"] for r in show]) / (show[0]["workers"] or 4)
    ax.annotate(f"{show[0]['n']:,} people · {show[0]['workers'] or 4} cores\n"
                f"{eff:.0%} parallel efficiency",
                (0.5, 0.88), xycoords="axes fraction", ha="center", fontsize=9,
                color=INK_2)

    fig.suptitle("Runtime of the scheduling DP", color=INK, fontsize=13,
                 fontweight="semibold", x=0.005, ha="left", y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.subplots_adjust(wspace=0.30)

    out = args.out or (args.dirs[0] / "solve_time.png")
    fig.savefig(out, dpi=170, facecolor=SURFACE)
    plt.close(fig)

    print(f"{'run':26}{'people':>9}{'wall':>9}{'CPU':>9}{'speed-up':>10}{'people/s':>10}")
    for r in runs:
        mark = "" if r in ok else "   <- excluded from the comparison"
        w = f"{r['wall_s']/60:.2f} min" if r["wall_s"] else "n/a"
        sp = f"{r['speedup']:.2f}x" if r["speedup"] else "n/a"
        ps = f"{r['n']/r['wall_s']:.0f}" if r["wall_s"] else "n/a"
        print(f"  {r['dir'] + '/' + r['tag']:24}{r['n']:9,}{w:>9}"
              f"{r['cpu_s']/60:8.2f}m{sp:>10}{ps:>10}{mark}")
    print(f"\nmedian {med:.2f} ms · 99th pct {p99:.0f} ms · "
          f"slowest 10% use {share:.0%} of compute · time ∝ activities^{b:.2f}")
    if excluded:
        print("\nExcluded runs had a parallel speed-up below "
              f"{MIN_SPEEDUP}x — different worker count, a shared machine, or a "
              "build predating a fix. Their per-person times are still pooled "
              "into the distribution only if no comparable run exists.")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
