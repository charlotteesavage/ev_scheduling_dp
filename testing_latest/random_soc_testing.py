"""
Testing with random initial SOC using time-based seeds.

This script demonstrates how to:
1. Use time(NULL) equivalent in Python for random seeds
2. Run multiple tests with different random starting SOC values
3. Compare results across different random initializations
"""

import pandas as pd
from ctypes import c_int, c_double, CDLL, POINTER, c_char
import time
import os
from pathlib import Path
from testing_check import (
    Activity, Label,
    compile_code,
    initialise_and_personalise_activities,
    initialize_utility,
    run_dp,
    extract_schedule
)


def run_with_random_soc(lib, activities_df, params, seed=None):
    """
    Run schedule with random initial SOC using a seed.

    Args:
        lib: Compiled C library
        activities_df: Activities DataFrame
        params: Utility parameters
        seed: Random seed (if None, uses current time)

    Returns:
        dict with results
    """
    # If no seed provided, use time (equivalent to time(NULL) in C)
    if seed is None:
        seed = int(time.time())

    print(f"\nRunning with seed: {seed}")

    # Set the random seed in C code
    lib.set_random_seed(c_int(seed))

    # Clear any fixed SOC (we want random)
    lib.clear_fixed_initial_soc()

    # Prepare activities
    activities_array, max_num_activities = initialise_and_personalise_activities(
        activities_df
    )

    # Run DP
    best_label = run_dp(lib, activities_array, max_num_activities, params)

    if not best_label:
        return None

    # Extract schedule
    schedule_df = extract_schedule(best_label, activities_array, activities_df)

    # Get the initial SOC that was randomly generated
    initial_soc = schedule_df['soc_start'].iloc[0]
    final_soc = schedule_df['soc_end'].iloc[-1]
    utility = schedule_df['utility'].iloc[-1]

    print(f"  Random initial SOC: {initial_soc:.2%}")
    print(f"  Final SOC: {final_soc:.2%}")
    print(f"  Utility: {utility:.2f}")

    # Cleanup
    lib.free_bucket()

    return {
        'seed': seed,
        'initial_soc': initial_soc,
        'final_soc': final_soc,
        'soc_change': final_soc - initial_soc,
        'utility': utility,
        'schedule': schedule_df
    }


def compare_random_initializations(lib, activities_file, params, num_runs=10, output_dir=None):
    """
    Run multiple tests with different random SOC initializations and compare results.

    This simulates the variability you'd see in real-world scenarios where
    people start their day with different battery levels.

    Args:
        lib: Compiled C library
        activities_file: Path to activities CSV
        params: Utility parameters
        num_runs: Number of random runs to perform
        output_dir: Directory to save results (optional)

    Returns:
        DataFrame with comparison of all runs
    """
    print(f"\n{'='*80}")
    print(f"RANDOM INITIAL SOC TESTING - {num_runs} runs")
    print(f"Each run will have a different random starting SOC")
    print(f"{'='*80}")

    # Load activities
    activities_df = pd.read_csv(activities_file)

    results = []

    for run_num in range(1, num_runs + 1):
        print(f"\n{'='*60}")
        print(f"RUN {run_num}/{num_runs}")
        print(f"{'='*60}")

        # Generate seed based on current time + offset
        # This ensures each run gets a different seed even in quick succession
        seed = int(time.time() * 1000) + run_num  # millisecond precision

        # Run with random SOC
        result = run_with_random_soc(lib, activities_df, params, seed)

        if result:
            result['run'] = run_num
            results.append(result)

            # Save individual schedule if output directory provided
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
                result['schedule'].to_csv(
                    output_path / f"run_{run_num:03d}_seed_{seed}_soc_{int(result['initial_soc']*100)}.csv",
                    index=False
                )

        # Small delay to ensure different timestamps
        time.sleep(0.01)

    # Analyze results
    if results:
        summary_data = []
        for r in results:
            summary_data.append({
                'Run': r['run'],
                'Seed': r['seed'],
                'Initial_SOC': r['initial_soc'],
                'Final_SOC': r['final_soc'],
                'SOC_Change': r['soc_change'],
                'Utility': r['utility']
            })

        summary_df = pd.DataFrame(summary_data)

        print(f"\n{'='*80}")
        print("COMPARISON OF RANDOM INITIALIZATIONS")
        print(f"{'='*80}")
        print(summary_df.to_string(index=False))

        print(f"\n{'='*80}")
        print("STATISTICS ACROSS ALL RUNS")
        print(f"{'='*80}")
        print(f"Initial SOC - Mean: {summary_df['Initial_SOC'].mean():.2%}, "
              f"Std: {summary_df['Initial_SOC'].std():.2%}, "
              f"Min: {summary_df['Initial_SOC'].min():.2%}, "
              f"Max: {summary_df['Initial_SOC'].max():.2%}")
        print(f"Final SOC   - Mean: {summary_df['Final_SOC'].mean():.2%}, "
              f"Std: {summary_df['Final_SOC'].std():.2%}, "
              f"Min: {summary_df['Final_SOC'].min():.2%}, "
              f"Max: {summary_df['Final_SOC'].max():.2%}")
        print(f"SOC Change  - Mean: {summary_df['SOC_Change'].mean():+.2%}, "
              f"Std: {summary_df['SOC_Change'].std():.2%}")
        print(f"Utility     - Mean: {summary_df['Utility'].mean():.2f}, "
              f"Std: {summary_df['Utility'].std():.2f}")

        # Check correlation between initial SOC and outcomes
        corr_soc_utility = summary_df['Initial_SOC'].corr(summary_df['Utility'])
        corr_soc_change = summary_df['Initial_SOC'].corr(summary_df['SOC_Change'])

        print(f"\n{'='*80}")
        print("CORRELATIONS")
        print(f"{'='*80}")
        print(f"Initial SOC vs Utility: {corr_soc_utility:.3f}")
        print(f"Initial SOC vs SOC Change: {corr_soc_change:.3f}")

        if abs(corr_soc_utility) > 0.5:
            print(f"\n⚠️  Strong correlation between starting SOC and utility!")
            print(f"   Starting battery level significantly affects daily utility.")

        if corr_soc_change < -0.5:
            print(f"\n⚠️  People starting with higher SOC tend to end with lower SOC change")
            print(f"   This might indicate they charge less when starting high.")

        # Save summary
        if output_dir:
            output_path = Path(output_dir)
            summary_df.to_csv(output_path / "random_soc_comparison.csv", index=False)
            print(f"\nSummary saved to: {output_path / 'random_soc_comparison.csv'}")

        return summary_df
    else:
        print("\nNo successful runs to compare")
        return None


def test_reproducibility(lib, activities_file, params):
    """
    Test that using the same seed produces the same initial SOC.

    This verifies that the random number generator is working correctly.
    """
    print(f"\n{'='*80}")
    print("REPRODUCIBILITY TEST")
    print(f"{'='*80}")
    print("Running the same seed twice should produce identical initial SOC\n")

    activities_df = pd.read_csv(activities_file)

    # Use a fixed seed
    test_seed = 42

    # Run 1
    print("Run 1 with seed 42:")
    result1 = run_with_random_soc(lib, activities_df, params, seed=test_seed)

    # Run 2 with same seed
    print("\nRun 2 with seed 42:")
    result2 = run_with_random_soc(lib, activities_df, params, seed=test_seed)

    if result1 and result2:
        print(f"\n{'='*80}")
        print("REPRODUCIBILITY RESULTS")
        print(f"{'='*80}")
        print(f"Run 1 Initial SOC: {result1['initial_soc']:.6f}")
        print(f"Run 2 Initial SOC: {result2['initial_soc']:.6f}")

        if abs(result1['initial_soc'] - result2['initial_soc']) < 1e-10:
            print("\n✓ PASS: Same seed produces identical initial SOC")
            print("  Random number generator is working correctly")
        else:
            print("\n✗ FAIL: Same seed produced different initial SOC")
            print("  There may be an issue with the random number generator")


def main():
    """Main execution."""
    print("="*80)
    print("RANDOM SOC TESTING WITH TIME-BASED SEEDS")
    print("="*80)

    # Compile C code
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
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype = None
    lib.clear_fixed_initial_soc.restype = None
    lib.set_random_seed.argtypes = [c_int]
    lib.set_random_seed.restype = None

    # Setup
    activities_file = "testing_latest/person_ending_1263/activities_with_charge_values.csv"
    if not os.path.exists(activities_file):
        print(f"Error: {activities_file} not found")
        return

    params = initialize_utility()
    output_dir = "testing_latest/random_soc_results"

    # Test 1: Reproducibility
    test_reproducibility(lib, activities_file, params)

    # Test 2: Multiple random runs
    summary_df = compare_random_initializations(
        lib=lib,
        activities_file=activities_file,
        params=params,
        num_runs=10,
        output_dir=output_dir
    )

    print(f"\n{'='*80}")
    print("Testing complete!")
    print(f"Results saved to: {output_dir}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
