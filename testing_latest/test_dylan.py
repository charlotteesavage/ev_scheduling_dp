"""
Test script for Dylan's schedule to match the paper results.

This script runs the Dylan test case with the correct parameters
to match Figure 1 from the paper.
"""

import pandas as pd
from ctypes import c_double
from testing_check import (
    compile_code, CDLL,
    initialise_and_personalise_activities,
    initialize_utility,
    run_dp,
    extract_schedule,
    Activity, Label, c_int, c_char, POINTER
)
import os


def test_dylan():
    """Run Dylan test case matching the paper."""

    print("="*80)
    print("DYLAN TEST CASE - Matching Paper Figure 1(c)")
    print("="*80)

    # Compile and load library
    lib_path = compile_code()
    lib = CDLL(lib_path)

    # Set up function signatures
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
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype = None
    lib.clear_fixed_initial_soc.restype = None

    # Try corrected file first, fall back to original
    activities_files = [
        "testing_latest/dylan/activities.csv",
        # "testing_latest/dylan/dylan_DP_schedule.csv"
    ]

    activities_file = None
    for f in activities_files:
        if os.path.exists(f):
            activities_file = f
            break

    if not activities_file:
        print("ERROR: No Dylan activities file found!")
        print("Checked:")
        for f in activities_files:
            print(f"  - {f}")
        return

    print(f"\nUsing activities file: {activities_file}")

    # Load activities
    activities_df = pd.read_csv(activities_file)
    print(f"Loaded {len(activities_df)} activities")
    print("\nActivities:")
    print(activities_df[['id', 'act_type', 'des_start_time', 'des_duration',
                         'is_charging', 'charge_mode']].to_string(index=False))

    # Check for issues
    issues = []

    # Check for missing values
    if activities_df.isnull().any().any():
        issues.append("⚠️  Missing values detected in activities file!")
        print("\nMissing values:")
        print(activities_df.isnull().sum())

    # Check for dusk activity
    if not any(activities_df['act_type'].str.lower() == 'dusk'):
        issues.append("⚠️  Missing DUSK activity (id should be max_id)")

    # Check dawn configuration
    dawn = activities_df[activities_df['id'] == 0]
    if not dawn.empty:
        if dawn['group'].iloc[0] != 0:
            issues.append(f"⚠️  Dawn has group={dawn['group'].iloc[0]}, should be 0")
        if dawn['min_duration'].iloc[0] != 1:
            issues.append(f"⚠️  Dawn has min_duration={dawn['min_duration'].iloc[0]}, should be 1")

    if issues:
        print("\n" + "="*80)
        print("ISSUES DETECTED:")
        print("="*80)
        for issue in issues:
            print(issue)
        print("\nThese issues may prevent matching the paper's results.")
        print("Consider using the corrected file: dylan_activities_corrected.csv")

    # Set initial SOC to match paper (approximately 25.8%)
    initial_soc = 0.258
    print(f"\n{'='*80}")
    print(f"Setting initial SOC to {initial_soc:.1%} (from paper)")
    print(f"{'='*80}")
    lib.set_fixed_initial_soc(c_double(initial_soc))

    # Prepare activities
    activities_array, max_num_activities = initialise_and_personalise_activities(
        activities_df
    )

    # Initialize parameters
    params = initialize_utility()

    # Run DP
    print("\nRunning DP algorithm...")
    best_label = run_dp(lib, activities_array, max_num_activities, params)

    if best_label:
        # Extract schedule
        schedule_df = extract_schedule(best_label, activities_array, activities_df)

        print("\n" + "="*80)
        print("OPTIMAL SCHEDULE")
        print("="*80)
        print(schedule_df.to_string(index=False))

        # Save results
        output_dir = "testing_latest/dylan"
        os.makedirs(output_dir, exist_ok=True)

        output_file = f"{output_dir}/dylan_optimal_schedule.csv"
        schedule_df.to_csv(output_file, index=False)
        print(f"\n✓ Schedule saved to: {output_file}")

        # Compare with expected paper results
        print("\n" + "="*80)
        print("COMPARISON WITH PAPER (Figure 1c)")
        print("="*80)

        print("\nExpected sequence from paper:")
        print("  1. Home (0:00-9:00, blue)")
        print("  2. Delivery/errands (~9:10-10:30, gray)")
        print("  3. Service station (~10:30-11:30, yellow, CHARGING)")
        print("  4. Other/escort (~11:40-13:00, gray)")
        print("  5. Shop/visit (~13:15-14:45, gray)")
        print("  6. Home (~15:00-24:00, blue)")

        print("\nYour schedule:")
        for idx, row in schedule_df.iterrows():
            start_hour = row['start_time'] * 5 / 60  # Convert intervals to hours
            duration_hour = row['duration'] * 5 / 60
            end_hour = start_hour + duration_hour

            charge_info = ""
            if row['is_charging']:
                charge_info = f", CHARGING mode={row['charge_mode']}"

            print(f"  {idx+1}. {row['act_type']} "
                  f"({start_hour:.2f}h-{end_hour:.2f}h, "
                  f"SOC: {row['soc_start']:.1%}→{row['soc_end']:.1%}{charge_info})")

        # Check SOC levels
        print("\n" + "="*80)
        print("SOC ANALYSIS")
        print("="*80)
        print(f"Initial SOC: {schedule_df['soc_start'].iloc[0]:.2%}")
        print(f"Final SOC: {schedule_df['soc_end'].iloc[-1]:.2%}")
        print(f"Minimum SOC reached: {schedule_df['soc_start'].min():.2%}")
        print(f"Maximum SOC reached: {schedule_df['soc_end'].max():.2%}")

        # Check if service station was used
        service_station_rows = schedule_df[schedule_df['act_type'].str.contains('service', case=False, na=False)]
        if not service_station_rows.empty:
            print("\n✓ Service station was visited")
            for idx, row in service_station_rows.iterrows():
                print(f"  - Duration: {row['duration']*5:.0f} minutes")
                print(f"  - SOC gain: {(row['soc_end'] - row['soc_start']):.2%}")
                print(f"  - Charge cost: £{row['charge_cost']:.2f}")
        else:
            print("\n⚠️  Service station was NOT visited!")

    else:
        print("\n✗ ERROR: No feasible solution found!")
        print("This likely means the constraints are too tight or there's an issue with the activities.")

    # Cleanup
    lib.clear_fixed_initial_soc()
    lib.free_bucket()

    print("\n" + "="*80)
    print("Test complete!")
    print("="*80)


if __name__ == "__main__":
    test_dylan()
