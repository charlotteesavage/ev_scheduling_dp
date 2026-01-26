#!/usr/bin/env python3
from __future__ import annotations

import colorsys
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

if "MPLCONFIGDIR" not in os.environ:
    cache_dir = Path(__file__).resolve().parent.parent / ".matplotlib_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(cache_dir)

if "XDG_CACHE_HOME" not in os.environ:
    xdg_cache_dir = Path(__file__).resolve().parent.parent / ".cache"
    xdg_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CACHE_HOME"] = str(xdg_cache_dir)

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch, Rectangle


SCHEDULE_PATH = Path(
    "testing_latest/optimal_schedules/dylan/activities_with_charge_shop_errands_and_service_station_result_2026-01-26_22-18-58.csv"
)

TIME_INTERVAL_MINUTES = 5

REQUIRED_COLUMNS = {
    "act_type",
    "start_time",
    "duration",
    "soc_start",
    "soc_end",
    "is_charging",
    "x",
    "y",
}


DEFAULT_COLORS = {
    "home": "#0B4F8A",
    "work": "#6C757D",
    "education": "#6C757D",
    "shop/visit": "#F4D35E",
    "delivery/errands": "#8D99AE",
    "other/escort": "#F4A261",
    "depot/medical": "#B56576",
    "leisure": "#A8A8A8",
}


@dataclass(frozen=True)
class Segment:
    start_h: float
    end_h: float
    act_type: str
    is_charging: bool = False
    charge_h: float = 0.0


def _looks_like_schedule_csv(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("."):
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            header = f.readline().strip().lower()
    except OSError:
        return False
    return (
        "act_id" in header
        and "act_type" in header
        and "start_time" in header
        and "soc_start" in header
    )


def _color_for_act_type(act_type: str) -> str:
    act_type_norm = (act_type or "").strip().lower()
    if act_type_norm in DEFAULT_COLORS:
        return DEFAULT_COLORS[act_type_norm]
    digest = hashlib.md5(act_type_norm.encode("utf-8")).hexdigest()
    hue = (int(digest[:8], 16) % 360) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.85)
    return (r, g, b)


def _load_schedule(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")

    df = df.copy()
    df["start_time"] = pd.to_numeric(df["start_time"], errors="coerce")
    df["duration"] = pd.to_numeric(df["duration"], errors="coerce")
    df["soc_start"] = pd.to_numeric(df["soc_start"], errors="coerce")
    df["soc_end"] = pd.to_numeric(df["soc_end"], errors="coerce")
    df["is_charging"] = (
        pd.to_numeric(df["is_charging"], errors="coerce").fillna(0).astype(int)
    )
    if "charge_duration" in df.columns:
        df["charge_duration"] = pd.to_numeric(
            df["charge_duration"], errors="coerce"
        ).fillna(0.0)
    df = df.dropna(subset=["start_time", "duration"]).sort_values(
        "start_time", kind="mergesort"
    )

    # `start_time` is already in hours (e.g. 16.5833 ~= 16:35).
    # `duration` is a count of TIME_INTERVAL_MINUTES increments (e.g. 197 -> 197*5 minutes).
    duration_hours = df["duration"] * (TIME_INTERVAL_MINUTES / 60.0)
    df["end_time"] = df["start_time"] + duration_hours
    df["act_type"] = df["act_type"].astype(str)
    return df


def _build_segments(df: pd.DataFrame) -> list[Segment]:
    segments: list[Segment] = []
    df_rows = df.to_dict(orient="records")
    for row in df_rows:
        start = float(row["start_time"])
        end = float(row["end_time"])
        if end <= start:
            continue
        is_charging = bool(row["is_charging"])
        charge_h = float(row.get("charge_duration", 0.0)) if is_charging else 0.0
        charge_h = max(0.0, min(charge_h, end - start))
        segments.append(
            Segment(start, end, str(row["act_type"]), is_charging, charge_h)
        )

    return sorted(segments, key=lambda s: (s.start_h, s.end_h))


def _soc_polyline(
    df: pd.DataFrame,
) -> tuple[list[float], list[float], list[float], list[float]]:
    df = df.sort_values("start_time", kind="mergesort")
    xs: list[float] = []
    ys: list[float] = []
    ann_x: list[float] = []
    ann_y: list[float] = []

    prev_end: float | None = None
    prev_soc: float | None = None

    for _, row in df.iterrows():
        start = float(row["start_time"])
        end = float(row["end_time"])
        soc_start = float(row["soc_start"]) * 100.0
        soc_end = float(row["soc_end"]) * 100.0

        if not xs:
            xs.append(start)
            ys.append(soc_start)
            ann_x.append(start)
            ann_y.append(soc_start)
        else:
            if prev_end is not None and start > prev_end:
                xs.extend([prev_end, start])
                ys.extend([prev_soc if prev_soc is not None else soc_start, soc_start])
            else:
                xs.append(start)
                ys.append(soc_start)

        xs.append(end)
        ys.append(soc_end)

        prev_end = end
        prev_soc = soc_end

    if xs and ys:
        ann_x.append(xs[-1])
        ann_y.append(ys[-1])

    return xs, ys, ann_x, ann_y


def _annotate_first_last_soc(ax: plt.Axes, xs: list[float], ys: list[float]) -> None:
    if len(xs) < 2:
        return
    points = [(xs[0], ys[0]), (xs[-1], ys[-1])]
    for x, y in points:
        ax.annotate(
            f"{y:.1f}%",
            (x, y),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#1B4332",
        )


def main() -> int:
    if not _looks_like_schedule_csv(SCHEDULE_PATH):
        raise SystemExit(
            f"SCHEDULE_PATH does not look like a schedule CSV: {SCHEDULE_PATH}"
        )

    df = _load_schedule(SCHEDULE_PATH)
    segments = _build_segments(df)
    act_types = sorted({s.act_type.strip().lower() for s in segments})

    fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(13, 2.6), constrained_layout=True)

    for seg in segments:
        rect = Rectangle(
            (seg.start_h, 0),
            seg.end_h - seg.start_h,
            1.0,
            facecolor=_color_for_act_type(seg.act_type),
            edgecolor="none",
            linewidth=0.0,
            alpha=0.98,
        )
        ax.add_patch(rect)

        # If the activity includes charging, overlay a hatched bar for the charging time.
        if seg.charge_h > 0:
            charge_rect = Rectangle(
                (seg.start_h, 0),
                seg.charge_h,
                1.0,
                facecolor=(0, 0, 0, 0),
                edgecolor="#212529",
                linewidth=0.7,
                hatch="///",
            )
            ax.add_patch(charge_rect)

        width = seg.end_h - seg.start_h
        if width >= 0.4:
            ax.text(
                seg.start_h + width / 2.0,
                0.5,
                seg.act_type,
                ha="center",
                va="center",
                rotation=90,
                fontsize=8,
                color="#212529",
                clip_on=True,
            )

    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlim(0, 24)
    ax.set_xlabel("Time [h]")
    # ax.set_title(SCHEDULE_PATH.name, loc="left", fontsize=10)

    ax_soc = ax.twinx()
    xs, ys, _, _ = _soc_polyline(df)
    ax_soc.plot(xs, ys, color="#2D6A4F", linewidth=1.6, label="State of Charge")
    ax_soc.set_ylim(0, 100)
    ax_soc.set_ylabel("State of Charge (%)")
    _annotate_first_last_soc(ax_soc, xs, ys)

    legend_handles: list[Patch] = [
        Patch(facecolor=_color_for_act_type(a), label=a) for a in act_types
    ]
    legend_handles.append(
        Patch(
            facecolor="white", edgecolor="#212529", hatch="///", label="charging time"
        )
    )
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8, frameon=True)

    out_path = (
        SCHEDULE_PATH.with_suffix(SCHEDULE_PATH.suffix + ".png")
        if SCHEDULE_PATH.suffix
        else SCHEDULE_PATH.with_suffix(".png")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)

    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
