"""
Visualise population-scale DP schedule results.

Produces three figures saved to testing_latest/population_results/:
  1. schedule_gantt.png     – Gantt charts for a sample of persons
  2. activity_heatmap.png   – fraction of population at each activity type by hour
  3. soc_profile.png        – SOC distribution across persons over the day
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

RESULTS_DIR = Path(__file__).parent / "population_results"
# Prefer the parquet the runner writes when pyarrow is available; fall back to CSV.
# Hardcoding schedules.csv meant a stale CSV from an earlier run could be plotted
# silently while the current run wrote schedules.parquet next to it.
_PARQUET    = RESULTS_DIR / "schedules.parquet"
_CSV        = RESULTS_DIR / "schedules.csv"
SCHED_PATH  = _PARQUET if _PARQUET.exists() else _CSV

# Consistent colour palette for activity types
ACT_COLOURS = {
    "home":              "#4e79a7",
    "work":              "#f28e2b",
    "shop":              "#59a14f",
    "visit":             "#76b7b2",
    "other":             "#b07aa1",
    "education":         "#e15759",
    "depot":             "#ff9da7",
    "medical":           "#9c755f",
    "pt interaction":    "#bab0ac",
    "business":          "#edc948",
    "escort_education":  "#d37295",
    "escort_shop":       "#fabfd2",
    "escort_home":       "#8cd17d",
    "escort_other":      "#a0cbe8",
    "escort_work":       "#ffbe7d",
    "delivery":          "#499894",
    "service_station":   "#86bcb6",
}


def load_schedules():
    df = pd.read_parquet(SCHED_PATH) if SCHED_PATH.suffix == ".parquet" else pd.read_csv(SCHED_PATH)
    df["end_h"] = df["start_time"] + df["duration"] * 5 / 60
    return df


# ── Figure 1: Gantt charts ────────────────────────────────────────────────────

def plot_gantt(df, n_persons=15, seed=42):
    pids = df["pid"].unique()
    rng  = np.random.default_rng(seed)

    # Prefer persons with at least one non-home activity
    has_nonhome = df[df["act_type"] != "home"]["pid"].unique()
    mixed = [p for p in pids if p in has_nonhome]
    pool  = mixed if len(mixed) >= n_persons else list(pids)
    chosen = rng.choice(pool, size=min(n_persons, len(pool)), replace=False)

    ncols = 3
    nrows = int(np.ceil(len(chosen) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(18, nrows * 1.8),
                             sharex=True, constrained_layout=True)
    axes = np.array(axes).flatten()

    for ax, pid in zip(axes, chosen):
        person = df[df["pid"] == pid].copy()
        for _, row in person.iterrows():
            colour = ACT_COLOURS.get(row["act_type"], "#cccccc")
            ax.barh(0, row["end_h"] - row["start_time"],
                    left=row["start_time"], height=0.6,
                    color=colour, edgecolor="white", linewidth=0.5)
            # Mark charging sessions with a gold stripe above the bar
            if row["is_charging"] and row["charge_duration"] > 0:
                ax.barh(0.23, row["end_h"] - row["start_time"],
                        left=row["start_time"], height=0.15,
                        color="gold", alpha=0.9)

        # SOC as line on twin axis
        ax2 = ax.twinx()
        mid_h = person["start_time"] + person["duration"] * 5 / 120
        ax2.plot(mid_h, person["soc_end"] * 100, "k--", linewidth=0.8, alpha=0.6)
        ax2.set_ylim(0, 110)
        ax2.set_yticks([0, 50, 100])
        ax2.tick_params(axis="y", labelsize=5, colors="grey")
        if ax != axes[ncols - 1] and ax != axes[-1]:
            ax2.set_yticklabels([])

        ax.set_xlim(0, 24)
        ax.set_yticks([])
        ax.set_xticks(range(0, 25, 6))
        ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 6)], fontsize=7)
        short_pid = pid.split("_")[-1]
        ax.set_title(f"…{short_pid}", fontsize=7, pad=2)
        ax.grid(axis="x", linestyle=":", linewidth=0.4, alpha=0.5)

    # Hide unused panels
    for ax in axes[len(chosen):]:
        ax.set_visible(False)

    # Legend
    present_types = df[df["pid"].isin(chosen)]["act_type"].unique()
    handles = [mpatches.Patch(color=ACT_COLOURS.get(t, "#cccccc"), label=t)
               for t in sorted(present_types)]
    handles.append(mpatches.Patch(color="gold", label="charging"))
    fig.legend(handles=handles, loc="lower center", ncol=min(len(handles), 6),
               fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.02))

    fig.suptitle("Sample schedules  (dashed line = SOC, gold bar = charging)",
                 fontsize=11, y=1.01)
    out = RESULTS_DIR / "schedule_gantt.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ── Figure 2: Activity heatmap by hour ────────────────────────────────────────

def plot_heatmap(df):
    """Fraction of population doing each activity type at each 15-min slot."""
    TIME_INTERVAL = 5   # minutes
    n_slots   = 24 * 60 // TIME_INTERVAL  # 288
    slot_mins = np.arange(n_slots) * TIME_INTERVAL

    pids      = df["pid"].unique()
    act_types = [t for t in ACT_COLOURS if t in df["act_type"].values]

    # Build (n_persons × n_slots) activity-type matrix
    occ = {t: np.zeros(n_slots) for t in act_types}
    for _, row in df.iterrows():
        s = max(0, int(row["start_time"] * 60 / TIME_INTERVAL))
        e = min(n_slots, int(row["end_h"] * 60 / TIME_INTERVAL))
        t = row["act_type"]
        if t in occ:
            occ[t][s:e] += 1

    total = len(pids)
    frac  = {t: occ[t] / total for t in act_types}

    # Stack areas: non-home on top for clarity
    order = ["work", "shop", "visit", "other", "medical", "education",
             "pt interaction", "business", "depot",
             "escort_education", "escort_shop", "escort_home",
             "escort_other", "escort_work", "delivery", "service_station",
             "home"]
    order = [t for t in order if t in frac]

    hours = slot_mins / 60
    fig, ax = plt.subplots(figsize=(14, 5))
    bottom = np.zeros(n_slots)
    for t in order:
        vals = frac[t]
        if vals.max() < 0.01:
            continue
        ax.fill_between(hours, bottom, bottom + vals,
                        color=ACT_COLOURS.get(t, "#cccccc"),
                        label=t, alpha=0.9, linewidth=0)
        bottom += vals

    ax.set_xlim(0, 24)
    ax.set_ylim(0, 1)
    ax.set_xticks(range(0, 25, 2))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 2)], fontsize=9)
    ax.set_ylabel("Fraction of population", fontsize=10)
    ax.set_xlabel("Hour of day", fontsize=10)
    ax.set_title(f"Activity distribution over the day  (n={total} persons)", fontsize=11)
    ax.grid(axis="y", linestyle=":", linewidth=0.4, alpha=0.5)

    handles = [mpatches.Patch(color=ACT_COLOURS.get(t, "#ccc"), label=t)
               for t in order if frac[t].max() >= 0.01]
    ax.legend(handles=handles, loc="upper right", ncol=2, fontsize=8, frameon=True)

    out = RESULTS_DIR / "activity_heatmap.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ── Figure 3: SOC profile over the day ───────────────────────────────────────

def plot_soc_profile(df):
    """Median + IQR of SOC across all persons at each hour."""
    TIME_INTERVAL = 5
    n_slots = 24 * 60 // TIME_INTERVAL
    pids    = df["pid"].unique()

    soc_matrix = np.full((len(pids), n_slots), np.nan)
    for pi, pid in enumerate(pids):
        person = df[df["pid"] == pid]
        for _, row in person.iterrows():
            s = max(0, int(row["start_time"] * 60 / TIME_INTERVAL))
            e = min(n_slots, int(row["end_h"] * 60 / TIME_INTERVAL))
            # Linear interpolation of SOC from soc_start to soc_end
            soc_matrix[pi, s:e] = np.linspace(row["soc_start"], row["soc_end"],
                                               max(1, e - s))

    hours   = np.arange(n_slots) * TIME_INTERVAL / 60
    median  = np.nanmedian(soc_matrix, axis=0) * 100
    p25     = np.nanpercentile(soc_matrix, 25, axis=0) * 100
    p75     = np.nanpercentile(soc_matrix, 75, axis=0) * 100
    p10     = np.nanpercentile(soc_matrix, 10, axis=0) * 100
    p90     = np.nanpercentile(soc_matrix, 90, axis=0) * 100

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(hours, p10, p90, alpha=0.15, color="#4e79a7", label="10–90th pct")
    ax.fill_between(hours, p25, p75, alpha=0.35, color="#4e79a7", label="IQR (25–75th)")
    ax.plot(hours, median, color="#4e79a7", linewidth=2, label="Median SOC")
    ax.axhline(30, color="red", linestyle="--", linewidth=0.8, alpha=0.7,
               label="SOC anxiety threshold (30%)")

    ax.set_xlim(0, 24)
    ax.set_ylim(0, 105)
    ax.set_xticks(range(0, 25, 2))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 2)], fontsize=9)
    ax.set_ylabel("State of Charge (%)", fontsize=10)
    ax.set_xlabel("Hour of day", fontsize=10)
    ax.set_title(f"SOC profile over the day  (n={len(pids)} persons)", fontsize=11)
    ax.legend(fontsize=9, frameon=True)
    ax.grid(axis="y", linestyle=":", linewidth=0.4, alpha=0.5)

    out = RESULTS_DIR / "soc_profile.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    df = load_schedules()
    print(f"Loaded {len(df)} schedule rows for {df['pid'].nunique()} persons")
    plot_gantt(df)
    plot_heatmap(df)
    plot_soc_profile(df)
    print("Done.")
