#!/usr/bin/env python3
"""
Validation Test Suite for EV Scheduling Algorithm

This script runs basic validation tests to ensure the scheduling algorithm
produces reasonable results for battery physics and constraint satisfaction.
"""

import pandas as pd
import numpy as np
import sys
import os
from pathlib import Path
from ctypes import c_double, c_int

# Add parent directory to path to import testing_check
sys.path.insert(0, str(Path(__file__).parent.parent))
from testing_check import (
    compile_code,
    initialise_and_personalise_activities,
    initialize_utility,
    run_dp,
    extract_schedule,
    CDLL,
    Activity,
    Label,
    POINTER,
    c_char,
    TIME_INTERVAL,
    HORIZON,
    SPEED,
)

# ============================================================================
# Test Configuration
# ============================================================================

BATTERY_CAPACITY = 60.0  # kWh
ENERGY_CONSUMPTION = 0.2  # kWh/km
SLOW_CHARGE_POWER = 7.0  # kW
FAST_CHARGE_POWER = 22.0  # kW
RAPID_CHARGE_POWER = 50.0  # kW

# ============================================================================
# Validation Helper Functions
# ============================================================================


def calculate_expected_soc_change_from_travel(distance_meters):
    """Calculate expected SOC change from travel distance."""
    distance_km = distance_meters / 1000.0
    energy_consumed = distance_km * ENERGY_CONSUMPTION
    soc_change = energy_consumed / BATTERY_CAPACITY
    return soc_change


def calculate_expected_soc_change_from_charging(charge_mode, duration_intervals):
    """Calculate expected SOC change from charging."""
    duration_hours = duration_intervals * TIME_INTERVAL / 60.0

    if charge_mode == 1:  # Slow charge
        power = SLOW_CHARGE_POWER
    elif charge_mode == 2:  # Fast charge
        power = FAST_CHARGE_POWER
    elif charge_mode == 3:  # Rapid charge
        power = RAPID_CHARGE_POWER
    elif charge_mode in [4, 5, 6]:  # Free charging (slow, fast, rapid)
        power = [SLOW_CHARGE_POWER, FAST_CHARGE_POWER, RAPID_CHARGE_POWER][charge_mode - 4]
    else:
        return 0.0

    energy_added = power * duration_hours
    soc_change = energy_added / BATTERY_CAPACITY
    return soc_change


def calculate_distance(x1, y1, x2, y2):
    """Calculate Euclidean distance between two points."""
    return np.sqrt((x2 - x1)**2 + (y2 - y1)**2)


# ============================================================================
# Validation Test Functions
# ============================================================================


def check_soc_bounds(schedule_df, activities_df, test_name):
    """Test that SOC stays within [0, 1] bounds."""
    violations = []

    # Check soc_start
    if (schedule_df['soc_start'] < 0).any():
        neg_soc = schedule_df[schedule_df['soc_start'] < 0]
        violations.append(f"  - {len(neg_soc)} rows have negative soc_start")

    if (schedule_df['soc_start'] > 1.0).any():
        over_soc = schedule_df[schedule_df['soc_start'] > 1.0]
        violations.append(f"  - {len(over_soc)} rows have soc_start > 100%")

    # Check soc_end
    if (schedule_df['soc_end'] < 0).any():
        neg_soc = schedule_df[schedule_df['soc_end'] < 0]
        violations.append(f"  - {len(neg_soc)} rows have negative soc_end")

    if (schedule_df['soc_end'] > 1.0).any():
        over_soc = schedule_df[schedule_df['soc_end'] > 1.0]
        violations.append(f"  - {len(over_soc)} rows have soc_end > 100%")

    if violations:
        return False, violations
    return True, ["  SOC bounds check passed: all values in [0, 1]"]


def check_travel_consumption(schedule_df, activities_df, test_name, tolerance=0.02):
    """Test that travel consumes battery correctly."""
    violations = []

    for i in range(1, len(schedule_df)):
        prev_row = schedule_df.iloc[i-1]
        curr_row = schedule_df.iloc[i]

        # Calculate distance traveled
        distance = calculate_distance(
            float(prev_row['x']), float(prev_row['y']),
            float(curr_row['x']), float(curr_row['y'])
        )

        if distance > 0:
            # Expected SOC at arrival (before charging)
            expected_soc_change = calculate_expected_soc_change_from_travel(distance)
            expected_arrival_soc = float(prev_row['soc_end']) - expected_soc_change

            # Actual SOC at activity start (before any charging)
            actual_arrival_soc = float(curr_row['soc_start'])

            # Allow small tolerance for floating point errors
            if abs(actual_arrival_soc - expected_arrival_soc) > tolerance:
                violations.append(
                    f"  - Activity {curr_row['act_id']}: "
                    f"Expected arrival SOC {expected_arrival_soc:.4f}, "
                    f"got {actual_arrival_soc:.4f} "
                    f"(distance: {distance/1000:.2f} km)"
                )

    if violations:
        return False, violations
    return True, [f"  Travel consumption check passed ({len(schedule_df)-1} transitions checked)"]


def check_charging_only_where_available(schedule_df, activities_df, test_name):
    """Test that charging only happens at activities where it's available."""
    violations = []

    # Merge schedule with activities to get is_charging flag from input
    schedule_with_input = schedule_df.merge(
        activities_df[['id', 'is_charging', 'charge_mode']],
        left_on='act_id',
        right_on='id',
        suffixes=('_output', '_input')
    )

    for _, row in schedule_with_input.iterrows():
        # If charging happened in output (charge_duration > 0)
        if row['charge_duration'] > 0:
            # Check if input allows charging
            if row['is_charging_input'] != 1:
                violations.append(
                    f"  - Activity {row['act_id']} ({row['act_type']}): "
                    f"Charging occurred but is_charging=0 in input"
                )

    if violations:
        return False, violations
    return True, [f"  Charging location check passed ({len(schedule_df)} activities checked)"]


def check_time_windows(schedule_df, activities_df, test_name):
    """Test that activities respect their time windows."""
    violations = []

    # Merge schedule with activities to get time windows
    schedule_with_input = schedule_df.merge(
        activities_df[['id', 'earliest_start', 'latest_start']],
        left_on='act_id',
        right_on='id'
    )

    for _, row in schedule_with_input.iterrows():
        # Convert start_time from hours to intervals
        start_interval = int(row['start_time'] * 60 / TIME_INTERVAL)

        if start_interval < row['earliest_start']:
            violations.append(
                f"  - Activity {row['act_id']} ({row['act_type']}): "
                f"Started at interval {start_interval}, "
                f"before earliest_start={row['earliest_start']}"
            )

        if start_interval > row['latest_start']:
            violations.append(
                f"  - Activity {row['act_id']} ({row['act_type']}): "
                f"Started at interval {start_interval}, "
                f"after latest_start={row['latest_start']}"
            )

    if violations:
        return False, violations
    return True, [f"  Time window check passed ({len(schedule_df)} activities checked)"]


def check_duration_bounds(schedule_df, activities_df, test_name):
    """Test that activity durations respect min/max bounds."""
    violations = []

    # Merge schedule with activities to get duration bounds
    schedule_with_input = schedule_df.merge(
        activities_df[['id', 'min_duration', 'max_duration']],
        left_on='act_id',
        right_on='id'
    )

    for _, row in schedule_with_input.iterrows():
        duration = row['duration']

        if duration < row['min_duration']:
            violations.append(
                f"  - Activity {row['act_id']} ({row['act_type']}): "
                f"Duration {duration} < min_duration={row['min_duration']}"
            )

        if duration > row['max_duration']:
            violations.append(
                f"  - Activity {row['act_id']} ({row['act_type']}): "
                f"Duration {duration} > max_duration={row['max_duration']}"
            )

    if violations:
        return False, violations
    return True, [f"  Duration bounds check passed ({len(schedule_df)} activities checked)"]


def check_service_station_charging(schedule_df, activities_df, test_name):
    """Test that service stations always include charging."""
    violations = []

    # Merge schedule with activities
    schedule_with_input = schedule_df.merge(
        activities_df[['id', 'is_service_station']],
        left_on='act_id',
        right_on='id'
    )

    for _, row in schedule_with_input.iterrows():
        if row['is_service_station'] == 1:
            if row['charge_duration'] <= 0:
                violations.append(
                    f"  - Activity {row['act_id']} ({row['act_type']}): "
                    f"Service station visited but no charging occurred"
                )

    if violations:
        return False, violations

    service_stations = schedule_with_input[schedule_with_input['is_service_station'] == 1]
    if len(service_stations) > 0:
        return True, [f"  Service station check passed ({len(service_stations)} service stations checked)"]
    return True, ["  No service stations in schedule"]


def check_no_group_repeats(schedule_df, activities_df, test_name):
    """Test that activity groups don't repeat (except home=group 1)."""
    violations = []

    # Merge schedule with activities to get groups
    schedule_with_input = schedule_df.merge(
        activities_df[['id', 'group']],
        left_on='act_id',
        right_on='id'
    )

    # Count occurrences of each group (excluding home=1)
    group_counts = schedule_with_input[schedule_with_input['group'] != 1]['group'].value_counts()

    for group, count in group_counts.items():
        if count > 1:
            activities_in_group = schedule_with_input[schedule_with_input['group'] == group]
            violations.append(
                f"  - Group {group}: appears {count} times "
                f"(activities: {activities_in_group['act_id'].tolist()})"
            )

    if violations:
        return False, violations
    return True, [f"  Group repeat check passed (no non-home groups repeated)"]


def check_horizon_constraint(schedule_df, activities_df, test_name):
    """Test that all activities complete before horizon."""
    violations = []

    for i, row in schedule_df.iterrows():
        # Calculate end time in intervals
        start_interval = int(row['start_time'] * 60 / TIME_INTERVAL)
        end_interval = start_interval + row['duration']

        if end_interval > HORIZON:
            violations.append(
                f"  - Activity {row['act_id']} ({row['act_type']}): "
                f"Ends at interval {end_interval}, exceeds horizon={HORIZON}"
            )

    if violations:
        return False, violations
    return True, [f"  Horizon constraint check passed ({len(schedule_df)} activities checked)"]


def check_utility_monotonicity(schedule_df, activities_df, test_name):
    """Test that utility never decreases."""
    violations = []

    for i in range(1, len(schedule_df)):
        prev_utility = schedule_df.iloc[i-1]['utility']
        curr_utility = schedule_df.iloc[i]['utility']

        if curr_utility < prev_utility:
            violations.append(
                f"  - Activity {schedule_df.iloc[i]['act_id']}: "
                f"Utility decreased from {prev_utility:.2f} to {curr_utility:.2f}"
            )

    if violations:
        return False, violations
    return True, [f"  Utility monotonicity check passed ({len(schedule_df)-1} transitions checked)"]


# ============================================================================
# Test Definitions
# ============================================================================

TESTS = {
    "test_1_1_travel_consumption.csv": {
        "name": "Battery Physics 1.1: Travel Consumption",
        "description": "Tests that travel correctly reduces SOC based on distance",
        "initial_soc": 0.5,
        "checks": [
            check_soc_bounds,
            check_travel_consumption,
            check_utility_monotonicity,
        ]
    },
    "test_1_2_charging_rates.csv": {
        "name": "Battery Physics 1.2: Charging Rates",
        "description": "Tests that charging adds correct amount of energy",
        "initial_soc": 0.3,
        "checks": [
            check_soc_bounds,
            check_charging_only_where_available,
            check_utility_monotonicity,
        ]
    },
    "test_1_3_soc_never_negative.csv": {
        "name": "Battery Physics 1.3: SOC Never Negative",
        "description": "Tests that SOC never goes below 0 with low initial battery",
        "initial_soc": 0.1,
        "checks": [
            check_soc_bounds,
            check_utility_monotonicity,
        ]
    },
    "test_1_4_soc_never_exceeds_100.csv": {
        "name": "Battery Physics 1.4: SOC Never Exceeds 100%",
        "description": "Tests that SOC never exceeds 100% with high initial battery and long charging",
        "initial_soc": 0.95,
        "checks": [
            check_soc_bounds,
            check_charging_only_where_available,
            check_utility_monotonicity,
        ]
    },
    "test_2_1_time_windows.csv": {
        "name": "Constraint Satisfaction 2.1: Time Windows",
        "description": "Tests that activities respect earliest/latest start times",
        "initial_soc": 0.5,
        "checks": [
            check_soc_bounds,
            check_time_windows,
            check_utility_monotonicity,
        ]
    },
    "test_2_2_duration_bounds.csv": {
        "name": "Constraint Satisfaction 2.2: Duration Bounds",
        "description": "Tests that activity durations stay within min/max bounds",
        "initial_soc": 0.5,
        "checks": [
            check_soc_bounds,
            check_duration_bounds,
            check_utility_monotonicity,
        ]
    },
    "test_2_3_service_station.csv": {
        "name": "Constraint Satisfaction 2.3: Service Station",
        "description": "Tests that service stations always include charging",
        "initial_soc": 0.3,
        "checks": [
            check_soc_bounds,
            check_service_station_charging,
            check_utility_monotonicity,
        ]
    },
    "test_2_4_no_group_repeats.csv": {
        "name": "Constraint Satisfaction 2.4: No Group Repeats",
        "description": "Tests that activity groups don't repeat in schedule",
        "initial_soc": 0.5,
        "checks": [
            check_soc_bounds,
            check_no_group_repeats,
            check_utility_monotonicity,
        ]
    },
    "test_2_5_horizon_constraint.csv": {
        "name": "Constraint Satisfaction 2.5: Horizon Constraint",
        "description": "Tests that all activities complete before midnight",
        "initial_soc": 0.5,
        "checks": [
            check_soc_bounds,
            check_horizon_constraint,
            check_utility_monotonicity,
        ]
    },
}


# ============================================================================
# Main Test Runner
# ============================================================================

def run_single_test(lib, test_file, test_config, test_dir):
    """Run a single validation test."""
    print("\n" + "=" * 80)
    print(f"TEST: {test_config['name']}")
    print("=" * 80)
    print(f"Description: {test_config['description']}")
    print(f"Input file: {test_file}")
    print(f"Initial SOC: {test_config['initial_soc']:.0%}")

    # Load test CSV
    csv_path = test_dir / test_file
    if not csv_path.exists():
        print(f"❌ SKIP: Test file not found: {csv_path}")
        return {"status": "SKIP", "reason": "File not found"}

    activities_df = pd.read_csv(csv_path)
    print(f"Loaded {len(activities_df)} activities")

    # Initialize activities
    activities_array, max_num_activities = initialise_and_personalise_activities(activities_df)

    # Initialize utility parameters
    params = initialize_utility()

    # Set fixed initial SOC and disable utility error for deterministic results
    lib.set_fixed_initial_soc(c_double(test_config['initial_soc']))
    lib.set_utility_error_std_dev(c_double(0.0))  # Disable random errors
    lib.set_random_seed(c_int(42))  # Fixed seed

    # Run DP
    try:
        result = run_dp(lib, activities_array, max_num_activities, params)
        if result is None:
            print("❌ FAIL: No feasible solution found")
            return {"status": "FAIL", "reason": "No feasible solution"}

        best_label, total_time = result
    except Exception as e:
        print(f"❌ FAIL: Exception during DP execution: {e}")
        return {"status": "FAIL", "reason": f"Exception: {e}"}

    # Extract schedule
    schedule_df = extract_schedule(best_label, activities_array, activities_df)

    print(f"\nSchedule generated: {len(schedule_df)} activities")
    print(f"Final SOC: {schedule_df['soc_end'].iloc[-1]:.2%}")
    print(f"Final utility: {schedule_df['utility'].iloc[-1]:.2f}")

    # Run validation checks
    print("\n" + "-" * 80)
    print("VALIDATION CHECKS")
    print("-" * 80)

    all_passed = True
    check_results = []

    for check_func in test_config['checks']:
        check_name = check_func.__name__
        try:
            passed, messages = check_func(schedule_df, activities_df, test_file)

            status_symbol = "✓" if passed else "✗"
            print(f"\n{status_symbol} {check_name}")
            for msg in messages:
                print(msg)

            check_results.append({
                "check": check_name,
                "passed": passed,
                "messages": messages
            })

            if not passed:
                all_passed = False
        except Exception as e:
            print(f"\n✗ {check_name}")
            print(f"  ERROR: {e}")
            check_results.append({
                "check": check_name,
                "passed": False,
                "messages": [f"ERROR: {e}"]
            })
            all_passed = False

    # Overall result
    print("\n" + "-" * 80)
    if all_passed:
        print("✓ TEST PASSED")
    else:
        print("✗ TEST FAILED")
    print("-" * 80)

    return {
        "status": "PASS" if all_passed else "FAIL",
        "schedule": schedule_df,
        "checks": check_results
    }


def main():
    """Main test runner."""
    print("=" * 80)
    print("EV SCHEDULING VALIDATION TEST SUITE")
    print("=" * 80)

    # Get test directory
    test_dir = Path(__file__).parent

    # Compile C code
    print("\nCompiling C code...")
    lib_path = compile_code()
    lib = CDLL(lib_path)

    # Set up C function signatures
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

    # Run all tests
    results = {}
    for test_file, test_config in TESTS.items():
        result = run_single_test(lib, test_file, test_config, test_dir)
        results[test_file] = result

        # Cleanup between tests
        lib.free_bucket()

    # Summary
    print("\n\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    passed_count = sum(1 for r in results.values() if r['status'] == 'PASS')
    failed_count = sum(1 for r in results.values() if r['status'] == 'FAIL')
    skipped_count = sum(1 for r in results.values() if r['status'] == 'SKIP')

    for test_file, result in results.items():
        status_symbol = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[result['status']]
        print(f"{status_symbol} {TESTS[test_file]['name']}: {result['status']}")

    print("\n" + "-" * 80)
    print(f"Total: {len(results)} tests")
    print(f"Passed: {passed_count}")
    print(f"Failed: {failed_count}")
    print(f"Skipped: {skipped_count}")
    print("=" * 80)

    # Exit with error code if any tests failed
    if failed_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
