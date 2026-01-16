import argparse
import contextlib
import io
import importlib.util
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Tuple

import pandas as pd
from ctypes import CDLL, POINTER, c_char, c_double, c_int


@dataclass(frozen=True)
class RunResult:
    person: str
    csv: str
    scenario: str
    initial_soc: float
    feasible: bool
    runtime_s: float
    final_utility: float | None
    charging_sessions: int | None
    total_charge_cost: float | None
    final_soc: float | None
    activities: int


def _load_testing_check_module():
    spec = importlib.util.spec_from_file_location(
        "testing_check", os.path.join("testing_latest", "testing_check.py")
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _pick_csv(person_dir: str) -> str:
    for candidate in [
        "activities_with_charge_values.csv",
        "activities_with_charge_adjusted.csv",
        "activities_with_charge.csv",
        "activities.csv",
    ]:
        path = os.path.join(person_dir, candidate)
        if os.path.exists(path):
            return candidate
    raise FileNotFoundError(f"No activities CSV found in {person_dir}")


def _scenario_variants() -> Dict[str, Callable[[pd.DataFrame], pd.DataFrame]]:
    def _disable_rows(out: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
        # Make rows unreachable without changing ids (C code expects 0..N-1 ids).
        # This avoids creating duplicate activities that can explode the DP state space.
        out = out.copy()
        mask = mask.fillna(False)
        if not mask.any():
            return out
        out.loc[mask, "earliest_start"] = 10**9
        out.loc[mask, "latest_start"] = -1
        out.loc[mask, "min_duration"] = 0
        out.loc[mask, "max_duration"] = 0
        if "is_charging" in out.columns:
            out.loc[mask, "is_charging"] = 0
        if "charge_mode" in out.columns:
            out.loc[mask, "charge_mode"] = 0
        if "is_service_station" in out.columns:
            out.loc[mask, "is_service_station"] = 0
        return out

    def baseline(df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def no_charging(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        # Disable charging-capable variants and service stations instead of turning them into
        # duplicates of existing non-charging activities.
        mask = pd.Series(False, index=out.index)
        if "is_charging" in out.columns:
            mask = mask | (out["is_charging"] == 1)
        if "is_service_station" in out.columns:
            mask = mask | (out["is_service_station"] == 1)
        return _disable_rows(out, mask)

    def work_charging_only(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        # Disable service stations.
        if "is_service_station" in out.columns:
            out = _disable_rows(out, out["is_service_station"] == 1)

        # If a charging variant exists for an activity at the same location, disable the
        # non-charging variant so charging is the only way to do that activity at that location.
        if {"act_type", "x", "y", "is_charging"}.issubset(out.columns):
            charging_keys = set(
                tuple(row)
                for row in out.loc[out["is_charging"] == 1, ["act_type", "x", "y"]].itertuples(index=False, name=None)
            )
            non_charge_mask = out["is_charging"] == 0
            key_mask = out.loc[non_charge_mask, ["act_type", "x", "y"]].apply(tuple, axis=1).isin(charging_keys)
            out = _disable_rows(out, non_charge_mask & key_mask)
        return out

    def service_station_only(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        # Disable all charging-capable non-station activities; keep service stations.
        mask = pd.Series(False, index=out.index)
        if "is_charging" in out.columns:
            mask = mask | (out["is_charging"] == 1)
        if "is_service_station" in out.columns:
            mask = mask & (out["is_service_station"] != 1)
        out = _disable_rows(out, mask)

        # Ensure service stations remain charging (Eq. 33).
        if "is_service_station" in out.columns:
            mask_station = out["is_service_station"] == 1
            if "is_charging" in out.columns:
                out.loc[mask_station, "is_charging"] = 1
            if "charge_mode" in out.columns:
                out.loc[mask_station & (out["charge_mode"] == 0), "charge_mode"] = 3
        return out

    def far_service_station(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "is_service_station" not in out.columns:
            return out
        mask_station = out["is_service_station"] == 1
        if mask_station.any():
            # Increase detour distance without making the DP runtime explode.
            out.loc[mask_station, "x"] = out.loc[mask_station, "x"] + 5000.0
            out.loc[mask_station, "y"] = out.loc[mask_station, "y"] + 5000.0
        return out

    return {
        "baseline": baseline,
        "no_charging": no_charging,
        "work_charging_only": work_charging_only,
        "service_station_only": service_station_only,
        "far_service_station": far_service_station,
    }


def _configure_lib_signatures(lib: CDLL, tc) -> None:
    lib.set_general_parameters.argtypes = [
        c_int,
        c_double,
        c_double,
        c_int,
        POINTER(c_double),
        POINTER(c_double),
        POINTER(c_double),
        POINTER(c_double),
        POINTER(c_double),
    ]
    lib.set_activities.argtypes = [POINTER(tc.Activity), c_int]
    lib.main.argtypes = [c_int, POINTER(POINTER(c_char))]
    lib.main.restype = c_int
    lib.get_final_schedule.restype = POINTER(tc.Label)
    lib.free_bucket.restype = None

    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype = None
    lib.clear_fixed_initial_soc.argtypes = []
    lib.clear_fixed_initial_soc.restype = None


def _run_once(lib: CDLL, tc, df: pd.DataFrame, initial_soc: float) -> Tuple[bool, float, object | None]:
    with contextlib.redirect_stdout(io.StringIO()):
        activities_array, max_num_activities = tc.initialise_and_personalise_activities(df)
    params = tc.initialize_utility()

    asc_array = (c_double * len(params["asc"]))(*params["asc"])
    early_array = (c_double * len(params["early"]))(*params["early"])
    late_array = (c_double * len(params["late"]))(*params["late"])
    long_array = (c_double * len(params["long"]))(*params["long"])
    short_array = (c_double * len(params["short"]))(*params["short"])

    lib.set_general_parameters(
        tc.HORIZON,
        tc.SPEED,
        tc.TRAVEL_TIME_PENALTY,
        tc.TIME_INTERVAL,
        asc_array,
        early_array,
        late_array,
        long_array,
        short_array,
    )

    lib.set_activities(activities_array, max_num_activities)
    lib.set_fixed_initial_soc(initial_soc)

    t0 = time.time()
    lib.main(0, None)
    runtime_s = time.time() - t0

    best_label = lib.get_final_schedule()
    if not best_label:
        lib.free_bucket()
        return False, runtime_s, None

    schedule_df = tc.extract_schedule(best_label, activities_array, df)
    lib.free_bucket()
    return True, runtime_s, schedule_df


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-run DP scenarios across fixed initial SOC values.")
    parser.add_argument(
        "--people",
        nargs="*",
        default=None,
        help="Person folder names under testing_latest/ (default: all person_* dirs)",
    )
    parser.add_argument(
        "--socs",
        default="0.1,0.2,0.3,0.4,0.6",
        help="Comma-separated list of initial SOC values in [0,1]",
    )
    parser.add_argument(
        "--scenarios",
        default="baseline,no_charging,work_charging_only,service_station_only,far_service_station",
        help="Comma-separated scenario names",
    )
    parser.add_argument(
        "--out",
        default="testing_latest/scale_runs_summary.csv",
        help="Output CSV path for the summary",
    )
    args = parser.parse_args()

    tc = _load_testing_check_module()
    with contextlib.redirect_stdout(io.StringIO()):
        lib_path = tc.compile_code()
    lib = CDLL(lib_path)
    _configure_lib_signatures(lib, tc)

    initial_socs = [float(x.strip()) for x in args.socs.split(",") if x.strip()]
    scenarios = [x.strip() for x in args.scenarios.split(",") if x.strip()]
    all_variants = _scenario_variants()
    for s in scenarios:
        if s not in all_variants:
            raise SystemExit(f"Unknown scenario '{s}'. Available: {', '.join(sorted(all_variants))}")

    if args.people is None:
        people = [
            os.path.basename(p)
            for p in sorted(
                d for d in os.listdir("testing_latest") if d.startswith("person_") and os.path.isdir(os.path.join("testing_latest", d))
            )
        ]
    else:
        people = args.people

    results: List[RunResult] = []

    for person in people:
        person_dir = os.path.join("testing_latest", person)
        csv_name = _pick_csv(person_dir)
        df_base = pd.read_csv(os.path.join(person_dir, csv_name))

        for scenario in scenarios:
            df = all_variants[scenario](df_base)

            for soc in initial_socs:
                feasible, runtime_s, schedule_df = _run_once(lib, tc, df, soc)
                if feasible and schedule_df is not None and len(schedule_df) > 0:
                    results.append(
                        RunResult(
                            person=person,
                            csv=csv_name,
                            scenario=scenario,
                            initial_soc=soc,
                            feasible=True,
                            runtime_s=runtime_s,
                            final_utility=float(schedule_df["utility"].iloc[-1]),
                            charging_sessions=int(schedule_df["is_charging"].sum()),
                            total_charge_cost=float(schedule_df["charge_cost"].max()),
                            final_soc=float(schedule_df["soc_end"].iloc[-1]),
                            activities=int(len(df)),
                        )
                    )
                else:
                    results.append(
                        RunResult(
                            person=person,
                            csv=csv_name,
                            scenario=scenario,
                            initial_soc=soc,
                            feasible=False,
                            runtime_s=runtime_s,
                            final_utility=None,
                            charging_sessions=None,
                            total_charge_cost=None,
                            final_soc=None,
                            activities=int(len(df)),
                        )
                    )

    lib.clear_fixed_initial_soc()

    out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pd.DataFrame([r.__dict__ for r in results]).to_csv(out_path, index=False)
    print(f"Wrote summary to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
