import pandas as pd
from ctypes import Structure, c_int, c_double, POINTER, CDLL, c_char
import subprocess
import os
import time
from pathlib import Path
import argparse

# Import all the structures and functions from testing_check.py
# We'll reuse most of the code
from testing_check import (
    Activity, Label, L_list, Group_mem,
    compile_code,
    initialise_and_personalise_activities,
    initialize_utility,
    extract_schedule,
    TIME_INTERVAL, HORIZON, SPEED, TRAVEL_TIME_PENALTY
)


def run_single_day(lib, activities_df, params, initial_soc, day_number):
    """
    Run scheduling for a single day with a specified initial SOC.

    Args:
        lib: The compiled C library
        activities_df: DataFrame with activity data
        params: Utility parameters
        initial_soc: Starting SOC for this day (0.0 to 1.0)
        day_number: Day number for tracking

    Returns:
        dict with schedule, final_soc, and other metrics
    """
    print(f"\n{'='*60}")
    print(f"DAY {day_number} - Starting SOC: {initial_soc:.2%}")
    print(f"{'='*60}")

    # Initialize activities for this day
    activities_array, max_num_activities = initialise_and_personalise_activities(
        activities_df
    )

    # Set the initial SOC for this day
    lib.set_fixed_initial_soc(c_double(initial_soc))

    # Convert parameters to C arrays
    asc_array = (c_double * len(params["asc"]))(*params["asc"])
    early_array = (c_double * len(params["early"]))(*params["early"])
    late_array = (c_double * len(params["late"]))(*params["late"])
    long_array = (c_double * len(params["long"]))(*params["long"])
    short_array = (c_double * len(params["short"]))(*params["short"])

    lib.set_general_parameters(
        HORIZON,
        SPEED,
        TRAVEL_TIME_PENALTY,
        TIME_INTERVAL,
        asc_array,
        early_array,
        late_array,
        long_array,
        short_array,
    )

    # Set activities
    lib.set_activities(activities_array, max_num_activities)

    # Run DP
    start_time = time.time()
    result = lib.main(0, None)
    total_time = time.time() - start_time

    print(f"DP completed in {total_time:.2f} seconds")

    # Get final schedule
    best_label = lib.get_final_schedule()

    if not best_label:
        print("ERROR: No feasible solution found!")
        return None

    # Extract schedule
    schedule_df = extract_schedule(best_label, activities_array, activities_df)

    # Calculate metrics
    final_soc = schedule_df['soc_end'].iloc[-1]
    total_utility = schedule_df['utility'].iloc[-1]
    charging_sessions = schedule_df['is_charging'].sum()
    total_charging_time = schedule_df['charge_duration'].sum()
    total_charging_cost = schedule_df['charge_cost'].max()

    print(f"\nDay {day_number} Summary:")
    print(f"  Initial SOC: {initial_soc:.2%}")
    print(f"  Final SOC: {final_soc:.2%}")
    print(f"  SOC Change: {(final_soc - initial_soc):+.2%}")
    print(f"  Utility: {total_utility:.2f}")
    print(f"  Charging sessions: {charging_sessions}")
    print(f"  Total charging time: {total_charging_time:.2f} hours")
    print(f"  Total charging cost: £{total_charging_cost:.2f}")

    # Cleanup for this day
    lib.clear_fixed_initial_soc()
    lib.free_bucket()

    return {
        'day': day_number,
        'initial_soc': initial_soc,
        'final_soc': final_soc,
        'soc_change': final_soc - initial_soc,
        'utility': total_utility,
        'charging_sessions': charging_sessions,
        'charging_time': total_charging_time,
        'charging_cost': total_charging_cost,
        'schedule': schedule_df,
        'computation_time': total_time
    }


def run_multi_day_simulation(lib, activities_file, params, num_days,
                             starting_soc=0.30, min_soc_threshold=0.20,
                             output_dir=None):
    """
    Run a multi-day simulation where each day's ending SOC becomes the next day's starting SOC.

    Args:
        lib: The compiled C library
        activities_file: Path to the activities CSV file
        params: Utility parameters
        num_days: Number of days to simulate
        starting_soc: Initial SOC for day 1 (default 0.30 = 30%)
        min_soc_threshold: Minimum SOC threshold - simulation stops if SOC drops below this
        output_dir: Directory to save results (optional)

    Returns:
        list of daily results
    """
    print(f"\n{'='*80}")
    print(f"MULTI-DAY SIMULATION: {num_days} days")
    print(f"Starting SOC: {starting_soc:.2%}")
    print(f"Minimum SOC Threshold: {min_soc_threshold:.2%}")
    print(f"{'='*80}")

    # Load activities (same activities repeated each day)
    activities_df = pd.read_csv(activities_file)

    results = []
    current_soc = starting_soc

    for day in range(1, num_days + 1):
        # Check if SOC is too low to continue
        if current_soc < min_soc_threshold:
            print(f"\n{'!'*80}")
            print(f"SIMULATION STOPPED: SOC ({current_soc:.2%}) below minimum threshold ({min_soc_threshold:.2%})")
            print(f"Completed {day - 1} days before SOC became critically low")
            print(f"{'!'*80}")
            break

        # Run this day's schedule
        day_result = run_single_day(lib, activities_df, params, current_soc, day)

        if day_result is None:
            print(f"\n{'!'*80}")
            print(f"SIMULATION STOPPED: No feasible solution found for day {day}")
            print(f"This may indicate the SOC was too low to complete necessary activities")
            print(f"{'!'*80}")
            break

        results.append(day_result)

        # Update SOC for next day
        current_soc = day_result['final_soc']

        # Save individual day schedule if output directory provided
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            day_result['schedule'].to_csv(
                output_path / f"day_{day:03d}_schedule.csv",
                index=False
            )

    return results


def analyze_multi_day_results(results, output_dir=None):
    """
    Analyze and visualize multi-day simulation results.

    Args:
        results: List of daily results from run_multi_day_simulation
        output_dir: Directory to save analysis (optional)
    """
    print(f"\n{'='*80}")
    print(f"MULTI-DAY SIMULATION ANALYSIS")
    print(f"{'='*80}")

    # Create summary DataFrame
    summary_data = []
    for r in results:
        summary_data.append({
            'Day': r['day'],
            'Initial_SOC': r['initial_soc'],
            'Final_SOC': r['final_soc'],
            'SOC_Change': r['soc_change'],
            'Utility': r['utility'],
            'Charging_Sessions': r['charging_sessions'],
            'Charging_Time_hrs': r['charging_time'],
            'Charging_Cost_GBP': r['charging_cost'],
            'Computation_Time_sec': r['computation_time']
        })

    summary_df = pd.DataFrame(summary_data)

    # Print summary
    print("\nDaily Summary:")
    print(summary_df.to_string(index=False))

    # Aggregate statistics
    print(f"\n{'='*80}")
    print(f"AGGREGATE STATISTICS")
    print(f"{'='*80}")
    print(f"Total days simulated: {len(results)}")
    print(f"Starting SOC (Day 1): {results[0]['initial_soc']:.2%}")
    print(f"Ending SOC (Day {len(results)}): {results[-1]['final_soc']:.2%}")
    print(f"Total SOC change: {(results[-1]['final_soc'] - results[0]['initial_soc']):+.2%}")
    print(f"Average daily SOC change: {summary_df['SOC_Change'].mean():+.2%}")
    print(f"Total utility (sum): {summary_df['Utility'].sum():.2f}")
    print(f"Average daily utility: {summary_df['Utility'].mean():.2f}")
    print(f"Total charging sessions: {summary_df['Charging_Sessions'].sum():.0f}")
    print(f"Total charging time: {summary_df['Charging_Time_hrs'].sum():.2f} hours")
    print(f"Total charging cost: £{summary_df['Charging_Cost_GBP'].sum():.2f}")
    print(f"Total computation time: {summary_df['Computation_Time_sec'].sum():.2f} seconds")

    # Check for SOC trends
    if summary_df['SOC_Change'].mean() < -0.01:  # Losing more than 1% per day on average
        print(f"\n⚠️  WARNING: Net negative SOC trend detected!")
        print(f"   Average daily loss: {summary_df['SOC_Change'].mean():.2%}")
        print(f"   This schedule is not sustainable long-term without more charging.")
    elif summary_df['SOC_Change'].mean() > 0.01:  # Gaining more than 1% per day
        print(f"\n✓ Net positive SOC trend detected")
        print(f"   Average daily gain: {summary_df['SOC_Change'].mean():+.2%}")
    else:
        print(f"\n✓ SOC is approximately stable")
        print(f"   Average daily change: {summary_df['SOC_Change'].mean():+.2%}")

    # Save summary if output directory provided
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        summary_df.to_csv(output_path / "multi_day_summary.csv", index=False)
        print(f"\nSummary saved to: {output_path / 'multi_day_summary.csv'}")

    return summary_df


def main():
    """Main execution function for multi-day testing."""
    parser = argparse.ArgumentParser(description="Run multi-day EV scheduling simulation")
    parser.add_argument(
        "--person",
        default="person_ending_1263",
        help="Folder under testing_latest/ containing the activities CSV"
    )
    parser.add_argument(
        "--csv",
        default="activities_with_charge_values.csv",
        help="CSV filename inside the person folder"
    )
    parser.add_argument(
        "--num-days",
        type=int,
        default=7,
        help="Number of days to simulate (default: 7)"
    )
    parser.add_argument(
        "--starting-soc",
        type=float,
        default=0.30,
        help="Starting SOC for day 1 (0.0 to 1.0, default: 0.30)"
    )
    parser.add_argument(
        "--min-soc",
        type=float,
        default=0.20,
        help="Minimum SOC threshold - simulation stops if SOC drops below this (default: 0.20)"
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to save results (default: testing_latest/multi_day_results/<person>)"
    )
    args = parser.parse_args()

    print("="*80)
    print("MULTI-DAY EV CHARGING SCHEDULING TEST")
    print("="*80)

    # Compile C code
    lib_path = compile_code()
    lib = CDLL(lib_path)

    # Set up C function signatures
    lib.set_general_parameters.argtypes = [
        c_int,  # horizon
        c_double,  # speed
        c_double,  # travel_time_penalty
        c_int,  # time_interval
        POINTER(c_double),  # asc
        POINTER(c_double),  # early
        POINTER(c_double),  # late
        POINTER(c_double),  # long
        POINTER(c_double),  # short
    ]
    lib.set_activities.argtypes = [POINTER(Activity), c_int]
    lib.main.argtypes = [c_int, POINTER(POINTER(c_char))]
    lib.main.restype = c_int
    lib.get_final_schedule.restype = POINTER(Label)
    lib.free_bucket.restype = None
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype = None
    lib.clear_fixed_initial_soc.restype = None

    # Set up paths
    person_folder = args.person
    activities_file = f"testing_latest/{person_folder}/{args.csv}"

    if not os.path.exists(activities_file):
        raise FileNotFoundError(f"Missing activities file: {activities_file}")

    # Set output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = f"testing_latest/multi_day_results/{person_folder}"

    # Initialize utility parameters
    params = initialize_utility()

    # Run multi-day simulation
    results = run_multi_day_simulation(
        lib=lib,
        activities_file=activities_file,
        params=params,
        num_days=args.num_days,
        starting_soc=args.starting_soc,
        min_soc_threshold=args.min_soc,
        output_dir=output_dir
    )

    # Analyze results
    if results:
        summary_df = analyze_multi_day_results(results, output_dir)

        print(f"\n{'='*80}")
        print(f"Multi-day simulation complete!")
        print(f"Results saved to: {output_dir}")
        print(f"{'='*80}")
    else:
        print("\nNo results to analyze - simulation failed or was stopped early")


if __name__ == "__main__":
    main()
