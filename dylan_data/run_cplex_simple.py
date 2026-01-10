"""
Simplified script to run CPLEX optimization on Dylan's schedule.

This is a minimal version that runs the CPLEX model and saves results
in a format comparable to your DP output.
"""

import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

print("="*80)
print("CPLEX OPTIMIZATION - Dylan Schedule")
print("="*80)

# Step 1: Check dependencies
print("\nStep 1: Checking dependencies...")
missing_deps = []

try:
    import pandas as pd
    print("  ✓ pandas")
except ImportError:
    missing_deps.append("pandas")
    print("  ✗ pandas")

try:
    import numpy as np
    print("  ✓ numpy")
except ImportError:
    missing_deps.append("numpy")
    print("  ✗ numpy")

try:
    from docplex.mp.model import Model
    print("  ✓ docplex (CPLEX)")
except ImportError:
    missing_deps.append("docplex")
    print("  ✗ docplex (CPLEX)")

try:
    import pickle
    print("  ✓ pickle")
except ImportError:
    missing_deps.append("pickle")
    print("  ✗ pickle")

try:
    from geopy.distance import distance
    print("  ✓ geopy")
except ImportError:
    missing_deps.append("geopy")
    print("  ✗ geopy")

if missing_deps:
    print("\n" + "="*80)
    print("❌ ERROR: Missing required dependencies!")
    print("="*80)
    print("\nPlease install the following:")
    for dep in missing_deps:
        if dep == "docplex":
            print(f"\n  {dep}:")
            print("    Option 1 (Academic license - FREE):")
            print("      1. Register at: https://www.ibm.com/academic/technology/data-science")
            print("      2. pip install docplex")
            print("    Option 2 (Trial):")
            print("      Get 90-day trial from IBM")
        else:
            print(f"  pip install {dep}")

    print("\nFor quick comparison without CPLEX:")
    print("  python tests/test_dylan_schedule.py  # Run DP only")
    sys.exit(1)

# Step 2: Try to import model functions
print("\nStep 2: Importing CPLEX model...")
try:
    from model import optimize_schedule
    from data_utils import cplex_to_df, create_dicts, compute_distances_from_tmat, add_new_coordinate
    print("  ✓ Model imported successfully")
except ImportError as e:
    print(f"  ✗ Import error: {e}")
    print("\nMake sure you're in the dylan_data/ directory or running from project root")
    sys.exit(1)

# Step 3: Load data
print("\nStep 3: Loading Dylan's schedule...")
script_dir = Path(__file__).parent

try:
    sched = pd.read_csv(script_dir / "dylan_schedule.csv", index_col=0)
    print(f"  ✓ Loaded {len(sched)} activities")
except FileNotFoundError:
    print(f"  ✗ dylan_schedule.csv not found in {script_dir}")
    sys.exit(1)

# Step 4: Prepare data (same as notebook Cell 2)
print("\nStep 4: Preparing data...")

# Convert location strings to tuples
sched['location'] = sched.location.apply(lambda x: tuple(map(float, x[1:-1].split(','))))

# Add mode and charger access
sched['mode'] = 'driving'
sched['charger_access'] = 'YES'

# Update labels
mask = (sched['label'] != 'dawn') & (sched['label'] != 'dusk')
sched.loc[mask, 'label'] = sched.loc[mask, 'label'].apply(lambda x: x + ' (car)')

# Create group column
sched['group'] = sched.loc[:, 'act_label']
sched.loc[0, 'group'] = 'dawn'
sched.loc[sched.index[-1], 'group'] = 'dusk'

# Drop errands if it exists (row index 2 in original)
if 2 in sched.index:
    sched.drop(2, inplace=True)

# Add service station
servicestation = {
    'act_id': 6,
    'act_label': 'Service station',
    'label': 'rapid (car)',
    'start_time': 10,
    'end_time': 10,
    'duration': 0,
    'feasible_start': 0,
    'feasible_end': 24,
    'location': (46.61402, 6.50511),
    'loc_id': 4,
    'categories': 'mandat',
    'flex_early': 0,
    'flex_late': -0.61,
    'flex_short': -0.61,
    'flex_long': -2.4,
    'mode': 'driving'
}

if servicestation['mode'] == 'driving':
    sched = pd.concat([sched, pd.DataFrame([servicestation])], ignore_index=True)

sched = sched.sort_values(by='start_time').reset_index(drop=True)
sched['charger_access'] = sched['act_id'].apply(lambda x: 'YES' if (x != 9) else 'NO')
sched["group"] = sched.label.apply(lambda x: x.split(" ")[0])

print(f"  ✓ Prepared schedule with {len(sched)} activities (including service station)")

# Load travel times
print("\nStep 5: Loading travel times...")
try:
    tt_driving = pickle.load(open(script_dir / "dylan_traveltimes.csv", "rb"))
    print(f"  ✓ Loaded travel times for {len(tt_driving)} locations")
except FileNotFoundError:
    print(f"  ✗ dylan_traveltimes.csv not found")
    sys.exit(1)

# Add service station to travel times
new_coordinate = (46.61402, 6.50511)
new_distances = {
    (46.6031, 6.77133): 0.025,
    (46.5884, 6.77146): 0.015,
    (46.5258, 6.62997): 0.02,
    (46.6696, 6.80041): 0.31,
    (46.6707, 6.79948): 0.109,
    (46.53, 6.62772): 0.29
}

tt_driving = add_new_coordinate(tt_driving, new_coordinate, new_distances)
travel_times = {'driving': tt_driving, 'bicycling': tt_driving, 'transit': tt_driving}

# Compute distances
distances = compute_distances_from_tmat(tt_driving)
print(f"  ✓ Computed distance matrix")

# Step 6: Run optimization
print("\nStep 6: Running CPLEX optimization...")
print("  This may take ~10 seconds per iteration...")
print("  Running 1 iteration (change n=1 to n=10 for full stochastic analysis)")

import time
start_time = time.time()

try:
    n_iter = 0
    initial_soc = 0.3  # 30% initial SOC
    var = 10  # Variance for error terms

    sol, figure, solution_value, mode_figure = optimize_schedule(
        sched,
        travel_times,
        distances,
        n_iter,
        initial_soc,
        var=var
    )

    computation_time = time.time() - start_time
    print(f"\n  ✓ Optimization completed in {computation_time:.2f} seconds")
    print(f"  ✓ Solution value: {solution_value:.2f}")

except Exception as e:
    print(f"\n  ✗ Optimization failed: {e}")
    print("\nThis could be due to:")
    print("  - CPLEX license issues")
    print("  - Infeasible problem")
    print("  - Missing constraints")
    sys.exit(1)

# Step 7: Save results
print("\nStep 7: Saving results...")

if not sol.empty:
    # Save CPLEX solution
    output_file = script_dir / "dylan_optimal_schedule_cplex.csv"
    sol.to_csv(output_file, index=False)
    print(f"  ✓ CPLEX schedule saved to: {output_file}")

    # Create metrics file
    metrics = {
        'method': 'CPLEX',
        'total_activities': len(sol),
        'computation_time': computation_time,
        'initial_soc': initial_soc,
        'final_soc': sol['soc'].iloc[-1] if 'soc' in sol.columns else None,
        'charging_sessions': sol['charging'].sum() if 'charging' in sol.columns else 0,
        'objective_value': solution_value,
    }

    metrics_file = script_dir / "dylan_cplex_metrics.csv"
    pd.DataFrame([metrics]).to_csv(metrics_file, index=False)
    print(f"  ✓ Metrics saved to: {metrics_file}")

    # Display summary
    print("\n" + "="*80)
    print("CPLEX RESULTS SUMMARY")
    print("="*80)
    print(f"\nActivities scheduled: {len(sol)}")
    print(f"Computation time: {computation_time:.2f} seconds")
    print(f"Objective value: {solution_value:.2f}")

    if 'soc' in sol.columns:
        print(f"Initial SOC: {initial_soc:.1%}")
        print(f"Final SOC: {sol['soc'].iloc[-1]:.1%}")

    if 'charging' in sol.columns:
        print(f"Charging sessions: {int(sol['charging'].sum())}")

    print("\n" + "="*80)
    print("NEXT STEPS")
    print("="*80)
    print("\n1. Compare with DP results:")
    print("   python dylan_data/visualize_comparison.py")
    print("\n2. View schedules:")
    print(f"   cat {output_file}")
    print("   cat dylan_data/dylan_optimal_schedule_dp.csv")
    print("\n3. For full stochastic analysis:")
    print("   Edit this file: change 'n_iter = 0' to loop over 10 iterations")
    print("   Or run: jupyter notebook dylan_data/solution_analysis.ipynb")

else:
    print("\n  ✗ Solution is empty - optimization may have failed")
    sys.exit(1)

print("\n" + "="*80)
print("SUCCESS!")
print("="*80)
