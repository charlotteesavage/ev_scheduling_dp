"""
Test script for running DP algorithm on Dylan's CPLEX schedule data.

This script runs your DP algorithm on Dylan's converted schedule to enable
comparison between the DP and CPLEX approaches.
"""

import sys
from pathlib import Path
import pandas as pd
import time

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from main_slice_cs_test import (
    compile_code,
    initialize_utility,
    load_test_activities,
    initialize_and_personalize_activities,
    run_dp,
    extract_schedule,
    Activity,
    Label,
    CDLL,
    POINTER,
    c_int,
    c_double,
    c_char,
    TIME_INTERVAL,
    HORIZON,
)


def print_schedule_comparison(schedule_df):
    """Print schedule in a format comparable to Dylan's CPLEX output."""
    print("\n" + "="*80)
    print("SCHEDULE DETAILS")
    print("="*80)

    print(f"\n{'ID':<4} {'Activity':<20} {'Start':<12} {'Duration':<12} "
          f"{'SOC In':<8} {'SOC Out':<8} {'Charging':<10}")
    print("-"*80)

    for idx, row in schedule_df.iterrows():
        start_str = f"{row['start_time']:5.2f}h"
        dur_str = f"{row['duration']:5.2f}h"
        soc_in_str = f"{row['soc_start']:.1%}"
        soc_out_str = f"{row['soc_end']:.1%}"
        charging_str = "Yes" if row['is_charging'] else "No"

        print(f"{row['act_id']:<4} {row['act_type']:<20} {start_str:<12} {dur_str:<12} "
              f"{soc_in_str:<8} {soc_out_str:<8} {charging_str:<10}")


def calculate_metrics(schedule_df):
    """Calculate key metrics for comparison."""
    metrics = {
        'total_activities': len(schedule_df),
        'total_duration': schedule_df['duration'].sum(),
        'charging_sessions': int(schedule_df['is_charging'].sum()),
        'total_charging_time': schedule_df['charge_duration'].sum(),
        'total_charging_cost': schedule_df['charge_cost'].max(),
        'initial_soc': schedule_df['soc_start'].iloc[0],
        'final_soc': schedule_df['soc_end'].iloc[-1],
        'min_soc': schedule_df['soc_start'].min(),
        'max_soc': schedule_df['soc_end'].max(),
        'total_utility': schedule_df['utility'].iloc[-1],
    }

    # Calculate SOC statistics
    metrics['soc_below_30'] = (schedule_df['soc_start'] < 0.3).sum()
    metrics['soc_range'] = metrics['max_soc'] - metrics['min_soc']

    # Calculate charging mode breakdown
    if 'charge_mode' in schedule_df.columns:
        charging_acts = schedule_df[schedule_df['is_charging'] == 1]
        if len(charging_acts) > 0:
            metrics['slow_charging_sessions'] = (charging_acts['charge_mode'] == 1).sum()
            metrics['fast_charging_sessions'] = (charging_acts['charge_mode'] == 2).sum()
            metrics['rapid_charging_sessions'] = (charging_acts['charge_mode'] == 3).sum()
        else:
            metrics['slow_charging_sessions'] = 0
            metrics['fast_charging_sessions'] = 0
            metrics['rapid_charging_sessions'] = 0

    return metrics


def print_metrics(metrics):
    """Print metrics in a formatted table."""
    print("\n" + "="*80)
    print("KEY METRICS")
    print("="*80)

    print(f"\n{'Metric':<40} {'Value':<20}")
    print("-"*60)

    print(f"{'Total activities:':<40} {metrics['total_activities']:<20}")
    print(f"{'Total duration:':<40} {metrics['total_duration']:<20.2f} hours")
    print(f"{'Initial SOC:':<40} {metrics['initial_soc']:<20.1%}")
    print(f"{'Final SOC:':<40} {metrics['final_soc']:<20.1%}")
    print(f"{'Minimum SOC:':<40} {metrics['min_soc']:<20.1%}")
    print(f"{'Maximum SOC:':<40} {metrics['max_soc']:<20.1%}")
    print(f"{'SOC range:':<40} {metrics['soc_range']:<20.1%}")
    print(f"{'Times below 30% threshold:':<40} {metrics['soc_below_30']:<20}")

    print(f"\n{'CHARGING STATISTICS':<40}")
    print("-"*60)
    print(f"{'Total charging sessions:':<40} {metrics['charging_sessions']:<20}")
    print(f"{'  - Slow (7kW):':<40} {metrics.get('slow_charging_sessions', 0):<20}")
    print(f"{'  - Fast (22kW):':<40} {metrics.get('fast_charging_sessions', 0):<20}")
    print(f"{'  - Rapid (50kW):':<40} {metrics.get('rapid_charging_sessions', 0):<20}")
    print(f"{'Total charging time:':<40} {metrics['total_charging_time']:<20.2f} hours")
    print(f"{'Total charging cost:':<40} £{metrics['total_charging_cost']:<19.2f}")

    print(f"\n{'OPTIMIZATION':<40}")
    print("-"*60)
    print(f"{'Total utility:':<40} {metrics['total_utility']:<20.2f}")


def main():
    """Main execution function."""
    print("="*80)
    print("DYLAN SCHEDULE - DP ALGORITHM TEST")
    print("="*80)

    # Get paths
    script_dir = Path(__file__).parent
    parent_dir = script_dir.parent
    data_file = parent_dir / "dylan_data" / "dylan_schedule_dp_format.csv"

    # Check if converted data exists
    if not data_file.exists():
        print(f"\n❌ ERROR: Converted data file not found!")
        print(f"Expected: {data_file}")
        print("\nPlease run the conversion script first:")
        print("  python dylan_data/convert_dylan_to_dp.py")
        return

    print(f"\n✓ Found converted data: {data_file}")

    # Compile C code
    print("\n" + "-"*80)
    print("STEP 1: Compiling C code")
    print("-"*80)
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

    # Load data
    print("\n" + "-"*80)
    print("STEP 2: Loading Dylan's converted schedule")
    print("-"*80)
    activities_df = load_test_activities(data_file)

    # Initialize activities
    print("\n" + "-"*80)
    print("STEP 3: Initializing activities")
    print("-"*80)
    activities_array, max_num_activities = initialize_and_personalize_activities(
        activities_df
    )

    # Initialize utility parameters
    print("\n" + "-"*80)
    print("STEP 4: Setting utility parameters")
    print("-"*80)
    params = initialize_utility()
    print("Using standard utility parameters (matching Dylan's CPLEX model)")

    # Run DP
    print("\n" + "-"*80)
    print("STEP 5: Running DP algorithm")
    print("-"*80)
    start_time = time.time()
    best_label = run_dp(lib, activities_array, max_num_activities, params)
    computation_time = time.time() - start_time

    if best_label:
        # Extract and display schedule
        schedule_df = extract_schedule(best_label, activities_array, activities_df)

        print_schedule_comparison(schedule_df)

        # Calculate and print metrics
        metrics = calculate_metrics(schedule_df)
        print_metrics(metrics)

        # Save results
        output_file = parent_dir / "dylan_data" / "dylan_optimal_schedule_dp.csv"
        schedule_df.to_csv(output_file, index=False)

        print("\n" + "="*80)
        print("COMPUTATION PERFORMANCE")
        print("="*80)
        print(f"Total computation time: {computation_time:.4f} seconds")
        print(f"Average time per activity: {computation_time/len(schedule_df):.4f} seconds")

        print("\n" + "="*80)
        print("OUTPUT FILES")
        print("="*80)
        print(f"Schedule saved to: {output_file}")

        # Save metrics
        metrics_file = parent_dir / "dylan_data" / "dylan_dp_metrics.csv"
        metrics_df = pd.DataFrame([metrics])
        metrics_df.to_csv(metrics_file, index=False)
        print(f"Metrics saved to: {metrics_file}")

        print("\n" + "="*80)
        print("COMPARISON WITH CPLEX")
        print("="*80)
        print("\nTo compare with Dylan's CPLEX results:")
        print("1. If you have CPLEX, run: jupyter notebook dylan_data/solution_analysis.ipynb")
        print("2. Compare metrics:")
        print("   - Activity sequences (qualitative)")
        print("   - Charging patterns (when/where/duration)")
        print("   - SOC trajectories")
        print("   - Computation time (DP is typically 10-100x faster)")
        print("\nNote: Results may differ due to:")
        print("  - Different optimization methods (DP vs MILP)")
        print("  - Discrete vs continuous time representation")
        print("  - Deterministic vs stochastic approach")

    else:
        print("\n❌ ERROR: No feasible solution found!")
        print("\nPossible issues:")
        print("  - Time windows too tight")
        print("  - Initial SOC too low for required travel")
        print("  - Charging infrastructure insufficient")
        print("\nCheck the converted data file for correctness:")
        print(f"  cat {data_file}")

    # Cleanup
    lib.free_bucket()

    print("\n" + "="*80)
    print("TEST COMPLETE!")
    print("="*80)


if __name__ == "__main__":
    main()
