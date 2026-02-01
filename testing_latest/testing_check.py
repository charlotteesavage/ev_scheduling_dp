import pandas as pd
from ctypes import Structure, c_int, c_double, POINTER, CDLL, c_char
import subprocess
import os
import time
import datetime as dt
from pathlib import Path
from typing import Iterable, Optional

# Constants
TIME_INTERVAL = 5  # minutes
HORIZON = 288  # number of 5-minute intervals in 24 hours
AVG_SPEED_PER_HOUR = (
    20.4 * 1.60934
)  # km/h taken from https://www.gov.uk/government/statistical-data-sets/average-speed-delay-and-reliability-of-travel-times-cgn#average-speed-delay-and-reliability-of-travel-times-on-local-a-roads-cgn05
# can also check https://www.gov.uk/government/publications/webtag-tag-unit-m1-2-data-sources-and-surveys
SPEED = AVG_SPEED_PER_HOUR * 16.667  # 1km/h = 16.667 m/min, converts it to minutes
TRAVEL_TIME_PENALTY = -0.1  # we will add dusk, home, dawn and work

act_type_to_group = {
    "home": 1,
    "work": 2,
    "business": 3,
    "shop": 4,
    "visit": 4,  # 4 is most common (21,791 vs 10)
    "education": 5,
    "depot": 6,
    "delivery": 7,  # /errands
    "other": 8,
    "medical": 8,  # 8 is most common (5,573 vs 25)
    "pt interaction": 8,
    "escort_business": 8,
    "escort_education": 8,
    "escort_home": 8,
    "escort_other": 8,
    "escort_shop": 8,
    "escort_work": 8,
    "service_station": 9,
}

group_to_type = {
    1: "home",
    2: "Work",
    3: "business",
    4: "shop",
    5: "education",
    6: "depot",
    7: "delivery/errands",
    8: "escort/other",
    9: "service_station",
}


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
    ("x", c_double),
    ("y", c_double),
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
                print(
                    f"Source file {os.path.basename(src_file)} is newer than compiled library"
                )
                break

        # Also check header files
        if not needs_recompile:
            for header_file in os.listdir(inc_dir):
                if header_file.endswith(".h"):
                    header_path = os.path.join(inc_dir, header_file)
                    if os.path.getmtime(header_path) > lib_mtime:
                        needs_recompile = True
                        print(
                            f"Header file {header_file} is newer than compiled library"
                        )
                        break

        if not needs_recompile:
            print(f"Using existing compiled library: {output_lib}")
            return output_lib

    compile_command = (
        [
            "gcc",
            "-m64",
            "-O3",
            "-shared",
            "-fPIC",
            f"-I{inc_dir}",
            "-o",
            output_lib,
        ]
        + sources
        + ["-lm"]
    )

    print(f"Compiling C code: {' '.join(compile_command)}")
    result = subprocess.run(compile_command, capture_output=True, text=True)

    if result.returncode != 0:
        print("Compilation failed!")
        print(f"STDERR: {result.stderr}")
        raise RuntimeError("Failed to compile C code")
    else:
        print(f"Compilation successful! Created {output_lib}")

    return output_lib


# ===== Data prep functions =====


def initialise_and_personalise_activities(df):
    """
    Create and personalize an array of activities from Sheffield data.

    Note: CSV should include dawn (id=0) and dusk (id=max) activities.
    """
    # unique_acts_without_home = df["act_type"].unique() -1
    max_num_activities = len(df)
    activities_array = (Activity * max_num_activities)()

    print(f"\nInitializing {max_num_activities} activities...")

    for _, row in df.iterrows():
        act_id = int(row["id"])

        activities_array[act_id].id = act_id
        activities_array[act_id].x = float(row["x"])
        activities_array[act_id].y = float(row["y"])

        # Align with algorithm's group=0 for home.
        # home=0 allows multiple home visits per day (see mem_contains in utils.c).
        activities_array[act_id].group = int(row["group"]) - 1

        activities_array[act_id].earliest_start = int(row["earliest_start"])
        activities_array[act_id].latest_start = int(row["latest_start"])
        activities_array[act_id].min_duration = int(row["min_duration"])
        activities_array[act_id].max_duration = int(row["max_duration"])
        activities_array[act_id].des_start_time = (
            int(row["des_start_time"]) if not pd.isna(row["des_start_time"]) else 0
        )
        activities_array[act_id].des_duration = (
            int(row["des_duration"]) if not pd.isna(row["des_duration"]) else 0
        )

        # Charging fields (handle NaN values)
        activities_array[act_id].charge_mode = (
            int(row["charge_mode"]) if not pd.isna(row["charge_mode"]) else 0
        )
        activities_array[act_id].is_charging = (
            int(row["is_charging"]) if not pd.isna(row["is_charging"]) else 0
        )
        activities_array[act_id].is_service_station = (
            int(row["is_service_station"])
            if not pd.isna(row["is_service_station"])
            else 0
        )

        # Memory (will be initialized by C code)
        activities_array[act_id].memory = None

    print(f"Initialized {len(df)} activities (array size: {max_num_activities})")
    print(f"  - Dawn: id=0, Dusk: id={max_num_activities - 1}")
    print(
        f"  - Activities with charging: {sum(activities_array[i].is_charging for i in range(max_num_activities))}"
    )

    return activities_array, max_num_activities


def initialize_utility():
    """Initialize utility parameters correctly mapped to actual data groups.

        Actual data group mapping (from activities_list_per_pid.csv):
        Group 1: Home
        Group 2: Work
        Group 3: Business
        Group 4: Shop/Visit (Leisure)
        Group 5: Education
        Group 6: Depot/Medical (Service)
        Group 7: Delivery/Visit
        Group 8: Escort/Other/PT Interaction
        Group 9: Service Station

        act_type_to_group = {
        'home': 1,
        'work': 2,
        'business': 3,
        'shop': 4,
        'visit': 4,              # 4 is most common (21,791 vs 10)
        'education': 5,
        'depot': 6,             #??? what is this?
        'delivery': 7,          #/errands
        'other': 8,
        'medical': 8,            # 8 is most common (5,573 vs 25)
        'pt interaction': 8,
        'escort_business': 8,
        'escort_education': 8,
        'escort_home': 8,
        'escort_other': 8,
        'escort_shop': 8,
        'escort_work': 8,
        'service_station': 9
    }
    """

    # we don't have a delivery parameter coeff in the paper, and the given data doesn't have a leisure activity, so can change these as desired

    # have made "business" coeffs the same as "errands" coeffs
    # have used Leisure params from the paper in 6: depot, cos not sure what else to do there

    asc = [0, 10.6, 16.1, 11.3, 17.4, 12, 16.1, 6.76, 0]
    early = [0, -1.37, -1.73, -2.51, -2.56, -0.031, -1.73, -2.55, 0]
    late = [0, -0.79, -3.42, -0.993, -1.54, -1.58, -3.42, -0.578, -0.61]
    long = [0, -0.201, -0.597, -0.133, -0.0783, -0.209, -0.597, -0.0267, -0.24]
    short = [0, -4.78, -5.63, 0.528, -0.783, -0.00764, -5.63, 0.134, -0.61]
    # short = [0, -4.78, -5.63, -0.528, -0.783, -0.00764, -5.63, -0.134, 0]

    return {"asc": asc, "early": early, "late": late, "long": long, "short": short}


# ===== Main Execution =====


def run_dp(lib, activities_array, max_num_activities, params):
    """Run the DP algorithm."""
    print("\n" + "=" * 60)
    print("Running DP Algorithm...")
    print("=" * 60)

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

    return best_label, total_time


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
        if activities_df is not None and "act_type" in activities_df.columns:
            act_type_row = activities_df[activities_df["id"] == label.act_id]
            if not act_type_row.empty:
                act_type = act_type_row.iloc[0]["act_type"]
            else:
                act_type = "home" if activity.group == 0 else f"group_{activity.group}"
        else:
            act_type = "home" if activity.group == 0 else f"group_{activity.group}"

        unique_key = (label.act_id, label.start_time)
        data = {
            "act_id": label.act_id,
            "act_type": act_type,
            "start_time": label.start_time * TIME_INTERVAL / 60,  # Convert to hours
            # "duration": label.duration * TIME_INTERVAL / 60,  # Convert to hours
            "duration": label.duration,
            "soc_start": label.soc_at_activity_start,
            "soc_end": label.current_soc,
            "is_charging": activity.is_charging,
            "charge_mode": activity.charge_mode,
            "charge_duration": label.charge_duration * TIME_INTERVAL / 60,  # hours
            "charge_cost": label.current_charge_cost,
            "utility": label.utility,
            "x": activity.x,
            "y": activity.y,
        }

        # Keep label with maximum duration for each (act_id, start_time) pair
        if (
            unique_key not in schedule_dict
            or schedule_dict[unique_key]["duration"] < data["duration"]
        ):
            schedule_dict[unique_key] = data

    schedule = list(schedule_dict.values())
    return pd.DataFrame(schedule)


def main():

    print("=" * 60)
    print("EV Charging Scheduling Test")
    print("=" * 60)

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
    lib.get_total_time.restype = c_int
    lib.set_random_seed.argtypes = [c_int]
    lib.set_random_seed.restype = None
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_fixed_initial_soc.restype = None
    lib.clear_fixed_initial_soc.argtypes = []
    lib.clear_fixed_initial_soc.restype = None
    lib.set_utility_error_std_dev.argtypes = [c_double]
    lib.set_utility_error_std_dev.restype = None

    person_folder = "dylan"
    # person_folder = "person_ending_1263"
    # person_folder = "person_ending_1259"
    # csv_to_load = "activities_charging_at_shop.csv"
    # csv_to_load = "activities_charging_at_shop_free.csv"
    # csv_to_load = "activities_with_charge_shop_errands_and_service_station.csv"
    # csv_to_load = "activities_with_service_station_and_work_charge.csv"
    # csv_to_load = "activities_with_charge_at_work_only.csv"
    csv_to_load = (
        "activities_with_charge_shop_errands_and_service_station_shop_free.csv"
    )
    # csv_to_load= "activities_with_charge_at_shop_and_service_station.csv"

    # csv_to_load = "activities_with_charge_at_shop.csv"

    output_root = "testing_latest/optimal_schedules"
    if not os.path.exists(output_root):
        os.makedirs(output_root)

    # Test with person data
    activities_file = f"testing_latest/{person_folder}/{csv_to_load}"
    if not os.path.exists(activities_file):
        raise FileNotFoundError(f"Missing activities file: {activities_file}")

    # Load data
    activities_df = pd.read_csv(activities_file)

    print(f"Loaded {len(activities_df)} activities")
    print(f"Activities with charging: {activities_df['is_charging'].sum()}")
    print(f"Charge modes: {activities_df['charge_mode'].value_counts().to_dict()}")

    # individual = load_test_individual(individual_file)

    # Initialize activities
    activities_array, max_num_activities = initialise_and_personalise_activities(
        activities_df
    )

    # Initialize utility parameters
    params = initialize_utility()

    # Run DP
    # Keep SOC fixed, but use the seed for utility error terms.
    # fixed_soc = 0.80
    utility_error_sigma = 1.0  # set 0.0 to disable error terms

    # lib.set_fixed_initial_soc(c_double(fixed_soc))
    lib.set_utility_error_std_dev(c_double(utility_error_sigma))

    utility_seed = int(time.time())
    lib.set_random_seed(c_int(utility_seed))
    # print(f"Fixed initial SOC: {fixed_soc:.2%}")
    print(f"Utility error sigma: {utility_error_sigma}")
    print(f"Utility error seed: {utility_seed}")
    best_label, total_time = run_dp(lib, activities_array, max_num_activities, params)

    if best_label:
        # Extract and display schedule (pass activities_df for better display names)
        schedule_df = extract_schedule(best_label, activities_array, activities_df)

        print("\n" + "=" * 60)
        print("OPTIMAL SCHEDULE")
        print("=" * 60)
        print(schedule_df.to_string(index=False))

        # Save to CSV
        person_out_dir = os.path.join(output_root, person_folder)
        if not os.path.exists(person_out_dir):
            os.makedirs(person_out_dir)
        output_file = f"{person_out_dir}/{csv_to_load[:-4]}_result_{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
        schedule_df.to_csv(output_file, index=False)
        print(f"\nSchedule saved to: {output_file}")
        print(f"total run time = {total_time}")
        # Summary statistics
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Total activities: {len(schedule_df)}")
        print(f"Charging sessions: {schedule_df['is_charging'].sum()}")
        print(f"Total charging time: {schedule_df['charge_duration'].sum():.2f} hours")
        print(f"Total charging cost: £{schedule_df['charge_cost'].max():.2f}")
        print(f"Final SOC: {schedule_df['soc_end'].iloc[-1]:.2%}")
        print(f"Total utility: {schedule_df['utility'].iloc[-1]:.2f}")

    # Cleanup
    lib.free_bucket()
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
