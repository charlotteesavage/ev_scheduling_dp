"""
Convert Dylan's CPLEX schedule format to DP algorithm format.

This script transforms Dylan's continuous-time, lat/lon-based schedule
into the discrete-interval, meter-based format required by the DP algorithm.
"""

import pandas as pd
import numpy as np
import pickle
from pathlib import Path

# Constants
TIME_INTERVAL = 5  # minutes
HORIZON = 288  # 24 hours in 5-min intervals


def convert_dylan_schedule_to_dp_format(
    dylan_csv_path,
    output_csv_path,
    add_service_station=True,
    verbose=True
):
    """
    Convert Dylan's schedule format to DP algorithm format.

    Parameters:
    -----------
    dylan_csv_path : str or Path
        Path to Dylan's schedule CSV file
    output_csv_path : str or Path
        Path to save converted DP format CSV
    add_service_station : bool
        Whether to add a service station for charging (default: True)
    verbose : bool
        Print conversion details (default: True)

    Returns:
    --------
    pd.DataFrame : Converted schedule in DP format
    """

    if verbose:
        print("="*70)
        print("CONVERTING DYLAN'S SCHEDULE TO DP FORMAT")
        print("="*70)

    # Load Dylan's schedule
    df = pd.read_csv(dylan_csv_path, index_col=0)

    if verbose:
        print(f"\nLoaded {len(df)} activities from {dylan_csv_path}")

    # Convert location strings to tuples if needed
    if isinstance(df['location'].iloc[0], str):
        df['location'] = df['location'].apply(
            lambda x: tuple(map(float, x[1:-1].split(',')))
        )

    # Activity label to group number mapping
    # These map to your DP algorithm's group numbers (will be -1 in preprocessing)
    activity_to_group = {
        'home': 1,           # Group 0 after preprocessing (special - allows multiple visits)
        'dawn': 1,           # Also Group 0 after preprocessing
        'dusk': 1,           # Also Group 0 after preprocessing
        'escort': 8,         # Group 7 after preprocessing (Escort/Other)
        'errands_services': 3,  # Group 2 after preprocessing (Business/Errands)
        'errands': 3,        # Group 2 after preprocessing
        'leisure': 4,        # Group 3 after preprocessing (Leisure)
        'shopping': 5,       # Group 4 after preprocessing (Shopping)
        'work': 2,           # Group 1 after preprocessing (Work)
        'education': 5,      # Group 4 after preprocessing (Education)
        'business_trip': 3,  # Group 2 after preprocessing (Business)
    }

    # Create output dataframe with DP format
    dp_activities = []

    for idx, row in df.iterrows():
        # Extract x, y from location tuple
        lat, lon = row['location']

        # Convert lat/lon to meters using simple approximation
        # At mid-latitudes: 1 degree lat ≈ 111 km, 1 degree lon ≈ 111 * cos(lat) km
        # For simplicity, we use 111 km per degree for both
        x_meters = int(lon * 111000)  # longitude → x (meters)
        y_meters = int(lat * 111000)  # latitude → y (meters)

        # Convert hours to 5-minute intervals
        earliest_start = int(row['feasible_start'] * 60 / TIME_INTERVAL)
        latest_start = int(row['feasible_end'] * 60 / TIME_INTERVAL)

        # Calculate desired start time in intervals
        des_start_intervals = int(row['start_time'] * 60 / TIME_INTERVAL)

        # Calculate desired duration in intervals
        des_duration_intervals = int(row['duration'] * 60 / TIME_INTERVAL)

        # Calculate min/max duration based on desired duration and flexibility
        # Use flex_short and flex_long if available, otherwise allow ±1 hour
        if 'flex_short' in row and 'flex_long' in row:
            # Flexibility parameters represent penalties, so larger absolute values = less flexible
            # For now, use a simple heuristic: allow ±20% of desired duration
            duration_flexibility = max(12, int(des_duration_intervals * 0.2))  # at least 1 hour
        else:
            duration_flexibility = 12  # ±1 hour default

        min_duration = max(1, des_duration_intervals - duration_flexibility)
        max_duration = des_duration_intervals + duration_flexibility

        # Determine group from activity label
        # Get base label (e.g., "escort1" → "escort")
        if 'act_label' in row:
            base_label = str(row['act_label']).lower()
        else:
            # Try to extract from label
            base_label = str(row['label']).split()[0].lower()

        # Handle numbered activities (e.g., "escort1", "escort2")
        base_label = ''.join([c for c in base_label if not c.isdigit()])

        group = activity_to_group.get(base_label, 1)  # Default to home group if unknown

        # Determine charging parameters
        # For Dylan's data, we'll start with no charging except at service stations
        charge_mode = 0
        is_charging = 0
        is_service_station = 0

        # Check if this is a service station
        if 'service' in base_label.lower() or 'station' in base_label.lower():
            is_service_station = 1
            charge_mode = 3  # Rapid charging at service stations
            is_charging = 1
            group = 6  # Service station group (will be 5 after preprocessing)

        dp_act = {
            'id': idx,
            'activity_idx': idx,
            'act_type': row['act_label'] if 'act_label' in row else row['label'],
            'x': x_meters,
            'y': y_meters,
            'group': group,
            'earliest_start': earliest_start,
            'latest_start': latest_start,
            'min_duration': min_duration,
            'max_duration': max_duration,
            'des_start_time': des_start_intervals,
            'des_duration': des_duration_intervals,
            'charge_mode': charge_mode,
            'is_charging': is_charging,
            'is_service_station': is_service_station,
        }
        dp_activities.append(dp_act)

    # Create dataframe
    dp_df = pd.DataFrame(dp_activities)

    # Optional: Add a service station if not already present
    if add_service_station and not any(dp_df['is_service_station']):
        if verbose:
            print("\nAdding service station for charging...")

        # Add service station between existing activities
        # Place it roughly mid-day
        service_station = {
            'id': len(dp_df),
            'activity_idx': len(dp_df),
            'act_type': 'Service station',
            'x': int(6.5 * 111000),  # Mid-point longitude
            'y': int(46.6 * 111000), # Mid-point latitude
            'group': 6,  # Service station group (will be 5 after preprocessing)
            'earliest_start': 0,
            'latest_start': HORIZON - 12,  # Must finish at least 1 hour before end
            'min_duration': 2,  # 10 minutes minimum (rapid charging)
            'max_duration': 24,  # 2 hours maximum
            'des_start_time': 120,  # 10am suggestion
            'des_duration': 6,  # 30 minutes (rapid charging to 80%)
            'charge_mode': 3,  # Rapid charging
            'is_charging': 1,
            'is_service_station': 1,
        }

        # Insert before dusk (last activity)
        dp_df = pd.concat([
            dp_df.iloc[:-1],
            pd.DataFrame([service_station]),
            dp_df.iloc[-1:]
        ], ignore_index=True)

        # Renumber IDs
        dp_df['id'] = range(len(dp_df))
        dp_df['activity_idx'] = range(len(dp_df))

    # Save to CSV
    dp_df.to_csv(output_csv_path, index=False)

    if verbose:
        print(f"\n{'='*70}")
        print("CONVERSION COMPLETE")
        print(f"{'='*70}")
        print(f"\nConverted schedule saved to: {output_csv_path}")
        print(f"Number of activities: {len(dp_df)}")
        print(f"Activities with charging: {dp_df['is_charging'].sum()}")
        print(f"Service stations: {dp_df['is_service_station'].sum()}")

        print(f"\n{'='*70}")
        print("ACTIVITY SUMMARY")
        print(f"{'='*70}")
        for idx, row in dp_df.iterrows():
            print(f"{row['id']:2d}. {row['act_type']:20s} | "
                  f"Group: {row['group']} | "
                  f"Start: {row['des_start_time']:3d} ({row['des_start_time']*TIME_INTERVAL/60:5.1f}h) | "
                  f"Dur: {row['des_duration']:3d} ({row['des_duration']*TIME_INTERVAL/60:5.1f}h) | "
                  f"Charging: {row['is_charging']}")

        print(f"\n{'='*70}")
        print("CONVERSION MAPPING")
        print(f"{'='*70}")
        print("Time conversion:")
        print(f"  Original (hours) → DP (5-min intervals)")
        print(f"  Example: 15.083h → {int(15.083*60/5)} intervals")
        print(f"\nLocation conversion:")
        print(f"  Original (lat, lon) → DP (x, y in meters)")
        print(f"  Formula: meters = degrees * 111000")
        print(f"\nGroup mapping (note: -1 in main_slice_cs_test.py preprocessing):")
        for label, group in sorted(activity_to_group.items(), key=lambda x: x[1]):
            print(f"  {label:20s} → Group {group} (becomes {group-1} after preprocessing)")

    return dp_df


def main():
    """Main execution function."""
    # Get the dylan_data directory
    script_dir = Path(__file__).parent

    # Input and output paths
    input_csv = script_dir / "dylan_schedule.csv"
    output_csv = script_dir / "dylan_schedule_dp_format.csv"

    # Check if input file exists
    if not input_csv.exists():
        print(f"ERROR: Input file not found: {input_csv}")
        print("Please ensure dylan_schedule.csv is in the dylan_data/ folder")
        return

    # Convert
    dp_df = convert_dylan_schedule_to_dp_format(
        input_csv,
        output_csv,
        add_service_station=True,
        verbose=True
    )

    print(f"\n{'='*70}")
    print("NEXT STEPS")
    print(f"{'='*70}")
    print("\n1. Review the converted file:")
    print(f"   cat {output_csv}")
    print("\n2. Run your DP algorithm on Dylan's data:")
    print(f"   python tests/test_dylan_schedule.py")
    print("\n3. Compare results with CPLEX (if you have it):")
    print(f"   jupyter notebook dylan_data/solution_analysis.ipynb")
    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
