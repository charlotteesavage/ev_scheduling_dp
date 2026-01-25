"""
Simple example of running multi-day simulations with SOC carryover.
"""

import pandas as pd
import statistics
import argparse
import os
import secrets
from ctypes import c_double
from testing_check import (
    Activity, Label, c_int, c_char, POINTER,
    compile_code, CDLL,
    initialise_and_personalise_activities,
    initialize_utility,
    # run_dp,
    # extract_schedule
)
import time

# Constants
TIME_INTERVAL = 5  # minutes
HORIZON = 288  # number of 5-minute intervals in 24 hours
AVG_SPEED_PER_HOUR = (
    20.4 * 1.60934
)  # km/h taken from https://www.gov.uk/government/statistical-data-sets/average-speed-delay-and-reliability-of-travel-times-cgn#average-speed-delay-and-reliability-of-travel-times-on-local-a-roads-cgn05
# can also check https://www.gov.uk/government/publications/webtag-tag-unit-m1-2-data-sources-and-surveys
SPEED = AVG_SPEED_PER_HOUR * 16.667  # 1km/h = 16.667 m/min, converts it to minutes
TRAVEL_TIME_PENALTY = -0.1  # we will add dusk, home, dawn and work

def seed_c_rng(lib, seed: int) -> int:
    """
    Seed the C-side RNG (drand48 via seed_random/srand48).

    Returns the actual seed value passed through ctypes.
    """
    # Keep it in signed 32-bit range for c_int.
    seed = int(seed) & 0x7FFFFFFF
    lib.set_random_seed(c_int(seed))
    return seed

def multi_run_test(seed: int | None = None):

    # Compile and load library
    lib_path = compile_code()
    lib = CDLL(lib_path)

    # Set up function signatures (add to testing_check.py setup)
    
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
    lib.clear_fixed_initial_soc.argtypes = []
    lib.clear_fixed_initial_soc.restype = None
    lib.set_random_seed.argtypes = [c_int]
    lib.set_random_seed.restype = None

    # Load activities
    activities_file = "testing_latest/person_ending_1263/activities_with_charge_values.csv"
    activities_df = pd.read_csv(activities_file)

    # Initialize parameters
    params = initialize_utility()

    # Starting SOC for day 1
    # current_soc = 0.30  # 30%
    # current_soc = random.uniform(0, 1)
    # Seed once per program run; every call to normal_random() will then produce
    # a new draw as the RNG state advances.
    if seed is None:
        seed = secrets.randbits(31)
    base_seed = seed_c_rng(lib, seed)
    print(f"Base RNG seed: {base_seed}")
    # lib.set_fixed_initial_soc(c_double(current_soc))

    asc_array = (c_double * len(params["asc"]))(*params["asc"])
    early_array = (c_double * len(params["early"]))(*params["early"])
    late_array = (c_double * len(params["late"]))(*params["late"])
    long_array = (c_double * len(params["long"]))(*params["long"])
    short_array = (c_double * len(params["short"]))(*params["short"])


    activities_array, max_num_activities = initialise_and_personalise_activities(
            activities_df
        )
    
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
    lib.set_activities(activities_array, max_num_activities)

    print("runs for warmup")
    for run in range(5):
        lib.main(0, None)
        lib.free_bucket()

    times = []
    t0 = time.perf_counter()
    for run in range(1000):
        lib.main(0, None)
        times.append(float(lib.get_total_time()))
        lib.free_bucket()
    
    wall = time.perf_counter() - t0

    print(f"total wall seconds: {wall:.3f}")
    print(
        "C get_total_time (s): mean={:.6f} stdev={:.6f} min={:.6f} max={:.6f}".format(
            statistics.mean(times),
            statistics.pstdev(times) if len(times) > 1 else 0.0,
            min(times),
            max(times),
        )
    )
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for C RNG (default: random OS seed).",
    )
    args = parser.parse_args()

    env_seed = os.environ.get("SCHED_SEED")
    seed = args.seed if args.seed is not None else (int(env_seed) if env_seed else None)
    multi_run_test(seed=seed)
