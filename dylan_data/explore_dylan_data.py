"""
Explore Dylan's data without running the full CPLEX optimization.
This shows you the input format and structure.
"""

import pandas as pd
import pickle
import sys
from pathlib import Path

def main():
    print("="*70)
    print("EXPLORING DYLAN'S CPLEX DATA")
    print("="*70)

    # Get the dylan_data directory
    data_dir = Path(__file__).parent

    # 1. Load the schedule CSV
    print("\n" + "="*70)
    print("1. SCHEDULE DATA (dylan_schedule.csv)")
    print("="*70)
    schedule_path = data_dir / "dylan_schedule.csv"

    if schedule_path.exists():
        sched = pd.read_csv(schedule_path, index_col=0)
        print(f"\nShape: {sched.shape}")
        print(f"\nColumns: {list(sched.columns)}")
        print("\nFirst few rows:")
        print(sched.head(10))

        print("\n" + "-"*70)
        print("Activity Summary:")
        print(f"  - Number of activities: {len(sched)}")
        print(f"  - Activity types: {sched['act_label'].unique().tolist()}")
        print(f"  - Time range: {sched['start_time'].min():.2f}h - {sched['end_time'].max():.2f}h")
        print(f"  - Total duration: {sched['duration'].sum():.2f} hours")

        # Parse locations
        sched['location_parsed'] = sched['location'].apply(
            lambda x: tuple(map(float, x[1:-1].split(','))) if isinstance(x, str) else x
        )
        print(f"\n  - Unique locations: {len(sched['location_parsed'].unique())}")
        for idx, row in sched.iterrows():
            print(f"    {row['act_label']:20s} @ {row['location_parsed']}")

    else:
        print(f"ERROR: {schedule_path} not found!")
        return

    # 2. Load travel times
    print("\n" + "="*70)
    print("2. TRAVEL TIMES (dylan_traveltimes.csv - pickle format)")
    print("="*70)
    tt_path = data_dir / "dylan_traveltimes.csv"

    if tt_path.exists():
        try:
            tt_driving = pickle.load(open(tt_path, "rb"))
            print(f"\nTravel time matrix type: {type(tt_driving)}")
            print(f"Number of locations: {len(tt_driving)}")
            print(f"\nLocations in travel time matrix:")
            for i, loc in enumerate(tt_driving.keys(), 1):
                print(f"  {i}. {loc}")

            # Show a sample of travel times
            print("\nSample travel times (hours):")
            locs = list(tt_driving.keys())
            if len(locs) >= 2:
                for i in range(min(3, len(locs))):
                    for j in range(min(3, len(locs))):
                        if i != j:
                            origin = locs[i]
                            dest = locs[j]
                            tt = tt_driving[origin][dest]
                            print(f"  {origin} → {dest}: {tt:.3f} hours")
        except Exception as e:
            print(f"ERROR reading travel times: {e}")
    else:
        print(f"ERROR: {tt_path} not found!")

    # 3. Show what the CPLEX model expects
    print("\n" + "="*70)
    print("3. CPLEX MODEL INPUT FORMAT (from model.py)")
    print("="*70)
    print("""
The optimize_schedule() function expects:

Input Arguments:
  - df: Schedule dataframe with columns:
      * act_id, act_label, label
      * start_time, end_time, duration (in hours, float)
      * feasible_start, feasible_end (time windows)
      * location (lat, lon tuple)
      * flex_early, flex_late, flex_short, flex_long (penalty params)
      * group (activity group for constraints)
      * mode (travel mode: 'driving', 'transit', etc.)
      * charger_access ('YES' or 'NO')

  - travel_times: Nested dict {mode: {origin_loc: {dest_loc: time_hours}}}

  - distances: Nested dict {origin_loc: {dest_loc: distance_km}}

  - n_iter: Iteration number (for stochastic terms)

  - initial_soc: Initial state of charge (0.0-1.0)

  - var: Variance for error terms

Output:
  - solution_df: Dataframe with optimized schedule including:
      * All input columns
      * soc: State of charge at activity start
      * charging: Binary (1 if charging, 0 otherwise)
      * charging_duration: Duration of charging
      * charge_time_type1/2/3: Time for each charger type
      * charger_level1/2/3: Binary for charger type selection
      * car_avail: Car availability
      * travel_time: Travel time to next activity
""")

    # 4. Show service station addition
    print("\n" + "="*70)
    print("4. SERVICE STATION (CHARGING LOCATION)")
    print("="*70)
    print("""
The notebook adds a service station with rapid charging:

servicestation = {
    'act_id': 6,
    'act_label': 'Service station',
    'label': 'rapid (car)',
    'start_time': 10,  # 10am
    'end_time': 10,
    'duration': 0,  # Flexible
    'feasible_start': 0,
    'feasible_end': 24,
    'location': (46.61402, 6.50511),
    'loc_id': 4,
    'flex_early': 0,
    'flex_late': -0.61,
    'flex_short': -0.61,
    'flex_long': -2.4,
    'mode': 'driving'
}

This represents an optional charging stop with rapid charging capabilities.
""")

    # 5. Key differences from your DP format
    print("\n" + "="*70)
    print("5. KEY DIFFERENCES: CPLEX vs YOUR DP ALGORITHM")
    print("="*70)
    print("""
CPLEX Format (Dylan):              DP Format (Your Code):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Time:                              Time:
  - Continuous hours (0.0-24.0)      - Discrete intervals (0-288)
  - Float values like 15.083           - Integer: 181 = (15.083*60)/5

Location:                          Location:
  - Lat/lon tuples: (46.60, 6.77)    - x,y in meters: (5,173,110, 751,913)
  - Direct coordinates                 - Projected coordinates

Activities:                        Activities:
  - Semantic labels: 'escort1'       - Numeric groups: 0-7
  - act_label: 'escort'               - group: 7 (for escort)

Charging:                          Charging:
  - Added dynamically in notebook    - Must be in CSV as columns
  - Complex 3-type system             - charge_mode: 0/1/2/3
  - Cost optimization                 - is_charging: 0/1
                                      - is_service_station: 0/1

Flexibility:                       Flexibility:
  - Explicit penalty parameters      - Computed in utility function
  - flex_early, flex_late, etc.      - Using early/late_parameters arrays

Optimization:                      Optimization:
  - CPLEX: continuous variables      - DP: discrete state space
  - Global optimum                   - Pareto-optimal labels
  - ~10 seconds per solve            - Sub-second typical
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

    print("\n" + "="*70)
    print("NEXT STEPS")
    print("="*70)
    print("""
To adapt Dylan's data for your DP algorithm:

1. Run the conversion script:
   python dylan_data/convert_dylan_to_dp.py

2. This will create: dylan_data/dylan_schedule_dp_format.csv

3. Then test with your DP algorithm:
   python tests/test_dylan_schedule.py

4. Compare results between CPLEX and DP approaches

Note: The CPLEX notebook requires IBM CPLEX license to run the
optimization. But you now understand the data format!
""")
    print("="*70)

if __name__ == "__main__":
    main()
