import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from testing_check import (
    compile_code, initialise_and_personalise_activities,
    initialize_utility, run_dp, extract_schedule,
    CDLL, Activity, Label, POINTER, c_char, c_double, c_int
)


def check_battery(schedule):
    for _, row in schedule.iterrows():
        if row['soc_start'] < 0 or row['soc_end'] < 0:
            return False
        if row['soc_start'] > 1.0 or row['soc_end'] > 1.0:
            return False
    return True


def check_travel_consumption(schedule, tolerance=0.03):
    # expected arrival SOC at next activity = previous soc_end - (distance_km * 0.2 / 60)
    # (0.2 kWh/km, 60 kWh battery)
    if len(schedule) < 2:
        return True

    ENERGY_CONSUMPTION_KWH_PER_KM = 0.2
    BATTERY_KWH = 60.0

    for i in range(1, len(schedule)):
        prev_row = schedule.iloc[i - 1]
        row = schedule.iloc[i]

        dx = float(row["x"]) - float(prev_row["x"])
        dy = float(row["y"]) - float(prev_row["y"])
        dist_m = (dx * dx + dy * dy) ** 0.5
        dist_km = dist_m / 1000.0

        expected_drop = (dist_km * ENERGY_CONSUMPTION_KWH_PER_KM) / BATTERY_KWH
        expected_soc = float(prev_row["soc_end"]) - expected_drop
        actual_soc = float(row["soc_start"])

        if abs(actual_soc - expected_soc) > tolerance:
            return False

    return True


def check_times(schedule, activities):
    for _, row in schedule.iterrows():
        input_act = activities[activities['id'] == row['act_id']]
        if len(input_act) == 0:
            continue
        input_act = input_act.iloc[0]
        start_interval = int(float(row['start_time']) * 60 / 5)  # 5-minute intervals
        if start_interval < input_act['earliest_start']:
            return False
        if start_interval > input_act['latest_start']:
            return False
    return True


def check_charging(schedule, activities):
    for _, row in schedule.iterrows():
        if row['charge_duration'] > 0:
            input_act = activities[activities['id'] == row['act_id']]
            if len(input_act) == 0:
                continue
            if input_act.iloc[0]['is_charging'] != 1:
                return False
    return True


def check_durations(schedule, activities):
    for i, row in schedule.iterrows():
        if i == len(schedule) - 1:
            continue
        input_act = activities[activities['id'] == row['act_id']]
        if len(input_act) == 0:
            continue
        input_act = input_act.iloc[0]
        if row['duration'] < input_act['min_duration']:
            return False
        if row['duration'] > input_act['max_duration']:
            return False
    return True


def check_horizon(schedule):
    # start_time is in hours, duration is in 5-minute intervals
    for _, row in schedule.iterrows():
        start_hr = float(row['start_time'])
        dur_hr = float(row['duration']) * 5 / 60.0
        if start_hr + dur_hr > 24.0 + 1e-9:
            return False
    return True


def check_service_station(schedule, activities):
    # If we visit a service station activity, we expect charge_duration > 0
    schedule_with_ss = schedule.merge(
        activities[['id', 'act_type', 'is_service_station']],
        left_on='act_id',
        right_on='id',
        how='left'
    )
    ss_rows = schedule_with_ss[schedule_with_ss['is_service_station'] == 1]
    for _, row in ss_rows.iterrows():
        if row['charge_duration'] <= 0:
            return False
    return True


def check_no_repeats(schedule, activities):
    schedule_with_groups = schedule.merge(
        activities[['id', 'group']],
        left_on='act_id',
        right_on='id'
    )
    non_home = schedule_with_groups[schedule_with_groups['group'] != 1]
    group_counts = non_home['group'].value_counts()
    for count in group_counts:
        if count > 1:
            return False
    return True


def run_test(lib, csv_file, start_battery):
    csv_path = Path(__file__).parent / csv_file
    if not csv_path.exists():
        return "SKIP"

    activities = pd.read_csv(csv_path)
    activities_array, num_activities = initialise_and_personalise_activities(activities)
    params = initialize_utility()

    lib.set_fixed_initial_soc(c_double(start_battery))
    lib.set_utility_error_std_dev(c_double(0.0))
    lib.set_random_seed(c_int(42))

    result = run_dp(lib, activities_array, num_activities, params)
    if result is None:
        return "FAIL"

    best_label, _ = result
    schedule = extract_schedule(best_label, activities_array, activities)
    schedule = schedule.sort_values('start_time').reset_index(drop=True)

    if not check_battery(schedule):
        return "FAIL"
    if not check_travel_consumption(schedule):
        return "FAIL"
    if not check_times(schedule, activities):
        return "FAIL"
    if not check_charging(schedule, activities):
        return "FAIL"
    if not check_durations(schedule, activities):
        return "FAIL"
    if not check_service_station(schedule, activities):
        return "FAIL"
    if not check_no_repeats(schedule, activities):
        return "FAIL"
    if not check_horizon(schedule):
        return "FAIL"

    return "PASS"


def main():
    print("Running tests...")

    lib_path = compile_code()
    lib = CDLL(lib_path)

    lib.set_general_parameters.argtypes = [
        c_int, c_double, c_double, c_int,
        POINTER(c_double), POINTER(c_double), POINTER(c_double),
        POINTER(c_double), POINTER(c_double)
    ]
    lib.set_activities.argtypes = [POINTER(Activity), c_int]
    lib.main.argtypes = [c_int, POINTER(POINTER(c_char))]
    lib.main.restype = c_int
    lib.get_final_schedule.restype = POINTER(Label)
    lib.free_bucket.restype = None
    lib.set_random_seed.argtypes = [c_int]
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_utility_error_std_dev.argtypes = [c_double]

    results = []

    results.append(run_test(lib, "travel_consumption.csv", 0.5))
    lib.free_bucket()

    results.append(run_test(lib, "charging_rates.csv", 0.3))
    lib.free_bucket()

    results.append(run_test(lib, "time_windows.csv", 0.5))
    lib.free_bucket()

    results.append(run_test(lib, "soc_never_negative.csv", 0.1))
    lib.free_bucket()

    results.append(run_test(lib, "soc_never_exceeds_100.csv", 0.95))
    lib.free_bucket()

    results.append(run_test(lib, "duration_bounds.csv", 0.5))
    lib.free_bucket()

    results.append(run_test(lib, "service_station.csv", 0.3))
    lib.free_bucket()

    results.append(run_test(lib, "no_group_repeats.csv", 0.5))
    lib.free_bucket()

    results.append(run_test(lib, "horizon_constraint.csv", 0.5))
    lib.free_bucket()

    passed = results.count("PASS")
    failed = results.count("FAIL")

    print(f"Passed: {passed}, Failed: {failed}")

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
