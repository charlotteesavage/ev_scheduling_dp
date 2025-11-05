from tqdm import tqdm
import subprocess
import numpy as np
from ctypes import Structure, c_int, c_double, POINTER, CDLL
import pandas as pd
from collections import namedtuple
import time
import Post_processing_slice
import warnings
import os

warnings.filterwarnings("ignore", category=pd.errors.SettingWithCopyWarning)

LOCAL = "NewYork"
SCHEDULING_VERSION = 6
TIME_INTERVAL = 5
HORIZON = round(24 * 60 / TIME_INTERVAL) + 1
# SPEED = 19 * 16.667  # 1km/h = 16.667 m/min

AVG_SPEED_Sheffield = (
    20.4 * 1.60934
)  # km/h taken from https://www.gov.uk/government/statistical-data-sets/average-speed-delay-and-reliability-of-travel-times-cgn#average-speed-delay-and-reliability-of-travel-times-on-local-a-roads-cgn05
# can also check https://www.gov.uk/government/publications/webtag-tag-unit-m1-2-data-sources-and-surveys
SPEED = AVG_SPEED_Sheffield * 16.667  # 1km/h = 16.667 m/min
TRAVEL_TIME_PENALTY = 0.1  # we will add dusk, home, dawn and work
H8 = round(8 * 60 / TIME_INTERVAL) + 1
H12 = round(12 * 60 / TIME_INTERVAL) + 1
H13 = round(13 * 60 / TIME_INTERVAL) + 1
H17 = round(17 * 60 / TIME_INTERVAL) + 1
H20 = round(20 * 60 / TIME_INTERVAL) + 1
FLEXIBLE = round(60 / TIME_INTERVAL)
MIDDLE_FLEXIBLE = round(30 / TIME_INTERVAL)
NOT_FLEXIBLE = round(10 / TIME_INTERVAL)
# activity_types = ["Home", "Education", "Errands", "Escort", "Leisure", "Shopping","Work", "ServiceStation"]
group_to_type = {
    0: "Home",
    1: "Education",
    2: "Errands",
    3: "Escort",
    4: "Leisure",
    5: "Shopping",
    6: "Work",
    7: "ServiceStation",
}


# Create output directory if it doesn't exist
current_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(current_dir, "output")
os.makedirs(output_dir, exist_ok=True)


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
    ("can_charge", c_int),  # mske bool in C, but use c_int for compatibility?
    ("slow_charging_available", c_int),
    ("fast_charging_available", c_int),
    ("rapid_charging_available", c_int),
    # ("charging_price_slow", c_double),
    # ("charging_price_fast", c_double),
    # ("charging_price_rapid", c_double), // not sure I need these...
    ("is_service_station", c_int),  # make bool in C ?
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
    ("soc", c_double),
    ("is_charging", c_int),
    ("charge_mode", c_int),
    ("charge_duration", c_int),
    ("charged_soc", c_double),
    ("charge_cost", c_double),
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


def initialize_and_personalize_activities(df, num_activities, individual):
    """Create and personalize an array of activities based on the given dataframe and individual data."""
    activities_array = (Activity * num_activities)()

    activities_array[0].id = 0
    activities_array[0].x = individual["home_x"]
    activities_array[0].y = individual["home_y"]
    activities_array[0].earliest_start = 0
    activities_array[0].latest_start = 0
    activities_array[0].max_duration = HORIZON - 2
    activities_array[0].min_duration = 1
    activities_array[0].group = 0

    activities_array[num_activities - 1].id = num_activities - 1
    activities_array[num_activities - 1].x = individual["home_x"]
    activities_array[num_activities - 1].y = individual["home_y"]
    activities_array[num_activities - 1].earliest_start = 0
    activities_array[num_activities - 1].latest_start = HORIZON - 2
    activities_array[num_activities - 1].max_duration = HORIZON - 2
    activities_array[num_activities - 1].min_duration = 1
    activities_array[num_activities - 1].group = 0

    activities_array[num_activities - 2].id = num_activities - 2
    activities_array[num_activities - 2].x = individual["home_x"]
    activities_array[num_activities - 2].y = individual["home_y"]
    activities_array[num_activities - 2].earliest_start = 0
    activities_array[num_activities - 2].latest_start = HORIZON
    activities_array[num_activities - 2].max_duration = HORIZON - 2
    activities_array[num_activities - 2].min_duration = 1
    activities_array[num_activities - 2].des_duration = 0
    activities_array[num_activities - 2].des_start_time = 0
    activities_array[num_activities - 2].group = 0

    activities_array[num_activities - 3].id = num_activities - 3
    activities_array[num_activities - 3].x = individual["work_x"]
    activities_array[num_activities - 3].y = individual["work_y"]
    activities_array[num_activities - 3].earliest_start = round(
        5 * 60 / TIME_INTERVAL
    )  # 5h
    activities_array[num_activities - 3].latest_start = round(
        23 * 60 / TIME_INTERVAL
    )  # 23h
    activities_array[num_activities - 3].max_duration = round(
        12 * 60 / TIME_INTERVAL
    )  # 12h
    activities_array[num_activities - 3].min_duration = round(10 / TIME_INTERVAL)  # 10m
    activities_array[num_activities - 3].group = 2
    activities_array[num_activities - 3].des_duration = individual["Work_dur"]
    activities_array[num_activities - 3].des_start_time = individual["Work_start"]

    for index, row in df.iterrows():
        activity_index = index + 1
        activities_array[activity_index].id = activity_index
        activities_array[activity_index].x = row["x"]
        activities_array[activity_index].y = row["y"]
        activities_array[activity_index].earliest_start = row["earliest_start"]
        activities_array[activity_index].latest_start = row["latest_start"]
        activities_array[activity_index].max_duration = row["max_duration"]
        activities_array[activity_index].min_duration = row["min_duration"]
        activities_array[activity_index].group = row["group"]

        group = activities_array[activity_index].group
        if group > 0:
            activity_type = group_to_type[group]
            activities_array[activity_index].des_duration = individual[
                f"{activity_type}_dur"
            ]
            activities_array[activity_index].des_start_time = individual[
                f"{activity_type}_start"
            ]

    return activities_array


def initialize_utility():
    UtilityParams = namedtuple("UtilityParams", "asc early late long short")
    params = UtilityParams(
        asc=[0, 18.7, 13.1, 8.74],
        early=[0, 1.35, 0.619, 0.0996],
        late=[0, 1.63, 0.338, 0.239],
        long=[0, 1.14, 1.22, 0.08],
        short=[0, 1.75, 0.932, 0.101],
    )
    return params


def compile_code():  # used AI to help make this, need to double check it
    """Compile the scheduling C code as a shared library for Python ctypes."""
    # Get the current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))

    compile_command = [
        "gcc",
        "-m64",
        "-O3",
        "-shared",
        "-fPIC",
        "-o",
        f"{current_dir}/scheduling_CS.so",
        f"{current_dir}/scheduling_CS.c",
        f"{current_dir}/scheduling_main.c",
        "-lm",
    ]

    print(f"Compiling C code: {' '.join(compile_command)}")
    result = subprocess.run(compile_command, capture_output=True, text=True)

    if result.returncode != 0:
        print("Compilation failed!")
        print(f"STDERR: {result.stderr}")
        raise RuntimeError("Failed to compile C code")
    else:
        print("Compilation successful! Created scheduling_CS.so")

    return f"{current_dir}/scheduling_CS.so"


def extract_schedule_data(label_pointer, activity_df, individual, num_activities):
    path_to_root = []
    while label_pointer:
        path_to_root.append(label_pointer)
        label_pointer = label_pointer.contents.previous

    schedule_data_dict = {}
    for label_pointer in reversed(path_to_root):
        label = label_pointer.contents

        acity = label.acity
        if (acity > 0) and (acity < num_activities - 3):
            activity_row_from_csv = activity_df.iloc[acity - 1]
            facility_id = activity_row_from_csv["facility"]
            x = activity_row_from_csv["x"]
            y = activity_row_from_csv["y"]
        elif acity == num_activities - 3:
            facility_id = individual["work_id"]
            x = individual["work_x"]
            y = individual["work_y"]
        else:
            facility_id = individual["homeid"]
            x = individual["home_x"]
            y = individual["home_y"]

        data = {
            "acity": label.acity,
            "facility": facility_id,
            "group": group_to_type[label.act.contents.group],
            "start": label.start_time,
            "duration": label.duration,
            "time": label.time,
            "x": x,
            "y": y,
        }

        unique_key = (data["acity"], data["start"])
        if (
            unique_key not in schedule_data_dict
            or schedule_data_dict[unique_key]["duration"] < data["duration"]
        ):
            schedule_data_dict[unique_key] = data

    schedule_data = list(schedule_data_dict.values())
    return schedule_data


def filter_closest(
    all_activities, individual, num_act_to_select, constraints, activities_locations
):
    home = np.array((individual["home_x"], individual["home_y"]))
    work = np.array((individual["work_x"], individual["work_y"]))
    mask = np.ones(len(all_activities), dtype=bool)
    if constraints[0]:
        mask &= all_activities["group"] != 3
    if constraints[1]:
        mask &= all_activities["group"] != 3
    if constraints[2]:
        mask &= all_activities["group"] != 1
    if constraints[3]:
        mask &= all_activities["group"] != 2

    filtered_activities = all_activities[mask]
    filtered_locations = activities_locations[mask]

    type_fractions = filtered_activities["type"].value_counts(normalize=True)
    distances_home = np.linalg.norm(filtered_locations - home, axis=1)
    distances_work = np.linalg.norm(filtered_locations - work, axis=1)
    min_distances = np.minimum(distances_home, distances_work)

    filtered_activities["distance"] = min_distances

    selected_activities = pd.DataFrame()
    for facility_type, fraction in type_fractions.items():
        type_activities = filtered_activities[
            filtered_activities["type"] == facility_type
        ]
        n_closest = int(num_act_to_select * fraction)
        selected_activities = pd.concat(
            [
                selected_activities,
                type_activities.sort_values("distance").head(n_closest),
            ]
        )

    return selected_activities.reset_index(drop=True)


def compile_and_initialize(start, end):
    so_path = compile_code()
    lib = CDLL(so_path)
    activity_csv = pd.read_csv(
        f"/home/ccortes/Modeling-Individual-Activity-Schedules-and-Behavior-Changes-in-COVID-19/Data/2_PreProcessed/activities_{LOCAL}.csv"
    )
    population_csv = pd.read_csv(
        f"/home/ccortes/Modeling-Individual-Activity-Schedules-and-Behavior-Changes-in-COVID-19/Data/2_PreProcessed/population_{LOCAL}.csv"
    ).iloc[start:end]

    lib.get_final_schedule.restype = POINTER(Label)
    lib.get_total_time.restype = c_double
    lib.get_count.restype = c_int

    util_params = initialize_utility()
    asc_array = (c_double * len(util_params.asc))(*util_params.asc)
    early_array = (c_double * len(util_params.early))(*util_params.early)
    late_array = (c_double * len(util_params.late))(*util_params.late)
    long_array = (c_double * len(util_params.long))(*util_params.long)
    short_array = (c_double * len(util_params.short))(*util_params.short)

    # Call set_general_parameters with correct signature
    # void set_general_parameters(int pyhorizon, double pyspeed, double pytravel_time_penalty,
    #                            int pytime_interval, double *asc, double *early, double *late,
    #                            double *longp, double *shortp, int pyflexible, int pymid_flex,
    #                            int pynot_flex)
    lib.set_general_parameters(
        c_int(HORIZON),
        c_double(SPEED),
        c_double(TRAVEL_TIME_PENALTY),
        c_int(TIME_INTERVAL),
        asc_array,
        early_array,
        late_array,
        long_array,
        short_array,
        c_int(FLEXIBLE),
        c_int(MIDDLE_FLEXIBLE),
        c_int(NOT_FLEXIBLE),
    )

    return activity_csv, population_csv, lib


def call_to_optimizer(
    activity_csv, population_csv, scenario, constraints, num_act_to_select=15
):
    print(f"Running scenario: {scenario}")
    # Note: set_scenario_constraints doesn't exist in scheduling_CS.c
    # constraints_array = (c_int * len(constraints))(*constraints)
    # lib.set_scenario_constraints(constraints_array)
    activities_locations = activity_csv[["x", "y"]].to_numpy()

    total_deviations_start = []
    total_deviations_dur = []
    final_utilities = []
    schedules = []
    ids = []

    for i, individual in tqdm(population_csv.iterrows(), total=population_csv.shape[0]):
        closest_facilities = filter_closest(
            activity_csv,
            individual,
            num_act_to_select,
            constraints,
            activities_locations,
        )
        num_activities = len(closest_facilities) + 4
        activities_array = initialize_and_personalize_activities(
            closest_facilities, num_activities, individual
        )
        lib.set_activities(activities_array, num_activities)
        lib.main()

        schedule_pointer = lib.get_final_schedule()
        schedule_data = extract_schedule_data(
            schedule_pointer, activity_csv, individual, num_activities
        )

        schedules.append(schedule_data)
        ids.append(individual["id"])

        if schedule_pointer and schedule_pointer.contents:
            final_utilities.append(schedule_pointer.contents.utility)
            total_deviations_start.append(schedule_pointer.contents.deviation_start)
            total_deviations_dur.append(schedule_pointer.contents.deviation_dur)
        else:
            final_utilities.append(0)
            total_deviations_start.append(0)
            total_deviations_dur.append(0)

        lib.free_bucket()

        if i % 1000 == 0 or i == population_csv.shape[0] - 1:
            print(f"\n i={i}, saving intermediate results...")
            mode = "a" if i != 0 else "w"
            results = pd.DataFrame(
                {
                    "id": ids,
                    "utility": final_utilities,
                    "total_deviation_start": total_deviations_start,
                    "total_deviation_dur": total_deviations_dur,
                    "daily_schedule": schedules,
                }
            )
            results.to_json(
                f"/home/ccortes/Modeling-Individual-Activity-Schedules-and-Behavior-Changes-in-COVID-19/Data/3_Generated/{scenario}{LOCAL}.json",
                orient="records",
                lines=True,
                indent=4,
                mode=mode,
            )

            ids.clear()
            final_utilities.clear()
            schedules.clear()
            total_deviations_start.clear()
            total_deviations_dur.clear()


if __name__ == "__main__":  # haven't touched this yet 03/11/2025
    scenari = ["Normal_life"]
    n = 15
    start_index = 150000
    end_index = 320000  # Adjust this to the range you want to process
    activity_csv, population_csv, lib = compile_and_initialize(start_index, end_index)
    constraints = {
        "Normal_life": [0, 0, 0, 0, 0, 0, 0],
        "Outings_limitation": [1, 0, 0, 0, 1, 0, 0],
        "Only_economy": [1, 1, 1, 0, 0, 0, 0],
        "Early_curfew": [0, 0, 0, 0, 0, 1, 0],
        "Essential_needs": [1, 0, 1, 0, 0, 0, 0],
        "Finding_balance": [0, 0, 0, 0, 0, 0, 1],
        "Impact_of_leisure": [1, 0, 0, 0, 0, 0, 0],
    }

    elapsed_times = []
    for scenario_name in scenari:
        start_time = time.time()
        call_to_optimizer(
            activity_csv,
            population_csv,
            scenario_name,
            constraints[scenario_name],
            num_act_to_select=n,
        )
        end_time = time.time()
        elapsed_time = end_time - start_time
        elapsed_times.append(round(elapsed_time, 2))
        print(
            f"For {end_index - start_index} individuals of {LOCAL} and {n} closest activities around their home/work, the execution time of scenario {scenario_name} is {elapsed_time:.1f} seconds\n"
        )

    print(elapsed_times)
    print("Creating the Post-processed files...")
    Post_processing_slice.create_postprocess_files(
        LOCAL, TIME_INTERVAL, scenari, end_index
    )
