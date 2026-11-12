#!/usr/bin/env python3
"""
Compare home-charger scenarios: when during the day the population draws power.

  python3 testing_latest/plot_scenario_comparison.py --dir population_results/latest
  python3 testing_latest/plot_scenario_comparison.py --tags hc1 hc5 hc10 hc30 hc100

Produces three figures in the results directory:
  charging_load_profile.png   total charging power across the day, one line per scenario
  charging_load_by_mode.png   the same split by charger type, shared y-axis
  charging_scaling.png        how participation and daily energy scale with home-charger share

What the profile actually is
----------------------------
A schedule row does not carry a load curve; it carries a charging session -- an
activity that starts at `start_time` and charges for `charge_duration` hours. The
C layer charges one 5-minute interval at a time from the moment the activity
begins, at the charger's rated power, and stops when the battery is full
(scheduling.c, update_label_from_activity). So a session is reconstructed here as:
rated power for every interval, with the final interval carrying only the
remainder, which is what makes the reconstructed energy match the SoC the DP
reported. --check verifies that per scenario; it should agree to well under 1%.

Colour
------
The scenarios are ORDERED (1% -> 100% home chargers), not merely different, so
they get one hue stepped light-to-dark rather than five unrelated colours. Reading
the chart is then the same act as reading the scale: darker = more home chargers.
Validated as an ordinal ramp (monotone lightness, visible step gaps, light end
clears the surface at 2.06:1).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).parent.parent
DEFAULT_DIR = REPO / "testing_latest" / "population_results" / "latest"

BATTERY_KWH = 60.0
INTERVAL_H = 5 / 60.0
N_BINS = 288                      # 5-minute intervals in 24 hours

# Rated power by charge_mode, from get_charge_rate_and_price() in src/scheduling.c.
POWER_KW = {1: 7.0, 2: 22.0, 3: 50.0, 4: 7.0, 5: 22.0, 6: 50.0, 7: 7.0}
MODE_LABEL = {1: "Public slow (7 kW)", 2: "Fast (22 kW)", 3: "Rapid (50 kW)",
              4: "Free slow (7 kW)", 5: "Free fast (22 kW)",
              6: "Free rapid (50 kW)", 7: "Home private (7 kW)"}
# Which panels the by-mode figure gets, in this order. Free modes are folded into
# their paid equivalents -- they are the same hardware at a different tariff, and
# four panels of the same three powers would say less than three.
PANELS = [("Public slow (7 kW)", (1, 4)), ("Fast (22 kW)", (2, 5)),
          ("Rapid (50 kW)", (3, 6)), ("Home private (7 kW)", (7,))]

# Ordinal ramp, blue, light -> dark. Steps 250/350/450/550/700 of the sequential
# scale. Do not reorder: the darkest step must always be the largest share, or the
# chart contradicts its own legend.
RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]

# Categorical slots 1-4, for series that are DIFFERENT rather than ORDERED --
# home-charger access, and charger type. Fixed order, never cycled: a scenario
# dropping out must not repaint the ones that remain.
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]

# Text and furniture never wear a series colour.
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def ramp_for(n):
    """n evenly spaced steps of the ordinal ramp, endpoints included."""
    if n <= 1:
        return [RAMP[-2]]
    idx = np.linspace(0, len(RAMP) - 1, n).round().astype(int)
    return [RAMP[i] for i in idx]


def share_of(tag):
    """hc30 -> 30.0. Used only for ordering and axis values, never for colour."""
    m = re.search(r"(\d+(?:\.\d+)?)", tag)
    return float(m.group(1)) if m else float("nan")


def label_for(tag):
    """
    Display label for a scenario: hc30 -> "30%".

    Tags stay as tags everywhere they identify something -- filenames, dict keys,
    --tags arguments. They are only translated at the point of drawing, because
    "hc30" is a handle for the run and "30%" is what the reader needs.

    The pattern is anchored deliberately: a tag like `hc1_linked` would otherwise
    be relabelled "1%" and silently claim to be a scenario it is not, so anything
    that is not exactly hc<number> is shown unchanged.

    The percentage is the scenario's TARGET share. Realised shares differ slightly
    (hc5 is 4.888%, hc30 is 30.040%) because assignment is a per-person draw; the
    difference is well inside a label's precision and the target is what the
    scenario means.
    """
    m = re.fullmatch(r"hc(\d+(?:\.\d+)?)", tag)
    return f"{float(m.group(1)):g}%" if m else tag



# ── Initial state of charge ───────────────────────────────────────────────────

def soc_condition(manifests, logs):
    """
    One line describing the initial-SoC assumption these runs were made under.

    This must be DERIVED, never hardcoded: the same script draws these figures for
    the baseline directory and for the equal-start control directory, and a fixed
    caption would be correct in one and a lie in the other.

    Preference order:
      - the manifest's initial_soc, which records the DESIGN parameters (0.95);
      - failing that, the realised mean from the run log, which reads 0.945 rather
        than 0.95 because the draw is clamped at a full battery. Clamping is the
        right behaviour -- plenty of people do charge to full overnight -- but it
        makes the realised mean a poor label, so the manifest wins where it exists
        and the fallback says it was measured.

    Returns None if the scenarios disagree, rather than picking one of them.
    """
    designs = set()
    for m in manifests.values():
        soc = (m or {}).get("initial_soc")
        if soc:
            designs.add((round(soc["home_mean"], 4), round(soc["nohome_mean"], 4)))
    if len(designs) > 1:
        return None
    if designs:
        home, nohome = designs.pop()
        measured = ""
    else:
        pairs = set()
        for log in logs.values():
            if log is None or "initial_soc" not in log.columns:
                return None
            g = log.groupby("has_home_charger").initial_soc.mean()
            if 1 not in g.index:
                return None
            pairs.add((round(float(g[1]), 2), round(float(g[0]), 2)))
        if len(pairs) != 1:
            return None
        home, nohome = pairs.pop()
        measured = " (measured)"

    if abs(home - nohome) < 1e-9:
        return f"Initial SoC: {nohome:.0%} for everyone{measured}"
    return (f"Initial SoC: {home:.0%} for home-charger owners, "
            f"{nohome:.0%} for everyone else{measured}")


# ── Loading ───────────────────────────────────────────────────────────────────

def discover(results_dir: Path, tags=None):
    """Find scenarios in a results directory, ordered by home-charger share."""
    found = []
    for p in sorted(results_dir.glob("schedules_*.parquet")):
        tag = p.stem[len("schedules_"):]
        if tags and tag not in tags:
            continue
        found.append(tag)
    if tags:
        missing = [t for t in tags if t not in found]
        if missing:
            print(f"  note: no schedules file for {', '.join(missing)} — skipping")
    return sorted(found, key=share_of)


def load_scenario(results_dir: Path, tag: str, expect_params: str):
    sched = pd.read_parquet(
        results_dir / f"schedules_{tag}.parquet",
        columns=["pid", "start_time", "duration", "soc_start", "soc_end",
                 "is_charging", "charge_mode", "charge_duration"])
    manifest_path = results_dir / f"manifest_{tag}.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    # Parameter set governs the result more than any lever in these charts -- it
    # moved charging participation by a factor of three on an identical population.
    # Plotting two scenarios estimated under different coefficients side by side
    # would attribute that difference to the home-charger share.
    used = manifest.get("param_set")
    if expect_params and used and used != expect_params:
        sys.exit(f"{tag} was run with {used!r} parameters, not {expect_params!r}. "
                 "Comparing them would attribute a parameter change to the "
                 "home-charger lever. Re-run it, or pass --expect-params ''.")
    if expect_params and not used:
        print(f"  warning: {tag} has no manifest, so its parameter set is unknown")

    # Home-charger access is a property of the person, so it lives in the run log,
    # not on the schedule rows.
    log_path = results_dir / f"run_log_{tag}.csv"
    cols = ["pid", "has_home_charger", "initial_soc"]
    log = None
    if log_path.exists():
        have = set(pd.read_csv(log_path, nrows=0).columns)
        log = pd.read_csv(log_path, usecols=[c for c in cols if c in have])
    return sched, manifest, log


# ── The profile ───────────────────────────────────────────────────────────────

def load_profile(sched, modes=None):
    """
    Total charging power in each 5-minute bin of the day, in kW.

    Each session contributes its rated power to every interval it occupies, with
    the last interval carrying only the energy that remained -- so summing the
    profile back up returns the energy the DP actually delivered.
    """
    ch = sched[sched.is_charging == 1]
    if modes is not None:
        ch = ch[ch.charge_mode.isin(modes)]
    profile = np.zeros(N_BINS)
    if ch.empty:
        return profile

    power = ch.charge_mode.map(POWER_KW).to_numpy(dtype=float)
    start = ch.start_time.to_numpy(dtype=float)
    energy = ((ch.soc_end - ch.soc_start).to_numpy(dtype=float)) * BATTERY_KWH
    # Interval count from charge_duration, which the C layer increments once per
    # charged interval -- more reliable than dividing energy by power, which the
    # final capped interval would round down.
    k = np.maximum(np.rint(ch.charge_duration.to_numpy(dtype=float) / INTERVAL_H), 1
                   ).astype(int)

    full_energy = power * INTERVAL_H
    # Energy in each of the first k-1 intervals, then whatever is left.
    last = np.clip(energy - (k - 1) * full_energy, 0.0, full_energy)

    session = np.repeat(np.arange(len(ch)), k)
    offset = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
    is_last = offset == np.repeat(k - 1, k)

    e = np.where(is_last, last[session], full_energy[session])
    b = (np.floor(start[session] / INTERVAL_H).astype(int) + offset) % N_BINS
    np.add.at(profile, b, e)
    return profile / INTERVAL_H       # kWh per interval -> kW


def summarise(sched):
    persons = sched.pid.nunique()
    ch = sched[sched.is_charging == 1]
    kwh = (ch.soc_end - ch.soc_start) * BATTERY_KWH
    home = ch.charge_mode == 7
    return {
        "persons": persons,
        "pct_charging": 100 * ch.pid.nunique() / persons,
        "events": len(ch),
        "mwh": kwh.sum() / 1000,
        "mwh_home": kwh[home].sum() / 1000,
        "mwh_public": kwh[~home].sum() / 1000,
    }


# ── Figures ───────────────────────────────────────────────────────────────────

def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)


def hour_axis(ax):
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)])


def plot_profiles(profiles, colours, out_path, title, subtitle, soc_note=None):
    """
    Absolute load on top, change against the lowest scenario underneath.

    The absolute panel alone is not enough. Moving from 1% to 5% home chargers
    changes what 4% of the population does, so the curves sit almost on top of one
    another and the eye reads "no difference" when the difference is simply small
    relative to the total. The lower panel rescales to the difference itself, which
    is the quantity the comparison is actually about. Same x-axis, separate panels
    -- never a second y-axis on the same plot, which would invent crossing points
    that are artefacts of the scaling.
    """
    hours = np.arange(N_BINS) * INTERVAL_H
    tags = list(profiles)
    base_tag = tags[0]
    base = profiles[base_tag]

    if len(tags) > 1:
        fig, (ax, ax2) = plt.subplots(
            2, 1, figsize=(11, 7.4), facecolor=SURFACE, sharex=True,
            gridspec_kw={"height_ratios": [2.1, 1]})
    else:
        fig, ax = plt.subplots(figsize=(11, 5.2), facecolor=SURFACE)
        ax2 = None

    for (tag, prof), c in zip(profiles.items(), colours):
        ax.plot(hours, prof / 1000, color=c, linewidth=2, label=label_for(tag),
                zorder=3,
                solid_capstyle="round")
    style(ax)
    hour_axis(ax)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Charging load (MW)", color=INK_2, fontsize=10)
    ax.set_title(title, color=INK, fontsize=13, fontweight="semibold",
                 loc="left", pad=14)
    ax.text(0, 1.015, subtitle + (f" · {soc_note.lower()}" if soc_note else ""),
            transform=ax.transAxes, color=INK_2, fontsize=9.5)
    leg = ax.legend(title="Home-charger share", frameon=False, fontsize=9,
                    labelcolor=INK_2, loc="upper left")
    leg.get_title().set_color(INK_2)
    leg.get_title().set_fontsize(9)

    if ax2 is not None:
        for (tag, prof), c in zip(profiles.items(), colours):
            if tag == base_tag:
                continue
            ax2.plot(hours, (prof - base) / 1000, color=c, linewidth=2, zorder=3,
                     solid_capstyle="round")
        ax2.axhline(0, color=INK_2, linewidth=1, zorder=2)
        style(ax2)
        hour_axis(ax2)
        ax2.set_ylabel("Change (MW)", color=INK_2, fontsize=10)
        ax2.set_title(f"Change against {label_for(base_tag)}", color=INK, fontsize=11,
                      loc="left", pad=8)
        ax2.set_xlabel("Time of day", color=INK_2, fontsize=10)
    else:
        ax.set_xlabel("Time of day", color=INK_2, fontsize=10)

    fig.tight_layout()
    if ax2 is not None:
        # After tight_layout, not before: passing hspace through gridspec_kw makes
        # the two incompatible and matplotlib warns rather than honouring either.
        fig.subplots_adjust(hspace=0.28)
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def plot_by_mode(per_mode, colours, tags, out_path, soc_note=None):
    hours = np.arange(N_BINS) * INTERVAL_H
    fig, axes = plt.subplots(1, len(PANELS), figsize=(15, 4.2), sharey=True,
                             facecolor=SURFACE)
    # Shared y-axis on purpose: the point of the figure is that the panels are
    # different sizes. Free y-scales would draw a 0.2 MW panel and a 6 MW panel at
    # the same height and quietly invite the wrong conclusion.
    for ax, (label, modes) in zip(axes, PANELS):
        for tag, c in zip(tags, colours):
            ax.plot(hours, per_mode[tag][label] / 1000, color=c, linewidth=1.8,
                    zorder=3, solid_capstyle="round")
        style(ax)
        hour_axis(ax)
        ax.set_xticks(range(0, 25, 6))
        ax.set_xticklabels([f"{h:02d}" for h in range(0, 25, 6)])
        ax.set_title(label, color=INK, fontsize=10.5, loc="left", pad=8)
    axes[0].set_ylim(bottom=0)
    axes[0].set_ylabel("Charging load (MW)", color=INK_2, fontsize=10)
    axes[0].set_xlabel("Hour of day", color=INK_2, fontsize=10)
    handles = [plt.Line2D([], [], color=c, linewidth=2) for c in colours]
    leg = fig.legend(handles, [label_for(t) for t in tags],
                     title="Home-charger share", frameon=False,
                     fontsize=9, labelcolor=INK_2, loc="upper right",
                     bbox_to_anchor=(0.998, 0.99), ncol=1)
    leg.get_title().set_color(INK_2)
    leg.get_title().set_fontsize(9)
    fig.suptitle("Where the load sits, by charger type", color=INK, fontsize=13,
                 fontweight="semibold", x=0.006, ha="left", y=0.99)
    if soc_note:
        fig.text(0.006, 0.925, soc_note, color=INK_2, fontsize=9.5, ha="left")
    fig.tight_layout(rect=(0, 0, 0.87, 0.9))
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def plot_scaling(rows, out_path, soc_note=None):
    """
    How the totals move with the lever.

    Two panels rather than two y-axes on one: a percentage and an energy total
    share no scale, and overlaying them would invent a crossing point that is an
    artefact of the axis choice.
    """
    d = pd.DataFrame(rows).sort_values("share")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE)

    ax = axes[0]
    ax.plot(d.share, d.pct_charging, color=RAMP[3], linewidth=2, marker="o",
            markersize=8, markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    style(ax)
    # Headroom for the point labels. Without it the topmost annotation lands on the
    # panel title -- the labels are drawn in data space, the title is not.
    ax.set_ylim(0, max(d.pct_charging.max() * 1.25, 1e-6))
    ax.set_ylabel("Persons charging (%)", color=INK_2, fontsize=10)
    ax.set_xlabel("Home-charger share (%)", color=INK_2, fontsize=10)
    ax.set_title("Participation", color=INK, fontsize=11.5, loc="left", pad=10)
    for _, r in d.iterrows():
        ax.annotate(f"{r.pct_charging:.1f}%", (r.share, r.pct_charging),
                    textcoords="offset points", xytext=(0, 11), ha="center",
                    fontsize=8.5, color=INK_2)

    ax = axes[1]
    ax.plot(d.share, d.mwh_public, color=RAMP[2], linewidth=2, marker="o",
            markersize=8, markeredgecolor=SURFACE, markeredgewidth=2,
            label="Public", zorder=3)
    ax.plot(d.share, d.mwh_home, color=RAMP[4], linewidth=2, marker="s",
            markersize=8, markeredgecolor=SURFACE, markeredgewidth=2,
            label="Home", zorder=3)
    style(ax)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Energy delivered (MWh/day)", color=INK_2, fontsize=10)
    ax.set_xlabel("Home-charger share (%)", color=INK_2, fontsize=10)
    ax.set_title("Daily energy, by where it is drawn", color=INK, fontsize=11.5,
                 loc="left", pad=10)
    # Upper right: the home series sits on the floor at low shares and rises, so a
    # legend anywhere along the bottom would end up on top of the data it labels.
    leg = ax.legend(frameon=False, fontsize=9, labelcolor=INK_2, loc="upper right")
    ax.set_ylim(0, max(d.mwh_public.max(), d.mwh_home.max()) * 1.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def access_split(sched, log):
    """
    Per-scenario participation and energy, split by whether the person has a
    home charger.

    This is the figure that tests the lever rather than describing it. A home
    charger is supposed to act two ways: the owner starts the day near full (an
    initial condition), and can top up at home during the day (charge_mode 7). If
    the aggregate fall in charging is entirely the first effect, then non-owners'
    behaviour will not move at all between scenarios -- the population average
    falls only because more people have been RECLASSIFIED as owners, not because
    anyone behaves differently. Two series across four scenarios makes that
    visible: a flat "without" line is the signature of a purely compositional
    effect.
    """
    if log is None:
        return None
    charged = sched.loc[sched.is_charging == 1, "pid"].unique()
    kwh = (sched.loc[sched.is_charging == 1]
           .assign(kwh=lambda d: (d.soc_end - d.soc_start) * BATTERY_KWH)
           .groupby("pid").kwh.sum())
    home7 = (sched.loc[sched.charge_mode == 7]
             .assign(kwh=lambda d: (d.soc_end - d.soc_start) * BATTERY_KWH)
             .groupby("pid").kwh.sum())

    g = log.set_index("pid")
    g["charged"] = g.index.isin(charged)
    g["kwh"] = kwh.reindex(g.index).fillna(0.0)
    g["kwh_home"] = home7.reindex(g.index).fillna(0.0)

    out = {}
    for has, label in ((1, "With home charger"), (0, "Without")):
        sub = g[g.has_home_charger == has]
        out[label] = {
            "n": len(sub),
            "pct": 100 * sub.charged.mean() if len(sub) else 0.0,
            "kwh_per_person": sub.kwh.mean() if len(sub) else 0.0,
            "kwh_home_share": (100 * sub.kwh_home.sum() / sub.kwh.sum()
                               if sub.kwh.sum() else 0.0),
        }
    return out


def plot_access_split(splits, tags, out_path, soc_note=None):
    labels = ["With home charger", "Without"]
    x = np.arange(len(tags))
    width = 0.38
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), facecolor=SURFACE)

    for ax, (key, ylab, fmt, title) in zip(axes, [
            ("pct", "Persons charging (%)", "{:.1f}%", "Who charges"),
            ("kwh_per_person", "Energy per person (kWh/day)", "{:.1f}",
             "How much they charge")]):
        for i, (label, colour) in enumerate(zip(labels, CAT)):
            vals = [splits[t][label][key] for t in tags]
            # 2px surface gap between adjacent bars, and a rounded data end.
            bars = ax.bar(x + (i - 0.5) * width, vals, width * 0.94, color=colour,
                          edgecolor=SURFACE, linewidth=2, zorder=3, label=label)
            # Direct labels on every bar: two of the categorical slots sit below
            # 3:1 against the surface, which obliges visible labels rather than
            # identity by colour alone.
            for b, v in zip(bars, vals):
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, v),
                            textcoords="offset points", xytext=(0, 4),
                            ha="center", fontsize=8.5, color=INK_2)
        style(ax)
        ax.set_xticks(x)
        ax.set_xticklabels([label_for(t) for t in tags])
        ax.set_ylim(0, max(splits[t][l][key] for t in tags for l in labels) * 1.25)
        ax.set_ylabel(ylab, color=INK_2, fontsize=10)
        ax.set_xlabel("Home-charger share", color=INK_2, fontsize=10)
        ax.set_title(title, color=INK, fontsize=11.5, loc="left", pad=10)

    leg = axes[0].legend(frameon=False, fontsize=9, labelcolor=INK_2,
                         loc="upper right")
    fig.suptitle("Does a home charger change behaviour, or just the label?",
                 color=INK, fontsize=13, fontweight="semibold", x=0.008,
                 ha="left", y=0.99)
    if soc_note:
        fig.text(0.008, 0.925, soc_note, color=INK_2, fontsize=9.5, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


def plot_energy_mix(mixes, tags, out_path, soc_note=None):
    """
    Daily energy by charger type: absolute on the left, share on the right.

    Bars, not a stacked area. The scenarios are ordered but not evenly spaced
    (1, 5, 10, 30), and an area would draw a straight line between 10% and 30%
    that asserts something about 20% which was never run.

    The share panel earns its place because the total is falling: without it, a
    segment shrinking in the absolute panel cannot be told apart from the whole
    bar shrinking around a constant mix.
    """
    x = np.arange(len(tags))
    names = [label for label, _ in PANELS]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8), facecolor=SURFACE)

    for ax, normalise in zip(axes, (False, True)):
        bottom = np.zeros(len(tags))
        totals = np.array([sum(mixes[t][n] for n in names) for t in tags])
        for name, colour in zip(names, CAT):
            vals = np.array([mixes[t][name] for t in tags], dtype=float)
            plotted = 100 * vals / np.where(totals > 0, totals, 1) if normalise else vals
            ax.bar(x, plotted, 0.62, bottom=bottom, color=colour,
                   edgecolor=SURFACE, linewidth=2, zorder=3, label=name)
            # Label a segment only where there is room for the text -- a number on
            # every segment would be unreadable on the thin ones and is not what
            # the reader is looking for anyway.
            for xi, (v, b) in enumerate(zip(plotted, bottom)):
                if v > (6 if normalise else 0.06 * max(totals.max(), 1)):
                    ax.annotate(f"{v:.0f}%" if normalise else f"{v:.0f}",
                                (xi, b + v / 2), ha="center", va="center",
                                fontsize=8.5, color="#ffffff", zorder=4)
            bottom += plotted
        style(ax)
        ax.set_xticks(x)
        ax.set_xticklabels([label_for(t) for t in tags])
        ax.set_xlabel("Home-charger share", color=INK_2, fontsize=10)
        if normalise:
            ax.set_ylim(0, 100)
            ax.set_ylabel("Share of daily energy (%)", color=INK_2, fontsize=10)
            ax.set_title("Mix", color=INK, fontsize=11.5, loc="left", pad=10)
        else:
            ax.set_ylim(0, totals.max() * 1.16)
            ax.set_ylabel("Energy delivered (MWh/day)", color=INK_2, fontsize=10)
            ax.set_title("Total", color=INK, fontsize=11.5, loc="left", pad=10)
            for xi, t in enumerate(totals):
                ax.annotate(f"{t:.0f} MWh", (xi, t), textcoords="offset points",
                            xytext=(0, 5), ha="center", fontsize=9, color=INK)

    handles, lab = axes[0].get_legend_handles_labels()
    leg = fig.legend(handles, lab, frameon=False, fontsize=9, labelcolor=INK_2,
                     loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.015))
    fig.suptitle("Where the day's energy is drawn, as home charging spreads",
                 color=INK, fontsize=13, fontweight="semibold", x=0.008,
                 ha="left", y=0.99)
    if soc_note:
        fig.text(0.008, 0.925, soc_note, color=INK_2, fontsize=9.5, ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 0.90))
    fig.savefig(out_path, dpi=170, facecolor=SURFACE)
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, dest="results_dir")
    ap.add_argument("--tags", nargs="*", default=None,
                    help="scenario tags to include (default: every one found)")
    ap.add_argument("--expect-params", default="oasis_t12",
                    help="refuse scenarios run with other parameters "
                         "(default: %(default)s; '' to allow any)")
    ap.add_argument("--check", action="store_true",
                    help="verify the reconstructed profile conserves energy")
    args = ap.parse_args()

    tags = discover(args.results_dir, args.tags)
    if not tags:
        sys.exit(f"No schedules_*.parquet in {args.results_dir}")

    print(f"Scenarios: {', '.join(tags)}\n")
    profiles, per_mode, rows = {}, {}, []
    splits, mixes = {}, {}
    manifests, logs = {}, {}
    for tag in tags:
        sched, manifest, log = load_scenario(args.results_dir, tag, args.expect_params)
        manifests[tag], logs[tag] = manifest, log
        profiles[tag] = load_profile(sched)
        per_mode[tag] = {label: load_profile(sched, modes)
                         for label, modes in PANELS}
        s = summarise(sched)
        s["tag"] = tag
        s["share"] = share_of(tag)
        rows.append(s)

        split = access_split(sched, log)
        if split is not None:
            splits[tag] = split
        ch = sched[sched.is_charging == 1]
        kwh = (ch.soc_end - ch.soc_start) * BATTERY_KWH
        mixes[tag] = {label: kwh[ch.charge_mode.isin(modes)].sum() / 1000
                      for label, modes in PANELS}

        peak_kw = profiles[tag].max()
        peak_at = int(profiles[tag].argmax()) * INTERVAL_H
        print(f"  {tag:6} {s['persons']:7,} persons | {s['pct_charging']:5.2f}% charging | "
              f"{s['mwh']:6.1f} MWh/day | peak {peak_kw/1000:5.2f} MW at "
              f"{int(peak_at):02d}:{int(peak_at % 1 * 60):02d}")

        if split is not None:
            w, wo = split["With home charger"], split["Without"]
            print(f"         with home charger: {w['pct']:5.2f}% charge "
                  f"({w['n']:,} persons, {w['kwh_home_share']:.1f}% of their "
                  f"energy from home) | without: {wo['pct']:5.2f}% "
                  f"({wo['n']:,} persons)")

        if args.check:
            from_profile = profiles[tag].sum() * INTERVAL_H / 1000
            gap = abs(from_profile - s["mwh"]) / max(s["mwh"], 1e-9)
            print(f"         energy check: profile {from_profile:.2f} MWh vs "
                  f"schedules {s['mwh']:.2f} MWh  ({gap:.3%} apart)")

    colours = ramp_for(len(tags))
    out = args.results_dir

    # Derived from these runs, not assumed: this same code draws the baseline and
    # the equal-start figures, and the two differ only in this line.
    soc_note = soc_condition(manifests, logs)
    if soc_note:
        print(f"\n{soc_note}")
    else:
        print("\n  warning: scenarios disagree on their initial-SoC setting, or it "
              "is not recorded — figures will carry no SoC caption")

    plot_profiles(profiles, colours, out / "charging_load_profile.png",
                  "Population charging load across the day",
                  f"{rows[0]['persons']:,} persons · darker = more home chargers",
                  soc_note)
    plot_by_mode(per_mode, colours, tags, out / "charging_load_by_mode.png",
                 soc_note)
    if len(rows) > 1:
        plot_scaling(rows, out / "charging_scaling.png", soc_note)
    if len(splits) == len(tags) and tags:
        plot_access_split(splits, tags, out / "charging_by_access.png", soc_note)
    plot_energy_mix(mixes, tags, out / "charging_energy_mix.png", soc_note)

    written = ["charging_load_profile.png", "charging_load_by_mode.png",
               "charging_energy_mix.png"]
    if len(rows) > 1:
        written.append("charging_scaling.png")
    if len(splits) == len(tags) and tags:
        written.append("charging_by_access.png")
    else:
        print("\n  note: charging_by_access.png needs a run_log_<tag>.csv for "
              "every scenario (that is where has_home_charger lives)")
    print("\nWrote:")
    for f in sorted(written):
        print(f"  {out / f}")


if __name__ == "__main__":
    main()
