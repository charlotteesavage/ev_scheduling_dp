#!/usr/bin/env python3
"""
Simplified Charging Participation Analysis using subprocess calls.

Avoids memory issues by running each simulation in a separate process.
"""

import pandas as pd
import subprocess
import os
import tempfile
import shutil
import time
import textwrap
from pathlib import Path

# Activity type to display name mapping
ACTIVITY_DISPLAY_NAMES = {
    'home': 'Home',
    'work': 'Work',
    'business': 'Business',
    'shop': 'Shopping',
    'shop/visit': 'Shopping',
    'visit': 'Leisure',
    'education': 'Education',
    'depot': 'Depot',
    'delivery/errands': 'Errands_services',
    'delivery': 'Errands_services',
    'errands': 'Errands_services',
    'other': 'Other',
    'other/escort': 'Escort',
    'escort': 'Escort',
    'medical': 'Medical',
    'pt interaction': 'PT Interaction',
    'service_station': 'Service Station',
}


def get_activity_display_name(act_type):
    """Convert activity type to display name."""
    act_type_lower = str(act_type).lower()
    return ACTIVITY_DISPLAY_NAMES.get(act_type_lower, act_type)


def run_single_simulation(activities_file, output_file, seed):
    """
    Run a single simulation by calling a Python script in subprocess.

    Returns True if successful, False otherwise.
    """
    script = textwrap.dedent(
        f"""
        import os
        import sys
        from ctypes import CDLL, POINTER, c_char, c_double, c_int

        import pandas as pd

        # Add current directory to Python path so testing_check can be imported
        sys.path.insert(0, os.getcwd())

        from testing_check import (
            Activity,
            Label,
            compile_code,
            extract_schedule,
            initialise_and_personalise_activities,
            initialize_utility,
            run_dp,
        )

        try:
            lib_path = compile_code()
            lib = CDLL(lib_path)

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
            lib.set_activities.argtypes = [POINTER(Activity), c_int]
            lib.main.argtypes = [c_int, POINTER(POINTER(c_char))]
            lib.main.restype = c_int
            lib.get_final_schedule.restype = POINTER(Label)
            lib.free_bucket.restype = None

            lib.set_fixed_initial_soc.argtypes = [c_double]
            lib.set_fixed_initial_soc.restype = None
            lib.set_utility_error_std_dev.argtypes = [c_double]
            lib.set_utility_error_std_dev.restype = None
            lib.set_random_seed.argtypes = [c_int]
            lib.set_random_seed.restype = None

            activities_df = pd.read_csv("{activities_file}")
            params = initialize_utility()

            utility_error_sigma = 1.0
            lib.set_utility_error_std_dev(c_double(utility_error_sigma))
            lib.set_random_seed(c_int({seed}))
            lib.clear_fixed_initial_soc()
            # Force a fixed initial SOC across all runs
            # lib.set_fixed_initial_soc(c_double(0.3))

            activities_array, max_num_activities = initialise_and_personalise_activities(
                activities_df
            )

            result = run_dp(lib, activities_array, max_num_activities, params)
            if result is not None:
                if isinstance(result, tuple):
                    best_label, _ = result
                else:
                    best_label = result

                schedule_df = extract_schedule(best_label, activities_array, activities_df)
                # Record the actual initial SOC used by C for this run
                schedule_df["initial_soc"] = c_double.in_dll(lib, "initial_soc").value
                schedule_df.to_csv("{output_file}", index=False)

            lib.free_bucket()

        except Exception as e:
            print(f"Error: {{e}}", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(1)
        """
    )

    try:
        # Get the directory where this script is located (testing_latest)
        script_dir = os.path.dirname(os.path.abspath(__file__))

        result = subprocess.run(
            ['python3', '-c', script],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=script_dir  # Ensure subprocess runs from testing_latest directory
        )

        if result.returncode == 0 and os.path.exists(output_file):
            return True
        else:
            if result.stderr:
                print(f"  Error in subprocess: {result.stderr[:200]}")
            return False

    except subprocess.TimeoutExpired:
        print("  Timeout")
        return False
    except Exception as e:
        print(f"  Exception: {e}")
        return False


def extract_charging_events_from_schedule(schedule_df, run_id, initial_soc):
    """Extract charging events from a schedule DataFrame."""
    charging_events = []

    for _, row in schedule_df.iterrows():
        if row['charge_duration'] > 0:
            start_hour = int(row['start_time'])
            activity_type = get_activity_display_name(row['act_type'])

            charging_events.append({
                'run_id': run_id,
                'initial_soc': initial_soc,
                'hour': start_hour,
                'activity_type': activity_type,
                'charge_duration': row['charge_duration']
            })

    return charging_events


def run_multiple_simulations(activities_file, num_runs, output_dir):
    """Run multiple simulations using subprocess."""
    print("=" * 80)
    print("CHARGING PARTICIPATION ANALYSIS (Subprocess Method)")
    print("=" * 80)
    print(f"Activities file: {activities_file}")
    print(f"Number of runs: {num_runs}")
    print()

    os.makedirs(output_dir, exist_ok=True)

    all_charging_events = []
    all_schedules = []
    failed_runs = 0

    # Create temp directory for individual run outputs
    temp_dir = tempfile.mkdtemp(prefix="charging_runs_")

    try:
        print(f"Running {num_runs} simulations...")
        # t0 = time.perf_counter()
        for run_id in range(1, num_runs + 1):
            if run_id % 10 == 0:
                print(f"  Completed {run_id}/{num_runs} runs...")

            # Generate a seed based on current time + offset.
            # Keep within 31 bits so it always fits in c_int cleanly.
            seed = (int(time.time() * 1000) + run_id) & 0x7FFFFFFF

            # Output file for this run
            output_file = os.path.join(temp_dir, f"run_{run_id:04d}.csv")

            # Run simulation
            success = run_single_simulation(activities_file, output_file, seed)
            if success:
                # Load schedule
                schedule_df = pd.read_csv(output_file)
                schedule_df['run_id'] = run_id
                initial_soc = float(schedule_df['initial_soc'].iloc[0]) if 'initial_soc' in schedule_df.columns and len(schedule_df) else float("nan")

                # Extract charging events
                charging_events = extract_charging_events_from_schedule(schedule_df, run_id, initial_soc)
                all_charging_events.extend(charging_events)
                all_schedules.append(schedule_df)

                # Clean up temp file
                os.remove(output_file)
            else:
                failed_runs += 1

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"\nCompleted {num_runs - failed_runs}/{num_runs} successful runs")
    if failed_runs > 0:
        print(f"Failed runs: {failed_runs}")

    # Save results
    if all_schedules:
        all_schedules_df = pd.concat(all_schedules, ignore_index=True)
        schedules_file = os.path.join(output_dir, "all_schedules.csv")
        all_schedules_df.to_csv(schedules_file, index=False)
        print(f"\nSaved all schedules to: {schedules_file}")

    if all_charging_events:
        charging_events_df = pd.DataFrame(all_charging_events)
        events_file = os.path.join(output_dir, "charging_events.csv")
        charging_events_df.to_csv(events_file, index=False)
        print(f"Saved charging events to: {events_file}")

    return all_charging_events, all_schedules


def create_participation_data(charging_events, num_runs, output_dir):
    """Create participation data aggregated by hour and activity."""
    if not charging_events:
        print("Warning: No charging events found!")
        return None

    events_df = pd.DataFrame(charging_events)

    # Count unique runs that had charging at each hour-activity combination
    participation = events_df.groupby(['hour', 'activity_type'])['run_id'].nunique().reset_index()
    participation.columns = ['hour', 'activity_type', 'count']

    # Convert to percentage
    participation['percentage'] = (participation['count'] / num_runs) * 100

    # Pivot for stacking
    pivot_data = participation.pivot(index='hour', columns='activity_type', values='percentage')
    pivot_data = pivot_data.fillna(0)

    # Ensure all hours 0-23 are present
    all_hours = pd.DataFrame({'hour': range(24)}).set_index('hour')
    pivot_data = all_hours.join(pivot_data).fillna(0)

    # Sort columns by total participation
    column_totals = pivot_data.sum(axis=0).sort_values(ascending=False)
    pivot_data = pivot_data[column_totals.index]

    # Save
    pivot_file = os.path.join(output_dir, "charging_participation_by_hour.csv")
    pivot_data.to_csv(pivot_file)
    print(f"\nSaved participation data to: {pivot_file}")

    return pivot_data


def print_summary_statistics(charging_events, all_schedules):
    """Print summary statistics."""
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)

    num_runs = len(all_schedules)

    # Runs with charging
    runs_with_charging = len(set(e['run_id'] for e in charging_events))
    print(f"Runs with charging: {runs_with_charging}/{num_runs} ({runs_with_charging/num_runs*100:.1f}%)")

    if charging_events:
        events_df = pd.DataFrame(charging_events)

        # Average number of charging sessions
        avg_sessions = events_df.groupby('run_id').size().mean()
        print(f"Average charging sessions per run: {avg_sessions:.2f}")

        # Average charging hours per run (total hours charged)
        avg_hours = events_df.groupby('run_id')['charge_duration'].sum().mean()
        print(f"Average charging hours per run: {avg_hours:.2f} hours")

        # Total charging hours across all runs
        total_hours = events_df['charge_duration'].sum()
        print(f"Total charging hours (all runs): {total_hours:.2f} hours")

        # Most common charging locations
        print("\nMost common charging locations:")
        location_counts = events_df['activity_type'].value_counts()
        for activity, count in location_counts.head(5).items():
            pct = (count / len(events_df)) * 100
            print(f"  {activity}: {count} sessions ({pct:.1f}%)")

        # Peak charging hours
        print("\nPeak charging hours:")
        hour_counts = events_df['hour'].value_counts().sort_index()
        top_hours = hour_counts.nlargest(5)
        for hour, count in top_hours.items():
            pct = (count / len(events_df)) * 100
            print(f"  {hour}:00-{hour+1}:00: {count} sessions ({pct:.1f}%)")

    # SOC statistics
    if all_schedules:
        all_schedules_df = pd.concat(all_schedules, ignore_index=True)
        final_socs = all_schedules_df.groupby('run_id')['soc_end'].last()

        print(f"\nFinal SOC statistics:")
        print(f"  Mean: {final_socs.mean():.2%}")
        print(f"  Median: {final_socs.median():.2%}")
        print(f"  Std Dev: {final_socs.std():.2%}")
        print(f"  Min: {final_socs.min():.2%}")
        print(f"  Max: {final_socs.max():.2%}")


def main():
    """Main function."""
    # Ensure we're running from the testing_latest directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    activities_file = "dylan/activities_with_charge_shop_errands_and_service_station_free.csv"
    num_runs = 1000
    output_dir = "charging_participation_results"

    # Check if file exists
    if not os.path.exists(activities_file):
        print(f"Error: Activities file not found: {activities_file}")
        print(f"Current directory: {os.getcwd()}")
        print(f"Looking for: {os.path.abspath(activities_file)}")
        return

    # Run simulations
    t0 = time.perf_counter()
    charging_events, all_schedules = run_multiple_simulations(
        activities_file=activities_file,
        num_runs=num_runs,
        output_dir=output_dir
    )
    wall = time.perf_counter() - t0
    print(f"total wall seconds: {wall:.3f}")

    # Print summary
    if charging_events and all_schedules:
        print_summary_statistics(charging_events, all_schedules)

        # Create participation data
        create_participation_data(charging_events, len(all_schedules), output_dir)

    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    main()
