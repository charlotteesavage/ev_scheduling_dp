"""
Simple example of running multi-day simulations with SOC carryover.

This is a minimal example showing how to chain days together.
For a full-featured version, see multi_day_testing.py
"""

import pandas as pd
from ctypes import c_double
from testing_check import (
    compile_code, CDLL,
    initialise_and_personalise_activities,
    initialize_utility,
    run_dp,
    extract_schedule
)


def simple_multi_day_test():
    """Run a simple 3-day test with SOC carryover."""

    # Compile and load library
    lib_path = compile_code()
    lib = CDLL(lib_path)

    # Set up function signatures (add to testing_check.py setup)
    from testing_check import Activity, Label, c_int, c_char, POINTER
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

    # Load activities
    activities_file = "testing_latest/person_ending_1263/activities_with_charge_values.csv"
    activities_df = pd.read_csv(activities_file)

    # Initialize parameters
    params = initialize_utility()

    # Starting SOC for day 1
    current_soc = 0.30  # 30%

    # Run for 3 days
    for day in range(1, 4):
        print(f"\n{'='*60}")
        print(f"DAY {day} - Starting SOC: {current_soc:.2%}")
        print(f"{'='*60}")

        # Set the initial SOC for this day
        lib.set_fixed_initial_soc(c_double(current_soc))

        # Prepare activities
        activities_array, max_num_activities = initialise_and_personalise_activities(
            activities_df
        )

        # Run the DP algorithm
        best_label = run_dp(lib, activities_array, max_num_activities, params)

        if best_label:
            # Extract schedule
            schedule_df = extract_schedule(best_label, activities_array, activities_df)

            # Get the ending SOC for this day
            ending_soc = schedule_df['soc_end'].iloc[-1]

            print(f"\nDay {day} Results:")
            print(f"  Started with: {current_soc:.2%} SOC")
            print(f"  Ended with: {ending_soc:.2%} SOC")
            print(f"  Change: {(ending_soc - current_soc):+.2%}")
            print(f"  Utility: {schedule_df['utility'].iloc[-1]:.2f}")

            # Save this day's schedule
            schedule_df.to_csv(f"testing_latest/day_{day}_schedule.csv", index=False)

            # Use ending SOC as starting SOC for next day
            current_soc = ending_soc
        else:
            print(f"ERROR: No solution found for day {day}")
            break

        # Clean up
        lib.clear_fixed_initial_soc()
        lib.free_bucket()

    print(f"\n{'='*60}")
    print("Multi-day simulation complete!")
    print(f"{'='*60}")


if __name__ == "__main__":
    simple_multi_day_test()
