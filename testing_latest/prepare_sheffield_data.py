"""
Prepares Sheffield activity data for population-scale DP runs.

Reads:
  Sheffield_Project_model_input/location_expansion/activities_long_with_groups_selected.csv
  Sheffield_Project_model_input/desired_start_time_and_duration_distibution/
      persons_home_depot_with_start_duration_draws.csv
  Sheffield_Project_model_input/charging location/charger_location_attributes_dropped_final.csv

Writes:
  testing_latest/sheffield_activities_prepared.parquet   (or .csv if pyarrow unavailable)

Run once before population-scale DP runs.

Output schema (one row per person-activity, including dawn and dusk sentinels):
  pid, id, act_type, x, y, group,
  earliest_start, latest_start, min_duration, max_duration,
  des_start_time, des_duration,
  charge_mode, is_charging, is_service_station
"""

import argparse
import hashlib
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import KDTree

# ── Configuration ────────────────────────────────────────────────────────────
CHARGER_THRESHOLD_M = 250  # activities within this distance of a charger get charging
HOME_CHARGE_MODE = 1  # 1 = slow (7 kW) home charging where available
HORIZON = 288  # 5-min intervals in 24 hours

# Share of the population with a home charger (1.0 = universal, the old behaviour).
# Home charging is a property of the dwelling, so it is assigned per PERSON and is
# constant across all of that person's home activities.
HOME_CHARGING_SHARE = 1.0
HOME_CHARGING_SEED = 20260726


def stable_unit_interval(keys, seed):
    """
    Map each key to a reproducible float in [0, 1).

    Python's built-in hash() is salted per process (PYTHONHASHSEED), so it gives a
    different answer on every run and must not be used for anything that needs to
    hold still between scenario runs. A counterfactual is only interpretable if the
    same person gets the same draw in baseline and intervention, so the mapping has
    to be a pure function of (key, seed).
    """
    out = np.empty(len(keys), dtype=np.float64)
    salt = str(seed).encode()
    for i, k in enumerate(keys):
        digest = hashlib.blake2b(salt + b"|" + str(k).encode(), digest_size=8).digest()
        out[i] = int.from_bytes(digest, "big") / 2**64
    return out


def assign_home_charging(pids, share, seed):
    """
    Decide which persons have a home charger.

    Returns a DataFrame [pid, has_home_charger]. Assignment is per person and
    reproducible, so scenarios that vary `share` are nested: lowering the share only
    ever removes chargers from people who had one at the higher share, it never
    reshuffles who has one. That keeps the difference between two runs attributable
    to the lever rather than to a new random draw.
    """
    pids = pd.Index(pids).unique()
    if share >= 1.0:
        flags = np.ones(len(pids), dtype=int)
    elif share <= 0.0:
        flags = np.zeros(len(pids), dtype=int)
    else:
        flags = (stable_unit_interval(pids, seed) < share).astype(int)
    return pd.DataFrame({"pid": pids, "has_home_charger": flags})

REPO_ROOT = Path(__file__).parent.parent
DATA_ROOT = REPO_ROOT / "Sheffield_Project_model_input"
OUT_STEM = REPO_ROOT / "testing_latest" / "sheffield_activities_prepared"

ACTS_PATH = (
    DATA_ROOT / "location_expansion" / "activities_long_with_groups_selected.csv"
)
PERSONS_PATH = (
    DATA_ROOT
    / "desired_start_time_and_duration_distibution"
    / "persons_home_depot_with_start_duration_draws.csv"
)
CHARGERS_PATH = (
    DATA_ROOT / "charging location" / "charger_location_attributes_dropped_final.csv"
)


# ── Coordinate conversion ─────────────────────────────────────────────────────


def wgs84_to_bng(lat_deg, lon_deg):
    """
    Vectorized WGS84 lat/lon → OSGB36 BNG easting/northing (metres).
    Uses the OS Helmert + Transverse Mercator algorithm; ~5 m accuracy for UK.
    No external dependencies beyond numpy.
    """
    lat = np.radians(np.asarray(lat_deg, dtype=np.float64))
    lon = np.radians(np.asarray(lon_deg, dtype=np.float64))

    # WGS84 ellipsoid → Cartesian
    a_w, b_w = 6378137.0, 6356752.3142
    e2_w = 1.0 - (b_w / a_w) ** 2
    nu_w = a_w / np.sqrt(1.0 - e2_w * np.sin(lat) ** 2)
    X = nu_w * np.cos(lat) * np.cos(lon)
    Y = nu_w * np.cos(lat) * np.sin(lon)
    Z = nu_w * (1.0 - e2_w) * np.sin(lat)

    # Helmert transformation: WGS84 → OSGB36
    tx, ty, tz = -446.448, 125.157, -542.060  # metres
    rx = np.radians(-0.1502 / 3600.0)  # arcsec → rad
    ry = np.radians(-0.2470 / 3600.0)
    rz = np.radians(-0.8421 / 3600.0)
    s = 20.4894e-6
    X2 = tx + (1 + s) * X - rz * Y + ry * Z
    Y2 = ty + rz * X + (1 + s) * Y - rx * Z
    Z2 = tz - ry * X + rx * Y + (1 + s) * Z

    # OSGB36 / Airy 1830 ellipsoid: Cartesian → lat/lon
    a, b = 6377563.396, 6356256.909
    e2 = 1.0 - (b / a) ** 2
    lon2 = np.arctan2(Y2, X2)
    p = np.sqrt(X2**2 + Y2**2)
    lat2 = np.arctan2(Z2, p * (1.0 - e2))
    for _ in range(10):
        nu2 = a / np.sqrt(1.0 - e2 * np.sin(lat2) ** 2)
        lat2 = np.arctan2(Z2 + e2 * nu2 * np.sin(lat2), p)

    # Transverse Mercator → BNG
    F0 = 0.9996012717
    E0, N0 = 400000.0, -100000.0
    phi0, lam0 = np.radians(49.0), np.radians(-2.0)
    n = (a - b) / (a + b)
    nu_p = a * F0 / np.sqrt(1.0 - e2 * np.sin(lat2) ** 2)
    rho = a * F0 * (1.0 - e2) / (1.0 - e2 * np.sin(lat2) ** 2) ** 1.5
    eta2 = nu_p / rho - 1.0
    t = np.tan(lat2)
    c = np.cos(lat2)
    sl = np.sin(lat2)

    M = (
        b
        * F0
        * (
            (1 + n + 5 / 4 * n**2 + 5 / 4 * n**3) * (lat2 - phi0)
            - (3 * n + 3 * n**2 + 21 / 8 * n**3)
            * np.sin(lat2 - phi0)
            * np.cos(lat2 + phi0)
            + (15 / 8 * n**2 + 15 / 8 * n**3)
            * np.sin(2 * (lat2 - phi0))
            * np.cos(2 * (lat2 + phi0))
            - 35 / 24 * n**3 * np.sin(3 * (lat2 - phi0)) * np.cos(3 * (lat2 + phi0))
        )
    )
    dl = lon2 - lam0
    N_coord = (
        N0
        + M
        + (nu_p / 2) * sl * c * dl**2
        + (nu_p / 24) * sl * c**3 * (5 - t**2 + 9 * eta2) * dl**4
        + (nu_p / 720) * sl * c**5 * (61 - 58 * t**2 + t**4) * dl**6
    )
    E_coord = (
        E0
        + nu_p * c * dl
        + (nu_p / 6) * c**3 * (nu_p / rho - t**2) * dl**3
        + (nu_p / 120)
        * c**5
        * (5 - 18 * t**2 + t**4 + 14 * eta2 - 58 * t**2 * eta2)
        * dl**5
    )
    return E_coord, N_coord


# ── Charger spatial index ─────────────────────────────────────────────────────


def build_charger_tree(path):
    """Load charger CSV, project to BNG, return (KDTree, power_array)."""
    print("Loading chargers...")
    df = pd.read_csv(path)
    df = df.dropna(subset=["lat", "lon", "power_range_max"])
    e, n = wgs84_to_bng(df["lat"].values, df["lon"].values)
    tree = KDTree(np.column_stack([e, n]))
    return tree, df["power_range_max"].values


def assign_charging(activities, tree, powers, threshold_m, home_chargers=None):
    """
    Spatial join: set charge_mode and is_charging for each unique (x, y).

    Home activities get HOME_CHARGE_MODE regardless of charger proximity, but only
    for persons who have a home charger. `home_chargers` is a DataFrame
    [pid, has_home_charger]; passing None keeps the old universal behaviour.
    """
    unique = activities[["x", "y"]].drop_duplicates().copy()
    coords = unique[["x", "y"]].values

    print(
        f"Querying {len(unique):,} unique locations against {len(powers):,} chargers..."
    )
    dists, idxs = tree.query(coords, k=1, workers=-1)

    power = powers[idxs]
    mode = np.where(power <= 7.0, 1, np.where(power <= 22.0, 2, 3))
    mode = np.where(dists <= threshold_m, mode, 0).astype(int)

    unique["charge_mode"] = mode
    unique["is_charging"] = (mode > 0).astype(int)

    result = activities.merge(unique, on=["x", "y"], how="left")

    home_mask = result["act_type"] == "home"
    if home_chargers is not None:
        result = result.merge(home_chargers, on="pid", how="left")
        result["has_home_charger"] = result["has_home_charger"].fillna(0).astype(int)
        home_mask &= result["has_home_charger"] == 1
    result.loc[home_mask, "charge_mode"] = HOME_CHARGE_MODE
    result.loc[home_mask, "is_charging"] = 1

    return result


# ── Desired start time / duration ─────────────────────────────────────────────


def join_desired_times(activities, persons):
    """
    Join des_start_time and des_duration from the persons draws table.
    Both are in 5-minute intervals (same units as activity time fields).
    Multiple activities of the same group get the same draw — this reflects
    a single preferred time for each activity type per person.
    """
    groups = range(1, 9)

    starts = persons[["pid"] + [f"start_g{g}" for g in groups]].melt(
        id_vars="pid", var_name="g_col", value_name="des_start_time"
    )
    starts["group"] = starts["g_col"].str.replace("start_g", "").astype(int)
    starts = starts.drop(columns="g_col")

    durs = persons[["pid"] + [f"dur_g{g}" for g in groups]].melt(
        id_vars="pid", var_name="g_col", value_name="des_duration"
    )
    durs["group"] = durs["g_col"].str.replace("dur_g", "").astype(int)
    durs = durs.drop(columns="g_col")

    time_draws = starts.merge(durs, on=["pid", "group"])

    activities = activities.merge(time_draws, on=["pid", "group"], how="left")
    activities["des_start_time"] = activities["des_start_time"].fillna(0).astype(int)
    activities["des_duration"] = activities["des_duration"].fillna(0).astype(int)
    return activities


# ── Dawn / dusk sentinels ─────────────────────────────────────────────────────


def add_dawn_dusk(activities, persons):
    """
    Prepend a dawn row (id=0, latest_start=0) and append a dusk row
    (id=last, latest_start=HORIZON-2) per person, using home coordinates
    from the persons table.

    Neither sentinel charges. Overnight charging is represented by the initial SoC
    draw in initialise_SOC() on the C side, so charging at dusk as well would both
    double-count it and — because duplicate_for_choice skips sentinels — force it on
    every person, which is exactly the exogenous rule this model exists to replace.

    If dusk charging is wanted later it has to become a genuine choice (a no-charge
    twin), and that is not a data-only change: main.c terminates the DP at
    bucket[horizon-1][max_num_activities-1], so a second dusk row would be
    unreachable as a terminal state until that is generalised.
    """
    home = persons[persons["pid"].isin(activities["pid"].unique())][
        ["pid", "x", "y"]
    ].copy()

    def _sentinel(df, is_dawn):
        out = df.copy()
        out["act_type"] = "home"
        out["group"] = 1
        out["is_service_station"] = 0
        out["des_start_time"] = 0
        out["des_duration"] = 0
        out["activity_idx"] = np.nan
        out["_sentinel"] = "dawn" if is_dawn else "dusk"
        out["min_duration"] = 1
        out["max_duration"] = HORIZON - 2
        out["charge_mode"] = 0
        out["is_charging"] = 0
        if is_dawn:
            out["earliest_start"] = 0
            out["latest_start"] = 0
        else:
            out["earliest_start"] = 0
            out["latest_start"] = HORIZON - 2
        return out

    dawn_df = _sentinel(home, is_dawn=True)
    dusk_df = _sentinel(home, is_dawn=False)
    activities = activities.copy()
    activities["_sentinel"] = "activity"

    return pd.concat([dawn_df, activities, dusk_df], ignore_index=True)


# ── Non-charging duplicate rows ───────────────────────────────────────────────


def duplicate_for_choice(df):
    """
    For every activity row that has charger access (charge_mode != 0), emit a
    second row representing the driver's option to NOT charge there.

    Rules:
    - Only duplicate rows with _sentinel == "activity" (excludes dawn/dusk).
    - Skip service stations (is_service_station == 1) — they always charge.
    - The charging variant keeps its original charge_mode / is_charging.
    - The non-charging variant gets charge_mode=0, is_charging=0.
    - A _is_noncharge_copy column (0 or 1) is added so assign_ids can place
      the charging row immediately before its non-charging twin.
    """
    df = df.copy()
    df["_is_noncharge_copy"] = 0

    mask = (
        (df["_sentinel"] == "activity")
        & (df["charge_mode"] != 0)
        & (df["is_service_station"] != 1)
    )
    dupes = df[mask].copy()
    dupes["charge_mode"] = 0
    dupes["is_charging"] = 0
    dupes["_is_noncharge_copy"] = 1

    return pd.concat([df, dupes], ignore_index=True)


# ── Sequential id assignment ──────────────────────────────────────────────────


def assign_ids(df):
    """
    Assign per-person sequential integer ids:
      0 = dawn, 1…N = activities (ordered by activity_idx), N+1 = dusk.

    When duplicate_for_choice has been applied, the charging variant of each
    activity immediately precedes its non-charging twin (_is_noncharge_copy
    distinguishes them within the same activity_idx).
    """
    df = df.copy()
    sentinel_order = df["_sentinel"].map({"dawn": 0, "dusk": 2}).fillna(1)
    act_idx = df.get("activity_idx", pd.Series(0, index=df.index)).fillna(0)
    noncharge = df.get("_is_noncharge_copy", pd.Series(0, index=df.index)).fillna(0)
    df["_sort"] = sentinel_order * 10_000 + act_idx + noncharge * 0.5
    df = df.sort_values(["pid", "_sort"])
    df["id"] = df.groupby("pid").cumcount()

    # base_id ties a no-charge twin back to its charging original. The sort above
    # places the charging variant immediately before its twin, so the original is
    # always at id - 1. Rows without a twin are their own base.
    # The C layer keys the participation/start/duration/travel error terms on
    # base_id, so twins share one draw instead of getting two independent ones.
    noncharge_sorted = (
        df.get("_is_noncharge_copy", pd.Series(0, index=df.index)).fillna(0).astype(int)
    )
    df["base_id"] = df["id"] - noncharge_sorted

    df = df.drop(
        columns=["_sort", "_sentinel", "activity_idx", "_is_noncharge_copy"],
        errors="ignore",
    )
    return df


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    p.add_argument(
        "--home-charging-share", type=float, default=HOME_CHARGING_SHARE,
        metavar="P",
        help="Fraction of persons with a home charger, 0.0-1.0 (default: %(default)s). "
             "Assignment is per person and nested across shares, so scenarios differ "
             "only by the lever.",
    )
    p.add_argument(
        "--home-charging-seed", type=int, default=HOME_CHARGING_SEED, metavar="N",
        help="Seed for home-charger assignment (default: %(default)s). Hold this "
             "fixed across scenarios you intend to compare.",
    )
    p.add_argument(
        "--home-chargers-file", type=Path, default=None, metavar="CSV",
        help="Optional CSV with columns [pid, has_home_charger] giving an explicit "
             "per-person assignment (e.g. derived from dwelling type). Overrides "
             "--home-charging-share.",
    )
    p.add_argument(
        "--out-stem", type=Path, default=OUT_STEM, metavar="PATH",
        help="Output path without extension (default: %(default)s). Give each "
             "scenario its own stem so runs do not overwrite each other.",
    )
    return p.parse_args()


def main(args=None):
    args = args or parse_args()
    t0 = time.time()

    # Charger index (built once, used for all unique locations)
    tree, powers = build_charger_tree(CHARGERS_PATH)

    # Activities
    print("Loading activities...")
    acts = pd.read_csv(ACTS_PATH)
    acts["is_service_station"] = 0

    # Persons
    print("Loading persons...")
    persons = pd.read_csv(PERSONS_PATH, low_memory=False)

    # Inner-join on pid: keep only persons present in both tables
    common = set(acts["pid"].unique()) & set(persons["pid"].unique())
    print(f"Persons in both tables: {len(common):,}")
    acts = acts[acts["pid"].isin(common)]
    persons = persons[persons["pid"].isin(common)]

    # Who has a home charger
    if args.home_chargers_file is not None:
        home_chargers = pd.read_csv(args.home_chargers_file)
        missing = {"pid", "has_home_charger"} - set(home_chargers.columns)
        if missing:
            raise SystemExit(
                f"{args.home_chargers_file} is missing column(s): {sorted(missing)}"
            )
        home_chargers = home_chargers[["pid", "has_home_charger"]]
        src = str(args.home_chargers_file)
    else:
        if not 0.0 <= args.home_charging_share <= 1.0:
            raise SystemExit("--home-charging-share must be between 0.0 and 1.0")
        home_chargers = assign_home_charging(
            sorted(common), args.home_charging_share, args.home_charging_seed
        )
        src = f"share={args.home_charging_share} seed={args.home_charging_seed}"
    n_home = int(home_chargers["has_home_charger"].sum())
    print(
        f"Home chargers: {n_home:,} / {len(home_chargers):,} persons "
        f"({n_home / max(len(home_chargers), 1):.1%})  [{src}]"
    )

    # Charging assignment (spatial join per unique location)
    acts = assign_charging(acts, tree, powers, CHARGER_THRESHOLD_M, home_chargers)

    # Desired start time / duration
    acts = join_desired_times(acts, persons)

    # Dawn / dusk sentinels
    acts = add_dawn_dusk(acts, persons)

    # Duplicate chargeable activities to give DP the option not to charge
    acts = duplicate_for_choice(acts)

    # Sequential ids
    acts = assign_ids(acts)

    # Re-attach per-person home-charger status. The dawn/dusk sentinels are built
    # from the persons table so they never carried it, and it is worth keeping in
    # the output as an analysis dimension (charging demand split by home access).
    acts = acts.drop(columns=["has_home_charger"], errors="ignore").merge(
        home_chargers, on="pid", how="left"
    )
    acts["has_home_charger"] = acts["has_home_charger"].fillna(0).astype(int)

    # Final column selection (matches testing_check.py expected schema)
    output_cols = [
        "pid",
        "id",
        "act_type",
        "x",
        "y",
        "group",
        "earliest_start",
        "latest_start",
        "min_duration",
        "max_duration",
        "des_start_time",
        "des_duration",
        "charge_mode",
        "is_charging",
        "is_service_station",
        "base_id",
        "has_home_charger",
    ]
    acts = acts[output_cols]

    # Enforce integer types
    int_cols = [
        "id",
        "group",
        "earliest_start",
        "latest_start",
        "min_duration",
        "max_duration",
        "des_start_time",
        "des_duration",
        "charge_mode",
        "is_charging",
        "is_service_station",
        "base_id",
        "has_home_charger",
    ]
    acts[int_cols] = acts[int_cols].astype(int)

    # Save. Build the filename by appending rather than Path.with_suffix(), which
    # would treat the ".6" in a stem like "…_home0.6" as an extension and overwrite
    # a different scenario's output.
    out_stem = Path(args.out_stem)
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    try:
        out_path = out_stem.with_name(out_stem.name + ".parquet")
        acts.to_parquet(out_path, index=False)
        print(
            f"Saved {len(acts):,} rows ({acts['pid'].nunique():,} persons) → {out_path}"
        )
    except ImportError:
        out_path = out_stem.with_name(out_stem.name + ".csv")
        acts.to_csv(out_path, index=False)
        print(f"pyarrow not found; saved as CSV → {out_path}")
        print("  Install with: pip install pyarrow   (much faster reads)")

    print(f"Elapsed: {time.time() - t0:.1f} s")

    # Quick sanity check
    sample = acts[acts["pid"] == acts["pid"].iloc[0]]
    print(f"\nSample person ({sample['pid'].iloc[0]}): {len(sample)} activities")
    print(f"  ids: {sample['id'].tolist()}")
    print(
        f"  dawn charge_mode={sample.iloc[0]['charge_mode']}, "
        f"dusk charge_mode={sample.iloc[-1]['charge_mode']}"
    )
    print(
        f"  home is_charging: {sample[sample['act_type'] == 'home']['is_charging'].tolist()}"
    )


if __name__ == "__main__":
    main()
