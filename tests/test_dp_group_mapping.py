"""
Test script for running DP with single-person Sheffield data.

This adapts main_slice_cs.py to work with the Sheffield case study data format.
"""

import pandas as pd
import numpy as np
from ctypes import *
import subprocess
import os
import time
from pathlib import Path

# Constants
TIME_INTERVAL = 5  # minutes
HORIZON = 288  # number of 5-minute intervals in 24 hours


# ===== C Structure Definitions =====

class Group_mem(Structure):
    pass


Group_mem._fields_ = [
    ("g", c_int),
    ("previous", POINTER(Group_mem)),
    ("next", POINTER(Group_mem)),
]


class Activity(Structure):
    pass


Activity._fields_ = [
    ("id", c_int),
    ("earliest_start", c_int),
    ("latest_start", c_int),
    ("min_duration", c_int),
    ("max_duration", c_int),
    ("x", c_int),
    ("y", c_int),
    ("group", c_int),
    ("memory", POINTER(Group_mem)),
    ("des_duration", c_int),
    ("des_start_time", c_int),
    ("charge_mode", c_int),
    ("is_charging", c_int),
    ("is_service_station", c_int),
]


class Label(Structure):
    pass


Label._fields_ = [
    ("act_id", c_int),
    ("time", c_int),
    ("start_time", c_int),
    ("duration", c_int),
    ("deviation_start", c_int),
    ("deviation_dur", c_int),
    ("soc_at_activity_start", c_double),
    ("current_soc", c_double),
    ("delta_soc", c_double),
    ("charge_duration", c_int),
    ("charge_cost_at_activity_start", c_double),
    ("current_charge_cost", c_double),
    ("utility", c_double),
    ("mem", POINTER(Group_mem)),
    ("previous", POINTER(Label)),
    ("act", POINTER(Activity)),
]


class L_list(Structure):
    pass


L_list._fields_ = [
    ("element", POINTER(Label)),
    ("previous", POINTER(L_list)),
    ("next", POINTER(L_list)),
]


# ===== C Compilation =====

def compile_code():
    """Compile the scheduling C code as a shared library for Python ctypes."""
    # Get the current directory and parent directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)

    # Define paths (src and include are in parent directory)
    src_dir = os.path.join(parent_dir, "src")
    inc_dir = os.path.join(parent_dir, "include")
    output_lib = os.path.join(current_dir, "scheduling.so")

    # Source files to compile
    sources = [
        os.path.join(src_dir, "scheduling.c"),
        os.path.join(src_dir, "utils.c"),
        os.path.join(src_dir, "main.c"),
    ]

    # Check if recompilation is needed
    needs_recompile = False
    if not os.path.exists(output_lib):
        needs_recompile = True
        print("No existing compiled library found")
    else:
        # Check if any source file is newer than the compiled library
        lib_mtime = os.path.getmtime(output_lib)
        for src_file in sources:
            if os.path.getmtime(src_file) > lib_mtime:
                needs_recompile = True
                print(f"Source file {os.path.basename(src_file)} is newer than compiled library")
                break

        # Also check header files
        if not needs_recompile:
            for header_file in os.listdir(inc_dir):
                if header_file.endswith('.h'):
                    header_path = os.path.join(inc_dir, header_file)
                    if os.path.getmtime(header_path) > lib_mtime:
                        needs_recompile = True
                        print(f"Header file {header_file} is newer than compiled library")
                        break

        if not needs_recompile:
            print(f"Using existing compiled library: {output_lib}")
            return output_lib

    compile_command = [
        "gcc",
        "-m64",
        "-O3",
        "-shared",
        "-fPIC",
        f"-I{inc_dir}",
        "-o",
        output_lib,
    ] + sources + ["-lm"]

    print(f"Compiling C code: {' '.join(compile_command)}")
    result = subprocess.run(compile_command, capture_output=True, text=True)

    if result.returncode != 0:
        print("Compilation failed!")
        print(f"STDERR: {result.stderr}")
        raise RuntimeError("Failed to compile C code")
    else:
        print(f"Compilation successful! Created {output_lib}")

    return output_lib


# ===== Utility Parameters =====

def initialize_utility():
    """Initialize utility parameters correctly mapped to actual data groups.

    Original parameter mapping from reference implementation:
    [0:Home, 1:Education, 2:Errands, 3:Escort, 4:Leisure, 5:Shopping, 6:Work, 7:ServiceStation]

    Actual data group mapping (from activities_list_per_pid.csv):
    Group 1: Home
    Group 2: Work
    Group 3: Business
    Group 4: Shop/Visit (Leisure)
    Group 5: Education
    Group 6: Depot/Medical (Service)
    Group 7: Delivery/Visit
    Group 8: Escort/Other/PT Interaction

    act_type_to_group = {
    'home': 1,
    'work': 2,
    'business': 3,
    'shop': 4,
    'visit': 4,              # 4 is most common (21,791 vs 10)
    'education': 5,
    'medical': 8,            # 8 is most common (5,573 vs 25)
    'depot': 6,
    'delivery': 7,
    'other': 8,
    'pt interaction': 8,
    'escort_business': 8,
    'escort_education': 8,
    'escort_home': 8,
    'escort_other': 8,
    'escort_shop': 8,
    'escort_work': 8
}
    """
    # Original parameters
    asc   = [0, 17.4, 16.1, 6.76, 12, 11.3, 10.6, 0]
    early = [0, -2.56, -1.73, -2.55, -0.031, -2.51, -1.37, 0]
    late  = [0, -1.54, -3.42, -0.578, -1.58, -0.993, -0.79, 0]
    long  = [0, -0.0783, -0.597, -0.0267, -0.209, -0.133, -0.201, 0]
    short = [0, -0.783, -5.63, 0.134, -0.00764, 0.528, -4.78, 0]


    return {
        'asc': asc,
        'early': early,
        'late': late,
        'long': long,
        'short': short
    }


# ===== Data Loading Functions =====

def load_test_activities(filepath):
    """Load test activities CSV with charging information."""
    df = pd.read_csv(filepath)
    # Strip whitespace from column names
    df.columns = df.columns.str.strip()

    # Replace empty strings and whitespace-only strings with NaN
    df = df.replace(r'^\s*$', np.nan, regex=True)

    print(f"Loaded {len(df)} activities from {filepath}")
    print(f"Activities with charging: {df['is_charging'].sum()}")
    print(f"Charge modes: {df['charge_mode'].value_counts().to_dict()}")
    return df


# def load_test_individual(filepath):
#     """Load test individual CSV."""
#     df = pd.read_csv(filepath)
#     individual = df.iloc[0].to_dict()
#     print(f"\nLoaded individual: {individual['pid']}")
#     return individual


# ===== Activity Initialization =====

def initialize_and_personalize_activities(df):
    """
    Create and personalize an array of activities from Sheffield data.

    Note: CSV should include dawn (id=0) and dusk (id=max) activities.
    """
    max_num_activities = len(df)
    activities_array = (Activity * max_num_activities)()

    print(f"\nInitializing {max_num_activities} activities...")

    for idx, row in df.iterrows():
        # Handle both 'activity_idx' and 'id' column names
        if 'activity_idx' in df.columns:
            act_id = int(row['activity_idx'])
        else:
            act_id = int(row['id'])

        activities_array[act_id].id = act_id
        activities_array[act_id].x = int(row['x'])
        activities_array[act_id].y = int(row['y'])

        # CRITICAL: Subtract 1 from all group numbers to align with algorithm's group=0 for home
        # Production data has: Group 1=Home, 2=Work, 3=Business, etc.
        # Algorithm expects: Group 0=Home, 1=Work, 2=Business, etc.
        # This makes home=0, which allows multiple visits per day (see mem_contains in utils.c)
        # NOTE: Test data should also follow production format (home=1) for consistency
        activities_array[act_id].group = int(row['group']) - 1

        activities_array[act_id].earliest_start = int(row['earliest_start'])
        activities_array[act_id].latest_start = int(row['latest_start'])
        activities_array[act_id].min_duration = int(row['min_duration'])
        activities_array[act_id].max_duration = int(row['max_duration'])
        activities_array[act_id].des_start_time = int(row['des_start_time']) if not pd.isna(row['des_start_time']) else 0
        activities_array[act_id].des_duration = int(row['des_duration']) if not pd.isna(row['des_duration']) else 0

        # Charging fields (handle NaN values)
        activities_array[act_id].charge_mode = int(row['charge_mode']) if not pd.isna(row['charge_mode']) else 0
        activities_array[act_id].is_charging = int(row['is_charging']) if not pd.isna(row['is_charging']) else 0
        activities_array[act_id].is_service_station = int(row['is_service_station']) if not pd.isna(row['is_service_station']) else 0

        # Memory (will be initialized by C code)
        activities_array[act_id].memory = None

    print(f"Initialized {len(df)} activities (array size: {max_num_activities})")
    print(f"  - Dawn: id=0, Dusk: id={max_num_activities-1}")
    print(f"  - Activities with charging: {sum(activities_array[i].is_charging for i in range(max_num_activities))}")

    return activities_array, max_num_activities


# ===== Main Execution =====

def run_dp(lib, activities_array, max_num_activities, params):
    """Run the DP algorithm."""
    print("\n" + "="*60)
    print("Running DP Algorithm...")
    print("="*60)

    # Convert parameters to C arrays
    asc_array = (c_double * len(params['asc']))(*params['asc'])
    early_array = (c_double * len(params['early']))(*params['early'])
    late_array = (c_double * len(params['late']))(*params['late'])
    long_array = (c_double * len(params['long']))(*params['long'])
    short_array = (c_double * len(params['short']))(*params['short'])

    # Set general parameters
    speed = 50000.0 / 60.0  # m/min (50 km/h)
    travel_time_penalty = -0.5

    lib.set_general_parameters(
        HORIZON,
        speed,
        travel_time_penalty,
        TIME_INTERVAL,
        asc_array,
        early_array,
        late_array,
        long_array,
        short_array
    )

    # Set activities
    lib.set_activities(activities_array, max_num_activities)

    print(f"Number of activities: {max_num_activities}")
    print(f"Horizon: {HORIZON} intervals ({HORIZON * TIME_INTERVAL / 60:.1f} hours)")
    print("Starting DP...")

    # Run main (creates bucket, runs DP, handles DSSR)
    # Pass dummy argc=0, argv=NULL since we're not using command line args
    start_time = time.time()
    result = lib.main(0, None)
    total_time = time.time() - start_time

    print(f"C main() returned: {result}")

    print(f"\nDP completed in {total_time:.2f} seconds")

    # Get final schedule
    best_label = lib.get_final_schedule()

    if not best_label:
        print("ERROR: No feasible solution found!")
        return None

    print(f"Final utility: {best_label.contents.utility:.2f}")

    return best_label


def extract_schedule(best_label, activities_array, activities_df=None):
    """
    Extract the schedule from the best label.

    Uses the same approach as main_slice_cs.py: group by (act_id, start_time)
    and keep only the label with maximum duration (= final state for that activity visit).
    """
    # Collect all labels in the path
    path_to_root = []
    current = best_label
    while current:
        path_to_root.append(current)
        current = current.contents.previous

    # Process labels and group by unique (act_id, start_time)
    schedule_dict = {}
    for label_pointer in reversed(path_to_root):
        label = label_pointer.contents
        activity = activities_array[label.act_id]

        # Get activity type name from original dataframe if available
        if activities_df is not None and 'act_type' in activities_df.columns:
            act_type_row = activities_df[activities_df['id'] == label.act_id]
            if not act_type_row.empty:
                act_type = act_type_row.iloc[0]['act_type']
            else:
                act_type = 'home' if activity.group == 0 else f'group_{activity.group}'
        else:
            act_type = 'home' if activity.group == 0 else f'group_{activity.group}'

        unique_key = (label.act_id, label.start_time)
        data = {
            'act_id': label.act_id,
            'act_type': act_type,
            'start_time': label.start_time * TIME_INTERVAL / 60,  # Convert to hours
            'duration': label.duration * TIME_INTERVAL / 60,  # Convert to hours
            'soc_start': label.soc_at_activity_start,
            'soc_end': label.current_soc,
            'is_charging': activity.is_charging,
            'charge_mode': activity.charge_mode,
            'charge_duration': label.charge_duration * TIME_INTERVAL / 60,  # hours
            'charge_cost': label.current_charge_cost,
            'utility': label.utility,
            'x': activity.x,
            'y': activity.y
        }

        # Keep label with maximum duration for each (act_id, start_time) pair
        if unique_key not in schedule_dict or schedule_dict[unique_key]['duration'] < data['duration']:
            schedule_dict[unique_key] = data

    schedule = list(schedule_dict.values())
    return pd.DataFrame(schedule)


def main():
    """Main execution function."""
    print("="*60)
    print("Sheffield EV Charging Scheduling Test")
    print("="*60)

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
    lib.main.argtypes = [c_int, POINTER(POINTER(c_char))]  # int argc, char **argv
    lib.main.restype = c_int
    lib.get_final_schedule.restype = POINTER(Label)
    lib.free_bucket.restype = None

    csv_to_load = "test_activities_person_654_work_less_duration.csv"
    # Paths (data is in parent directory)
    script_dir = Path(__file__).parent
    parent_dir = script_dir.parent
    data_dir = parent_dir / "tests/"
    # Test with person_654 data
    activities_file = data_dir / csv_to_load
    # activities_file = data_dir / "person_259/person_259_acts_with_dawn_dusk.csv"
    # activities_file = data_dir / "person_259/person_259_minimal_test.csv"
    # activities_file = data_dir / "test_activities_single_person.csv"
    # individual_file = data_dir / "test_individual.csv"

    # Check files exist
    if not activities_file.exists():
        print(f"ERROR: {activities_file} not found!")
        print("Please run: python3 prepare_single_person_data.py")
        return

    # Load data
    activities_df = load_test_activities(activities_file)
    # individual = load_test_individual(individual_file)

    # Initialize activities
    activities_array, max_num_activities = initialize_and_personalize_activities(
        activities_df
    )

    # Initialize utility parameters
    params = initialize_utility()

    # Run DP
    best_label = run_dp(lib, activities_array, max_num_activities, params)

    if best_label:
        # Extract and display schedule (pass activities_df for better display names)
        schedule_df = extract_schedule(best_label, activities_array, activities_df)

        print("\n" + "="*60)
        print("OPTIMAL SCHEDULE")
        print("="*60)
        print(schedule_df.to_string(index=False))

        # Save to CSV
        output_file = data_dir / f"{csv_to_load[:-4]}_optimal_schedule.csv"
        schedule_df.to_csv(output_file, index=False)
        print(f"\nSchedule saved to: {output_file}")

        # Summary statistics
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Total activities: {len(schedule_df)}")
        print(f"Charging sessions: {schedule_df['is_charging'].sum()}")
        print(f"Total charging time: {schedule_df['charge_duration'].sum():.2f} hours")
        print(f"Total charging cost: £{schedule_df['charge_cost'].max():.2f}")
        print(f"Final SOC: {schedule_df['soc_end'].iloc[-1]:.2%}")
        print(f"Total utility: {schedule_df['utility'].iloc[-1]:.2f}")

    # Cleanup
    lib.free_bucket()
    print("\n" + "="*60)
    print("Test complete!")
    print("="*60)


if __name__ == "__main__":
    main()
