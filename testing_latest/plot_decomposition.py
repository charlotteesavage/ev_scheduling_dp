#!/usr/bin/env python3
"""
Separate the two channels the home-charger lever acts through.

  python3 testing_latest/plot_decomposition.py
  python3 testing_latest/plot_decomposition.py --baseline population_results/latest \
      --equal population_results/equal_soc --from hc1 --to hc30

Owning a home charger does two things in this model:

  1. STARTING BATTERY  -- the owner begins the day near full (95% state of
     charge) rather than on whatever they had left (40% SoC). Overnight charging
     is represented this way rather than as an activity.
  2. DAYTIME CHARGING  -- the owner can plug in at home during the day
     (charge_mode 7).

In the baseline runs the two are tangled together, so the headline figure cannot
say which one produced it. The equal-SoC runs untangle them: they set both
starting-SoC distributions to the same N(0.40, 0.10), and because
SocMixture.draw() seeds on the person id alone -- never on home-charger status --
every driver then begins the day on exactly the same charge in both scenarios.
Whatever difference survives is channel 2 acting on its own.

This is worth doing because the two channels turn out to push in OPPOSITE
directions, and the baseline headline is their net. Reporting only the net would
attribute to home charging an effect that is mostly an artefact of how overnight
charging is represented.

Produces, in the baseline directory:
  decomposition_public_demand.png   public energy under each configuration
  decomposition_load_profile.png    when the load sits, public vs home
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
from plot_scenario_comparison import (
    BATTERY_KWH, INTERVAL_H, N_BINS, load_profile, style, hour_axis,
    INK, INK_2, GRID, SURFACE, RAMP, label_for,
)

REPO = Path(__file__).parent.parent
RESULTS = REPO / "testing_latest" / "population_results"

C_LOW, C_HIGH = RAMP[0], RAMP[4]        # ordinal: few home chargers -> many
C_PUBLIC, C_HOME = "#2a78d6", "#eb6834"  # identity: where the energy is drawn
HOME_MODE = 7


def read(results_dir: Path, tag: str):
    sched = pd.read_parquet(results_dir / f"schedules_{tag}.parquet")
    mpath = results_dir / f"manifest_{tag}.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}
    return sched, manifest


def energy(sched, home=None):
    """MWh/day. home=True for charge_mode 7 only, False for everything else."""
    ch = sched[sched.is_charging == 1]
    if home is True:
        ch = ch[ch.charge_mode == HOME_MODE]
    elif home is False:
        ch = ch[ch.charge_mode != HOME_MODE]
    return float(((ch.soc_end - ch.soc_start) * BATTERY_KWH).sum() / 1000)


def check_equal_soc(manifest, tag):
    """The whole decomposition rests on the two starting distributions matching."""
    soc = manifest.get("initial_soc")
    if not soc:
        print(f"  warning: {tag} has no manifest — cannot confirm it used equal "
              "starting SoC")
        return
    if (soc["home_mean"], soc["home_std"]) != (soc["nohome_mean"], soc["nohome_std"]):
        sys.exit(
            f"{tag} in the equal-SoC directory was run with DIFFERENT starting-SoC "
            f"distributions (home {soc['home_mean']}±{soc['home_std']}, "
            f"no-home {soc['nohome_mean']}±{soc['nohome_std']}). The decomposition "
            "needs them equal. Re-run with --home-soc-mean 0.40 --home-soc-std 0.10.")


def plot_public_demand(rows, out_path, tag_from, tag_to):
    """
    Left: public energy, both configurations. Right: where the energy is drawn.

    The left panel is the result. Bars rather than lines because there are two
    discrete configurations, not a continuum, and the comparison the reader needs
    is between the two percentage changes rather than between four heights.
    """
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.9), facecolor=SURFACE,
                             gridspec_kw={"width_ratios": [1, 1.05]})
    configs = ["Baseline\ninitial SoC 95% / 40%", "Equal start\ninitial SoC 40% for all"]
    x = np.arange(2)
    width = 0.34

    ax = axes[0]
    for i, (tag, colour) in enumerate(((tag_from, C_LOW), (tag_to, C_HIGH))):
        vals = [rows[c][tag]["public"] for c in ("baseline", "equal")]
        bars = ax.bar(x + (i - 0.5) * width, vals, width * 0.92, color=colour,
                      edgecolor=SURFACE, linewidth=2, zorder=3)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:.0f}", (b.get_x() + b.get_width() / 2, v),
                        textcoords="offset points", xytext=(0, 5), ha="center",
                        fontsize=9.5, color=INK_2)
            # Name each bar on the bar instead of in a legend box. The same two
            # series repeat in both groups, so a legend would spend the panel's
            # only clear space restating what a four-character label says in place
            # -- and that clear space is where the percentage brackets go.
            ax.annotate(label_for(tag), (b.get_x() + b.get_width() / 2, 0),
                        textcoords="offset points", xytext=(0, 9), ha="center",
                        fontsize=9.5, color="#ffffff", zorder=4)
    # The percentage change is the quantity being compared, so it is drawn rather
    # than left for the reader to compute from four bar heights.
    top = max(rows[c][t]["public"] for c in ("baseline", "equal")
              for t in (tag_from, tag_to))
    for xi, cfg in enumerate(("baseline", "equal")):
        a, b = rows[cfg][tag_from]["public"], rows[cfg][tag_to]["public"]
        yb = top * 1.14
        # Each leg of the bracket starts just above ITS OWN bar. Using one bar's
        # height for both dropped the left leg through the taller bar beside it.
        ax.plot([xi - width / 2, xi - width / 2, xi + width / 2, xi + width / 2],
                [a + top * 0.045, yb, yb, b + top * 0.045],
                color=INK_2, linewidth=1, zorder=4)
        ax.annotate(f"{b / a - 1:+.1%}", (xi, yb), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=12, color=INK,
                    fontweight="semibold")
    style(ax)
    ax.set_xticks(x)
    ax.set_xticklabels(configs, fontsize=9.5)
    ax.set_ylim(0, top * 1.26)
    ax.set_ylabel("Public charging energy (MWh/day)", color=INK_2, fontsize=10)
    ax.set_title("Public demand falls either way — but not by the same amount",
                 color=INK, fontsize=11.5, loc="left", pad=10)

    ax = axes[1]
    labels, pub, home = [], [], []
    for cfg, cfg_lab in (("baseline", "Baseline"), ("equal", "Equal start")):
        for tag in (tag_from, tag_to):
            labels.append(f"{cfg_lab}\n{label_for(tag)}")
            pub.append(rows[cfg][tag]["public"])
            home.append(rows[cfg][tag]["home"])
    xs = np.arange(len(labels))
    ax.bar(xs, pub, 0.6, color=C_PUBLIC, edgecolor=SURFACE, linewidth=2,
           zorder=3, label="Public chargers")
    ax.bar(xs, home, 0.6, bottom=pub, color=C_HOME, edgecolor=SURFACE, linewidth=2,
           zorder=3, label="Home charger")
    for xi, (p, h) in enumerate(zip(pub, home)):
        if h > 5:
            ax.annotate(f"{h:.0f}", (xi, p + h / 2), ha="center", va="center",
                        fontsize=9, color="#ffffff", zorder=4)
        ax.annotate(f"{p + h:.0f}", (xi, p + h), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=9.5, color=INK_2)
    style(ax)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, max(np.array(pub) + np.array(home)) * 1.18)
    ax.set_ylabel("Energy delivered (MWh/day)", color=INK_2, fontsize=10)
    ax.set_title("Home charging is real once it is not suppressed",
                 color=INK, fontsize=11.5, loc="left", pad=10)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK_2, loc="upper left")

    fig.suptitle("Two channels, opposite signs", color=INK, fontsize=13,
                 fontweight="semibold", x=0.008, ha="left", y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def plot_profiles(scheds, out_path, tag_from, tag_to):
    """
    When the load sits, public and home stacked, one panel per configuration.

    Stacked areas rather than lines: the question here is what the total looks
    like and how much of it is home charging, and a stack answers both at once.
    Shared y-axis, because the totals differ and that difference is the point.
    """
    hours = np.arange(N_BINS) * INTERVAL_H
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.4), sharey=True, facecolor=SURFACE)
    titles = {"baseline": "Baseline — initial SoC 95% for owners, 40% for others",
              "equal": "Equal start — initial SoC 40% for everyone"}

    for ax, cfg in zip(axes, ("baseline", "equal")):
        s = scheds[cfg][tag_to]
        pub = load_profile(s[s.charge_mode != HOME_MODE]) / 1000
        home = load_profile(s[s.charge_mode == HOME_MODE]) / 1000
        ax.fill_between(hours, 0, pub, color=C_PUBLIC, zorder=3, linewidth=0,
                        label="Public chargers")
        ax.fill_between(hours, pub, pub + home, color=C_HOME, zorder=3, linewidth=0,
                        label="Home charger")
        style(ax)
        hour_axis(ax)
        ax.set_xlabel("Time of day", color=INK_2, fontsize=10)
        ax.set_title(titles[cfg], color=INK, fontsize=11.5, loc="left", pad=10)
        peak = (pub + home).max()
        ax.annotate(f"peak {peak:.0f} MW", (0.98, 0.93), xycoords="axes fraction",
                    ha="right", fontsize=10, color=INK_2)
    axes[0].set_ylim(bottom=0)
    axes[0].set_ylabel("Charging load (MW)", color=INK_2, fontsize=10)
    axes[0].legend(frameon=False, fontsize=9, labelcolor=INK_2, loc="upper left")

    fig.suptitle(f"Where the load sits at {label_for(tag_to)} home-charger "
                 "share, under each assumption",
                 color=INK, fontsize=13, fontweight="semibold", x=0.006,
                 ha="left", y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", type=Path, default=RESULTS / "latest")
    ap.add_argument("--equal", type=Path, default=RESULTS / "equal_soc")
    ap.add_argument("--from", dest="tag_from", default="hc1")
    ap.add_argument("--to", dest="tag_to", default="hc30")
    args = ap.parse_args()

    dirs = {"baseline": args.baseline, "equal": args.equal}
    rows, scheds = {}, {}
    for cfg, d in dirs.items():
        rows[cfg], scheds[cfg] = {}, {}
        for tag in (args.tag_from, args.tag_to):
            sched, manifest = read(d, tag)
            if cfg == "equal":
                check_equal_soc(manifest, tag)
            scheds[cfg][tag] = sched
            ch = sched[sched.is_charging == 1]
            rows[cfg][tag] = {
                "public": energy(sched, home=False),
                "home": energy(sched, home=True),
                "total": energy(sched),
                "pct": 100 * ch.pid.nunique() / sched.pid.nunique(),
                "home_events": int((ch.charge_mode == HOME_MODE).sum()),
            }

    print(f"  {'configuration':26}{'share':>6}{'%charge':>9}{'public':>10}{'home':>9}"
          f"{'total':>9}{'home evts':>11}")
    for cfg, cfg_lab in (("baseline", "baseline, SoC 95%/40%"),
                         ("equal", "equal start, SoC 40% flat")):
        for tag in (args.tag_from, args.tag_to):
            r = rows[cfg][tag]
            print(f"  {cfg_lab:26}{label_for(tag):>6}{r['pct']:8.2f}%{r['public']:10.1f}"
                  f"{r['home']:9.1f}{r['total']:9.1f}{r['home_events']:11,}")

    print(f"\nPublic charging demand, {label_for(args.tag_from)} -> "
          f"{label_for(args.tag_to)} home-charger share:")
    shares = {}
    for cfg, lab in (("baseline", "both channels together"),
                     ("equal", "daytime home charging alone")):
        a = rows[cfg][args.tag_from]["public"]
        b = rows[cfg][args.tag_to]["public"]
        shares[cfg] = b / a - 1
        print(f"  {lab:30} {a:6.1f} -> {b:6.1f} MWh   {shares[cfg]:+7.1%}")
    if shares["baseline"]:
        frac = shares["equal"] / shares["baseline"]
        print(f"\n  Substitution to home charging explains {frac:.0%} of the "
              f"baseline reduction.\n  The other {1 - frac:.0%} comes from the "
              "assumption that owners begin the day at 95%.")

    out = args.baseline
    plot_public_demand(rows, out / "decomposition_public_demand.png",
                       args.tag_from, args.tag_to)
    plot_profiles(scheds, out / "decomposition_load_profile.png",
                  args.tag_from, args.tag_to)
    print(f"\nWrote:\n  {out / 'decomposition_public_demand.png'}"
          f"\n  {out / 'decomposition_load_profile.png'}")


if __name__ == "__main__":
    main()
