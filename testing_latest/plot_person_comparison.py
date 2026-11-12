#!/usr/bin/env python3
"""
Show what changed for one person between two scenarios.

  python3 testing_latest/plot_person_comparison.py
  python3 testing_latest/plot_person_comparison.py --from hc1 --to hc30
  python3 testing_latest/plot_person_comparison.py --pids 2002000540_7_E02001521_1_2002001274

Why individuals, after the aggregates
-------------------------------------
The aggregate charts say the home-charger lever is compositional: participation
among people WITHOUT a charger does not move at all between scenarios, so the
population figure falls only because people are reclassified. That is a claim
about mechanism, and a mechanism is best shown one person at a time -- the
aggregate cannot distinguish "everyone charges slightly less" from "a few people
stop entirely while nobody else moves". These panels show the second.

The scenario files are NESTED (hc1's charger owners are a subset of hc5's, and so
on up), so a person can be followed across scenarios: someone with no home charger
in hc1 and one in hc30 is the same person, with the same activities, the same
random seed and the same error draws. The only thing that changed is their
home-charger access -- which is what makes the comparison a controlled one rather
than two unrelated schedules side by side.

Reuses the Gantt idiom and the activity palette from visualise_schedules.py, so
these panels read the same way as schedule_gantt.png.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, str(Path(__file__).parent))
from visualise_schedules import ACT_COLOURS      # one palette for activity types
from plot_scenario_comparison import label_for

REPO = Path(__file__).parent.parent
DEFAULT_DIR = REPO / "testing_latest" / "population_results" / "latest"
BATTERY_KWH = 60.0
# Violet, not the gold that schedule_gantt.png uses. The activity palette runs
# warm (work #f28e2b, business #edc948, escort_work #ffbe7d), so a gold charging
# stripe sits right on top of three activity colours -- on a thin bar it is
# genuinely ambiguous whether you are looking at charging or at work. Violet
# appears nowhere in ACT_COLOURS, so the marker can only mean one thing.
CHARGE_MARK = "#4a3aa7"

# The two scenarios are an identity pair here (before / after), not an ordered
# ramp, so they take the first two categorical slots.
C_FROM, C_TO = "#2a78d6", "#eb6834"
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def load(results_dir: Path, tag: str):
    sched = pd.read_parquet(results_dir / f"schedules_{tag}.parquet")
    log_path = results_dir / f"run_log_{tag}.csv"
    log = pd.read_csv(log_path, usecols=["pid", "has_home_charger", "initial_soc"])
    return sched, log


def choose_examples(s_from, s_to, l_from, l_to, n_acts=(4, 7)):
    """
    Pick one person for each way the switch can play out, plus a control.

    Hand-picking would invite the charge that the examples were chosen to suit the
    story, so the rule is written down: among switchers whose schedules are short
    enough to read (4-7 activities in both runs), take the first by pid in each
    category. Deterministic, and re-running on new results picks afresh rather
    than keeping a stale hardcoded id.
    """
    j = l_from.merge(l_to, on="pid", suffixes=("_a", "_b"))
    switchers = set(j[(j.has_home_charger_a == 0) & (j.has_home_charger_b == 1)].pid)
    stayers = set(j[(j.has_home_charger_a == 0) & (j.has_home_charger_b == 0)].pid)

    ch_a = set(s_from.loc[s_from.is_charging == 1, "pid"])
    ch_b = set(s_to.loc[s_to.is_charging == 1, "pid"])
    n_a = s_from.groupby("pid").size()
    n_b = s_to.groupby("pid").size()

    lo, hi = n_acts

    def readable(pids):
        return sorted(p for p in pids
                      if lo <= n_a.get(p, 0) <= hi and lo <= n_b.get(p, 0) <= hi)

    picks = []
    for pool, note in (
        (switchers & ch_a - ch_b, "gained a home charger and stopped charging"),
        (switchers & ch_b - ch_a, "gained a home charger and charged MORE"),
        (switchers & ch_a & ch_b, "gained a home charger, still charged"),
        (stayers & ch_a, "control: no home charger in either scenario"),
    ):
        cand = readable(pool)
        if cand:
            picks.append((cand[0], note, len(cand)))
    return picks


def draw_person(ax_strip, ax_soc, pid, note, n_like, s_from, s_to,
                tag_from, tag_to, soc_from, soc_to):
    rows = [(s_to[s_to.pid == pid], tag_to, 0, C_TO),
            (s_from[s_from.pid == pid], tag_from, 1, C_FROM)]

    for person, tag, y, _ in rows:
        person = person.sort_values("start_time")
        for _, r in person.iterrows():
            width = r["duration"] * 5 / 60
            ax_strip.barh(y, width, left=r["start_time"], height=0.52,
                          color=ACT_COLOURS.get(r["act_type"], "#cccccc"),
                          edgecolor=SURFACE, linewidth=1.2, zorder=3)
            if r["is_charging"] and r["charge_duration"] > 0:
                # The charging stripe spans only the part of the activity that was
                # actually charging, not the whole visit -- charge_duration is
                # usually shorter than the stay, and drawing the full bar would
                # overstate how long the charger was occupied.
                ax_strip.barh(y + 0.34, r["charge_duration"], left=r["start_time"],
                              height=0.14, color=CHARGE_MARK, zorder=4)
                kwh = (r["soc_end"] - r["soc_start"]) * BATTERY_KWH
                ax_strip.annotate(f"{kwh:.0f} kWh",
                                  (r["start_time"] + r["charge_duration"] / 2,
                                   y + 0.46),
                                  ha="center", va="bottom", fontsize=7.5,
                                  color=INK_2, zorder=5)

    ax_strip.set_yticks([0, 1])
    ax_strip.set_yticklabels([label_for(tag_to), label_for(tag_from)], fontsize=9)
    ax_strip.get_yticklabels()[0].set_color(C_TO)
    ax_strip.get_yticklabels()[1].set_color(C_FROM)
    ax_strip.set_ylim(-0.45, 1.75)
    ax_strip.set_title(f"…{pid.split('_')[-1]} — {note}   "
                       f"({n_like:,} people like this)",
                       fontsize=10, color=INK, loc="left", pad=6)

    traces = []
    for person, tag, colour in ((s_from[s_from.pid == pid], tag_from, C_FROM),
                                (s_to[s_to.pid == pid], tag_to, C_TO)):
        person = person.sort_values("start_time")
        # Step the SoC through each activity: it holds across the stay unless
        # charging, then steps down for the drive to the next one. A straight line
        # between activity midpoints would imply the battery drains while parked.
        t, v = [], []
        for _, r in person.iterrows():
            t += [r["start_time"], r["start_time"] + r["duration"] * 5 / 60]
            v += [r["soc_start"] * 100, r["soc_end"] * 100]
        traces.append((t, v))
        ax_soc.plot(t, v, color=colour, linewidth=2, label=label_for(tag), zorder=3,
                    solid_capstyle="round")

    # When nothing changed, the second line is drawn exactly over the first and the
    # panel silently shows one series where there are two. Redraw the earlier one
    # dashed on top and say so, rather than letting "identical" look like "missing".
    if len(traces) == 2 and traces[0] == traces[1]:
        ax_soc.plot(*traces[0], color=C_FROM, linewidth=2, zorder=4,
                    linestyle=(0, (4, 3)))
        ax_soc.annotate("identical in both scenarios", (23.7, 8), ha="right",
                        fontsize=8, color=INK_2, style="italic")

    ax_soc.set_ylim(0, 105)
    ax_soc.set_ylabel("SoC (%)", fontsize=9, color=INK_2)
    # Offset above/below the line rather than centred on it, or the line strikes
    # through its own label. Whichever series starts lower gets the label below.
    lower_first = soc_from <= soc_to
    ax_soc.annotate(f"starts at {soc_from:.0%}", (0.25, soc_from * 100),
                    textcoords="offset points",
                    xytext=(0, -11 if lower_first else 7),
                    fontsize=7.5, color=C_FROM,
                    va="top" if lower_first else "bottom")
    ax_soc.annotate(f"starts at {soc_to:.0%}", (0.25, soc_to * 100),
                    textcoords="offset points",
                    xytext=(0, 7 if lower_first else -11),
                    fontsize=7.5, color=C_TO,
                    va="bottom" if lower_first else "top")

    for ax in (ax_strip, ax_soc):
        ax.set_facecolor(SURFACE)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3))
        ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left" if ax is ax_strip else "_"):
            if side in ax.spines:
                ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            if ax.spines[side].get_visible():
                ax.spines[side].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=8, length=0)
    ax_strip.tick_params(labelbottom=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, dest="results_dir")
    ap.add_argument("--from", dest="tag_from", default="hc1")
    ap.add_argument("--to", dest="tag_to", default="hc30")
    ap.add_argument("--pids", nargs="*", default=None,
                    help="specific persons to draw (default: pick one of each "
                         "archetype automatically)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    s_from, l_from = load(args.results_dir, args.tag_from)
    s_to, l_to = load(args.results_dir, args.tag_to)

    if args.pids:
        picks = [(p, "requested", 0) for p in args.pids]
    else:
        picks = choose_examples(s_from, s_to, l_from, l_to)
    if not picks:
        sys.exit("No suitable example persons found.")

    soc_a = l_from.set_index("pid").initial_soc
    soc_b = l_to.set_index("pid").initial_soc

    n = len(picks)
    # Three rows per person: the activity strip, the SoC panel, and a blank spacer.
    # A single hspace cannot express this -- the strip and its SoC panel belong
    # together and should touch, while consecutive people need a gap or the next
    # person's title lands on the previous person's axis.
    fig = plt.figure(figsize=(12, 3.9 * n), facecolor=SURFACE)
    gs = fig.add_gridspec(3 * n, 1, height_ratios=[1.25, 1, 0.42] * n, hspace=0.0)
    axes = []
    for i, (pid, note, n_like) in enumerate(picks):
        ax_strip = fig.add_subplot(gs[3 * i])
        ax_soc = fig.add_subplot(gs[3 * i + 1], sharex=ax_strip)
        spacer = fig.add_subplot(gs[3 * i + 2])
        spacer.set_visible(False)
        draw_person(ax_strip, ax_soc, pid, note, n_like, s_from, s_to,
                    args.tag_from, args.tag_to,
                    float(soc_a.get(pid, np.nan)), float(soc_b.get(pid, np.nan)))
        axes += [ax_strip, ax_soc]
        if i < n - 1:
            ax_soc.tick_params(labelbottom=False)
    axes[-1].tick_params(labelbottom=True)
    axes[-1].set_xticks(range(0, 25, 3))
    axes[-1].set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)])
    axes[-1].set_xlabel("Time of day", color=INK_2, fontsize=10)

    shown = pd.concat([s_from[s_from.pid.isin([p for p, _, _ in picks])],
                       s_to[s_to.pid.isin([p for p, _, _ in picks])]])
    handles = [mpatches.Patch(color=ACT_COLOURS.get(t, "#cccccc"), label=t)
               for t in sorted(shown.act_type.unique())]
    handles.append(mpatches.Patch(color=CHARGE_MARK, label="charging"))
    handles += [plt.Line2D([], [], color=C_FROM, linewidth=2,
                           label=f"SoC at {label_for(args.tag_from)}"),
                plt.Line2D([], [], color=C_TO, linewidth=2,
                           label=f"SoC at {label_for(args.tag_to)}")]
    fig.legend(handles=handles, loc="lower center", ncol=min(len(handles), 7),
               fontsize=8.5, frameon=False, labelcolor=INK_2,
               bbox_to_anchor=(0.5, -0.004))

    fig.suptitle(f"The same people at {label_for(args.tag_from)} and "
                 f"{label_for(args.tag_to)} home-charger share",
                 color=INK, fontsize=13, fontweight="semibold", x=0.008,
                 ha="left", y=0.997)
    fig.text(0.008, 0.978,
             "Same activities, same seed, same error draws — only home-charger "
             "access differs. The violet stripe marks the charging part of a stay.",
             color=INK_2, fontsize=9.5, ha="left")

    # Filename keeps the tags: it identifies the run, it is not a label.
    out = args.out or (args.results_dir /
                       f"person_comparison_{args.tag_from}_{args.tag_to}.png")
    fig.tight_layout(rect=(0, 0.05, 1, 0.965))
    fig.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)

    print(f"Examples ({args.tag_from} → {args.tag_to}):")
    for pid, note, n_like in picks:
        print(f"  {pid}\n      {note}" + (f"  ({n_like:,} similar)" if n_like else ""))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
