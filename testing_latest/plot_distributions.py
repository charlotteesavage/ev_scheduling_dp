#!/usr/bin/env python3
"""
Two distribution figures across the home-charger scenarios.

  python3 testing_latest/plot_distributions.py
  python3 testing_latest/plot_distributions.py --dir population_results/latest \
      --tags hc1 hc5 hc10 hc30

Produces, in the results directory:
  charging_by_mode_of_day.png   charging load by charger type through the day,
                                one panel per scenario
  activity_distribution.png     where the population is through the day, and how
                                that shifts as home-charger access rises

Two different jobs, two different forms
---------------------------------------
The charging figure stacks by charger type inside each scenario, so one panel
answers both "what shape is the day" and "which chargers carry it". The existing
charging_load_by_mode.png does the transpose -- a panel per charger type, a line
per scenario -- which is the better figure for comparing one charger type across
scenarios, and the worse one for reading the mix within a scenario. Both are
worth having; neither replaces the other.

The activity figure needs a different treatment because the effect is small. The
composition moves by fractions of a percentage point on shares as large as 72%,
so a pair of stacked charts side by side would look identical and say nothing. It
takes the baseline pattern and the CHANGE as two separate panels on their own
scales -- the second panel is the one carrying the result.
"""

import argparse
import json
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
    BATTERY_KWH, INTERVAL_H, N_BINS, PANELS, load_profile, style, hour_axis,
    INK, INK_2, GRID, SURFACE, CAT, share_of, label_for, soc_condition,
)

REPO = Path(__file__).parent.parent
DEFAULT_DIR = REPO / "testing_latest" / "population_results" / "latest"

# Activity types worth naming individually. Everything else is folded into one
# series -- seventeen labelled types would be a colour lookup exercise, not a
# chart, and the tail is under 1% of rows each.
NAMED = ["home", "work", "other", "shop", "pt interaction", "education"]
REST = "all other types"
# Deliberately NOT ACT_COLOURS["pt interaction"], which is also #bab0ac -- the two
# series were being drawn in the same grey and could not be told apart. A cool
# grey keeps the tail recessive without colliding with a named type.
REST_COLOUR = "#8a8f98"


def occupancy(sched, n_persons):
    """
    Share of the population engaged in each activity type, per 5-minute bin.

    An activity row covers a span, not an instant, so each row is expanded across
    the bins it occupies. Summing the result over types gives just under 100% --
    the shortfall is travel, when a person is between activities and not "at" any
    of them.
    """
    start = np.floor(sched.start_time.to_numpy() / INTERVAL_H).astype(int)
    k = sched.duration.to_numpy().astype(int)
    k = np.maximum(k, 1)
    idx = np.repeat(np.arange(len(sched)), k)
    off = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
    bins = (start[idx] + off) % N_BINS
    types = sched.act_type.to_numpy().astype(object)[idx]

    out = {}
    for name in NAMED:
        c = np.zeros(N_BINS)
        np.add.at(c, bins[types == name], 1)
        out[name] = c / n_persons * 100
    rest = np.zeros(N_BINS)
    np.add.at(rest, bins[~np.isin(types, NAMED)], 1)
    out[REST] = rest / n_persons * 100
    return out


# ── Figure 1: charging by charger type, through the day ──────────────────────

def plot_charging_by_mode(scheds, tags, out_path, soc_note=None):
    hours = np.arange(N_BINS) * INTERVAL_H
    fig, axes = plt.subplots(1, len(tags), figsize=(3.6 * len(tags), 4.3),
                             sharey=True, facecolor=SURFACE)
    axes = np.atleast_1d(axes)

    for ax, tag in zip(axes, tags):
        s = scheds[tag]
        bottom = np.zeros(N_BINS)
        for (label, modes), colour in zip(PANELS, CAT):
            band = load_profile(s[s.charge_mode.isin(modes)]) / 1000
            ax.fill_between(hours, bottom, bottom + band, color=colour,
                            linewidth=0, zorder=3, label=label)
            bottom = bottom + band
        style(ax)
        hour_axis(ax)
        ax.set_xticks(range(0, 25, 6))
        ax.set_xticklabels([f"{h:02d}" for h in range(0, 25, 6)])
        ax.set_xlabel("Hour", color=INK_2, fontsize=9.5)
        ax.set_title(f"{label_for(tag)} own a home charger",
                     color=INK, fontsize=11, loc="left", pad=8)
        ax.annotate(f"peak {bottom.max():.0f} MW", (0.97, 0.93),
                    xycoords="axes fraction", ha="right", fontsize=9.5,
                    color=INK_2)
    axes[0].set_ylim(bottom=0)
    axes[0].set_ylabel("Charging load (MW)", color=INK_2, fontsize=10)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, labelcolor=INK_2,
               loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("Charging load by charger type through the day",
                 color=INK, fontsize=13, fontweight="semibold", x=0.006,
                 ha="left", y=0.99)
    note = "Shared vertical scale — the panels are meant to be different heights."
    fig.text(0.006, 0.925, (soc_note + " · " + note[0].lower() + note[1:])
             if soc_note else note, color=INK_2, fontsize=9.5, ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 0.90))
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


# ── Figure 2: where the population is, and how it shifts ─────────────────────

def plot_activity_distribution(occ, counts, tags, out_path, soc_note=None):
    hours = np.arange(N_BINS) * INTERVAL_H
    base, last = tags[0], tags[-1]
    fig = plt.figure(figsize=(14, 4.6), facecolor=SURFACE)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1.25, 0.8])

    # (a) the pattern itself, away from home. Home is excluded rather than stacked:
    # it runs to 100% overnight and would flatten everything else into the baseline.
    ax = fig.add_subplot(gs[0])
    bottom = np.zeros(N_BINS)
    for name in NAMED[1:] + [REST]:
        colour = ACT_COLOURS.get(name, REST_COLOUR)
        band = occ[base][name]
        # `where` guards the zero-area case: a band that falls to exactly zero
        # leaves a degenerate polygon whose coincident edges can render as a
        # hairline. (The thin red evening band is not that -- education genuinely
        # runs at 0.2-1.1% of the population into the night.)
        ax.fill_between(hours, bottom, bottom + band, where=band > 1e-9,
                        color=colour, linewidth=0, zorder=3, label=name,
                        interpolate=False)
        bottom = bottom + band
    style(ax)
    hour_axis(ax)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Share of population (%)", color=INK_2, fontsize=10)
    ax.set_xlabel("Time of day", color=INK_2, fontsize=10)
    ax.set_title(f"Away from home — {label_for(base)} own a charger",
                 color=INK, fontsize=11.5,
                 loc="left", pad=8)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK_2, loc="upper left",
              ncol=2, handlelength=1.1)

    # (b) the result. Separate panel and separate scale because the shifts are
    # ~1 pp against shares of 70% -- on the panel to the left they are invisible.
    ax = fig.add_subplot(gs[1])
    ax.axhline(0, color=INK_2, linewidth=1, zorder=2)
    for name in NAMED:
        delta = occ[last][name] - occ[base][name]
        colour = ACT_COLOURS.get(name, REST_COLOUR)
        ax.plot(hours, delta, color=colour, linewidth=2, zorder=3,
                solid_capstyle="round")
        # Direct-label at each series' own extreme, so identity never rests on
        # colour alone against a seventeen-colour activity palette.
        j = int(np.abs(delta).argmax())
        if abs(delta[j]) > 0.12:
            ax.annotate(name, (hours[j], delta[j]), textcoords="offset points",
                        xytext=(0, 7 if delta[j] > 0 else -14), ha="center",
                        fontsize=9, color=colour, fontweight="semibold")
    style(ax)
    hour_axis(ax)
    ax.set_ylabel("Change in share (pp)", color=INK_2, fontsize=10)
    ax.set_xlabel("Time of day", color=INK_2, fontsize=10)
    ax.set_title(f"Change, {label_for(base)} → {label_for(last)}",
                 color=INK, fontsize=11.5,
                 loc="left", pad=8)

    # (c) the one number that moves monotonically across all four scenarios.
    ax = fig.add_subplot(gs[2])
    vals = [counts[t] for t in tags]
    ax.plot(range(len(tags)), vals, color=CAT[0], linewidth=2, marker="o",
            markersize=8, markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    for i, v in enumerate(vals):
        ax.annotate(f"{v:.2f}", (i, v), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=9, color=INK_2)
    style(ax)
    ax.set_xticks(range(len(tags)))
    ax.set_xticklabels([label_for(t) for t in tags], fontsize=9.5)
    ax.set_ylim(min(vals) * 0.985, max(vals) * 1.015)
    ax.set_ylabel("Activities per person", color=INK_2, fontsize=10)
    ax.set_xlabel("Home-charger share", color=INK_2, fontsize=10)
    ax.set_title("Day gets slightly fuller", color=INK, fontsize=11.5,
                 loc="left", pad=8)
    # This axis does not start at zero, which makes a 3.6% rise look like a cliff.
    # Stating the change in relative terms puts the magnitude back, rather than
    # leaving the reader to infer it from a zoomed scale.
    ax.annotate(f"{vals[-1] / vals[0] - 1:+.1%} across the sweep\n"
                "(note: axis does not start at zero)",
                (0.5, 0.04), xycoords="axes fraction", ha="center",
                fontsize=8.5, color=INK_2)

    fig.suptitle("What people do, and how home-charger access shifts it",
                 color=INK, fontsize=13, fontweight="semibold", x=0.005,
                 ha="left", y=0.995)
    if soc_note:
        fig.text(0.005, 0.935, soc_note, color=INK_2, fontsize=9.5, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    # After tight_layout: passing wspace through gridspec_kw makes the two
    # incompatible, and matplotlib warns instead of honouring either.
    fig.subplots_adjust(wspace=0.30)
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, dest="results_dir")
    ap.add_argument("--tags", nargs="*", default=["hc1", "hc5", "hc10", "hc30"])
    args = ap.parse_args()

    tags = [t for t in args.tags
            if (args.results_dir / f"schedules_{t}.parquet").exists()]
    missing = [t for t in args.tags if t not in tags]
    if missing:
        print(f"  note: no schedules for {', '.join(missing)} — skipping")
    if not tags:
        sys.exit(f"No scenarios found in {args.results_dir}")
    tags.sort(key=share_of)

    scheds, occ, counts = {}, {}, {}
    manifests, logs = {}, {}
    for tag in tags:
        s = pd.read_parquet(args.results_dir / f"schedules_{tag}.parquet")
        n = s.pid.nunique()
        scheds[tag] = s
        occ[tag] = occupancy(s, n)
        counts[tag] = len(s) / n

        mp = args.results_dir / f"manifest_{tag}.json"
        manifests[tag] = json.loads(mp.read_text()) if mp.exists() else {}
        lp = args.results_dir / f"run_log_{tag}.csv"
        logs[tag] = None
        if lp.exists():
            have = set(pd.read_csv(lp, nrows=0).columns)
            want = [c for c in ("has_home_charger", "initial_soc") if c in have]
            logs[tag] = pd.read_csv(lp, usecols=want) if len(want) == 2 else None

    # Derived, never assumed: --dir can point at the equal-start control runs, and
    # a hardcoded caption would be correct for one directory and wrong for the other.
    soc_note = soc_condition(manifests, logs)

    print(f"Scenarios: {', '.join(tags)}")
    print(f"{soc_note or '  warning: initial-SoC setting not recorded or inconsistent'}\n")
    print(f"{'':7}{'acts/person':>13}{'peak MW':>10}{'home @17:30':>13}"
          f"{'work @10:00':>13}")
    j1730, j1000 = int(17.5 / INTERVAL_H), int(10.0 / INTERVAL_H)
    for tag in tags:
        peak = (load_profile(scheds[tag]) / 1000).max()
        print(f"  {tag:5}{counts[tag]:13.4f}{peak:10.1f}"
              f"{occ[tag]['home'][j1730]:12.2f}%{occ[tag]['work'][j1000]:12.2f}%")

    out = args.results_dir
    plot_charging_by_mode(scheds, tags, out / "charging_by_mode_of_day.png",
                          soc_note)
    plot_activity_distribution(occ, counts, tags,
                               out / "activity_distribution.png", soc_note)
    print(f"\nWrote:\n  {out / 'charging_by_mode_of_day.png'}"
          f"\n  {out / 'activity_distribution.png'}")


if __name__ == "__main__":
    main()
