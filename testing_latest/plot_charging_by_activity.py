#!/usr/bin/env python3
"""
Where the population charges: charging load by the ACTIVITY it happens at.

  python3 testing_latest/plot_charging_by_activity.py
  python3 testing_latest/plot_charging_by_activity.py --shares hc1 hc30

Every other charging figure in this repo splits load by charger TYPE — 7 kW,
22 kW, 50 kW, home. That answers "what hardware carries the load". This one
splits the same load by the activity the driver is doing while it charges, which
answers a different question: where in the day, and in what part of someone's
life, the demand actually lands. A 22 kW post used during a work stay and the
same post used during a shopping trip are the same hardware and completely
different siting problems.

The figure is a 2x2: home-charger share across the columns, starting-SoC
assumption down the rows. All four panels share one vertical scale, because the
comparison between the cells is the whole point — three of them look much alike
and the fourth does not.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
from visualise_schedules import ACT_COLOURS
from plot_scenario_comparison import (
    BATTERY_KWH, INTERVAL_H, N_BINS, load_profile, style, hour_axis,
    INK, INK_2, SURFACE, label_for,
)

REPO = Path(__file__).parent.parent
RESULTS = REPO / "testing_latest" / "population_results"

# Named in stack order, bottom to top. Home sits at the bottom because it is the
# band that moves: anchoring it to the axis means its growth is read against a
# straight edge rather than against whatever is beneath it.
NAMED = ["home", "work", "pt interaction", "other", "shop", "visit", "education"]
REST = "all other activities"
REST_COLOUR = "#8a8f98"          # not ACT_COLOURS["pt interaction"], which is #bab0ac


def charging_by_activity(sched):
    """MW in each 5-minute bin, per activity type, for charging rows only."""
    bands = {}
    for name in NAMED:
        bands[name] = load_profile(sched[sched.act_type == name]) / 1000
    bands[REST] = load_profile(sched[~sched.act_type.isin(NAMED)]) / 1000
    return bands


def totals(sched):
    ch = sched[sched.is_charging == 1]
    return float(((ch.soc_end - ch.soc_start) * BATTERY_KWH).sum() / 1000)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", type=Path, default=RESULTS / "latest",
                    help="runs where owners start the day at 95%% SoC and others at 40%%")
    ap.add_argument("--equal", type=Path, default=RESULTS / "equal_soc",
                    help="runs where everyone starts the day at 40%% SoC")
    ap.add_argument("--shares", nargs=2, default=["hc1", "hc30"], metavar="TAG")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    # "SoC" is spelled out in every one of these labels on purpose: the figure
    # already carries "30% own a home charger" as a column heading, and a bare
    # "95% / 40%" beside it reads as another population share rather than a
    # battery level.
    rows = [("Initial SoC: 95% for owners, 40% for everyone else", args.baseline),
            ("Initial SoC: 40% for everyone", args.equal)]
    # Short forms for the console table, where the full captions do not fit a column.
    short = {rows[0][0]: "SoC 95%/40%", rows[1][0]: "SoC 40% flat"}

    bands, tot = {}, {}
    for row_label, d in rows:
        for tag in args.shares:
            path = d / f"schedules_{tag}.parquet"
            if not path.exists():
                sys.exit(f"Missing {path}")
            s = pd.read_parquet(path)
            bands[(row_label, tag)] = charging_by_activity(s)
            tot[(row_label, tag)] = totals(s)

    hours = np.arange(N_BINS) * INTERVAL_H
    ymax = max(sum(b[k] for k in b).max() for b in bands.values()) * 1.08

    fig, axes = plt.subplots(2, 2, figsize=(13, 7.6), sharex=True, sharey=True,
                             facecolor=SURFACE)
    for r, (row_label, _) in enumerate(rows):
        for c, tag in enumerate(args.shares):
            ax = axes[r][c]
            b = bands[(row_label, tag)]
            bottom = np.zeros(N_BINS)
            for name in NAMED + [REST]:
                colour = ACT_COLOURS.get(name, REST_COLOUR)
                band = b[name]
                ax.fill_between(hours, bottom, bottom + band, where=band > 1e-9,
                                color=colour, linewidth=0, zorder=3, label=name,
                                interpolate=False)
                bottom = bottom + band
            style(ax)
            hour_axis(ax)
            ax.set_ylim(0, ymax)
            ax.set_title(f"{label_for(tag)} own a home charger",
                         color=INK, fontsize=11.5, loc="left", pad=8)
            ax.annotate(f"{tot[(row_label, tag)]:.0f} MWh/day   ·   "
                        f"peak {bottom.max():.0f} MW",
                        (0.975, 0.93), xycoords="axes fraction", ha="right",
                        fontsize=9.5, color=INK_2)
            if r == 1:
                ax.set_xlabel("Time of day", color=INK_2, fontsize=10)
            if c == 0:
                ax.set_ylabel("Charging load (MW)", color=INK_2, fontsize=10)

    # Row captions: the starting-SoC assumption is the factor down the page, and
    # naming it once per row beats repeating it in four panel titles.
    for r, (row_label, _) in enumerate(rows):
        axes[r][0].annotate(row_label, (0, 1.24), xycoords="axes fraction",
                            ha="left", fontsize=12, color=INK, fontweight="semibold")

    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, labelcolor=INK_2,
               loc="lower center", ncol=8, bbox_to_anchor=(0.5, -0.012))
    fig.suptitle("Where the population charges, by activity",
                 color=INK, fontsize=14, fontweight="semibold", x=0.006,
                 ha="left", y=0.995)
    fig.tight_layout(rect=(0, 0.05, 1, 0.965))
    # Generous row gap: each row carries a caption above its panel titles, and at a
    # tighter spacing the second row's caption sits on the first row's x-axis.
    fig.subplots_adjust(hspace=0.55)

    out = args.out or (args.baseline / "charging_by_activity.png")
    fig.savefig(out, dpi=170, facecolor=SURFACE)
    plt.close(fig)

    print(f"  {'configuration':14}{'share':>5}{'MWh/day':>9}{'home':>8}{'work':>8}"
          f"{'pt int':>8}{'other':>8}{'shop':>8}")
    for row_label, _ in rows:
        for tag in args.shares:
            b = bands[(row_label, tag)]
            e = {k: v.sum() * INTERVAL_H for k, v in b.items()}
            print(f"  {short[row_label]:14}{label_for(tag):>5}"
                  f"{tot[(row_label, tag)]:9.0f}"
                  + "".join(f"{e[k]:8.0f}" for k in
                            ["home", "work", "pt interaction", "other", "shop"]))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
