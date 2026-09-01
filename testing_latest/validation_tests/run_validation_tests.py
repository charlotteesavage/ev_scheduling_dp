import itertools
import math
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from testing_check import (
    compile_code, initialise_and_personalise_activities,
    initialize_utility, run_dp, extract_schedule,
    CDLL, Activity, Label, POINTER, c_char, c_double, c_int,
    SPEED, TIME_INTERVAL, HORIZON, TRAVEL_TIME_PENALTY
)

# Mirrors of values that are hard-coded in src/scheduling.c and cannot be set
# from Python. Used by the brute-force optimality check below.
THETA_SOC = -80.0
SOC_THRESHOLD = 0.3
ENERGY_CONSUMPTION_RATE = 0.2   # kWh per km
BATTERY_CAPACITY = 60.0         # kWh


def check_battery(schedule):
    for _, row in schedule.iterrows():
        if row['soc_start'] < 0 or row['soc_end'] < 0:
            return False
        if row['soc_start'] > 1.0 or row['soc_end'] > 1.0:
            return False
    return True


def check_travel_consumption(schedule, tolerance=0.03):
    # expected arrival SOC at next activity = previous soc_end - (distance_km * 0.2 / 60)
    # (0.2 kWh/km, 60 kWh battery)
    if len(schedule) < 2:
        return True

    ENERGY_CONSUMPTION_KWH_PER_KM = 0.2
    BATTERY_KWH = 60.0

    for i in range(1, len(schedule)):
        prev_row = schedule.iloc[i - 1]
        row = schedule.iloc[i]

        dx = float(row["x"]) - float(prev_row["x"])
        dy = float(row["y"]) - float(prev_row["y"])
        dist_m = (dx * dx + dy * dy) ** 0.5
        dist_km = dist_m / 1000.0

        expected_drop = (dist_km * ENERGY_CONSUMPTION_KWH_PER_KM) / BATTERY_KWH
        expected_soc = float(prev_row["soc_end"]) - expected_drop
        actual_soc = float(row["soc_start"])

        if abs(actual_soc - expected_soc) > tolerance:
            return False

    return True


def _interval(start_time_hours):
    """extract_schedule() reports start_time in hours; the DP works in intervals."""
    return int(round(float(start_time_hours) * 60 / TIME_INTERVAL))


def _travel_intervals(a1, a2):
    """Mirror of travel_time() in src/scheduling.c: distance in metres divided by
    speed in metres per minute, then rounded UP to a whole number of intervals."""
    dist = math.hypot(float(a2["x"]) - float(a1["x"]), float(a2["y"]) - float(a1["y"]))
    return int(math.ceil((dist / SPEED) / TIME_INTERVAL))


def _travel_soc(a1, a2):
    """Mirror of energy_consumed_soc() in src/scheduling.c: the fraction of the
    battery used driving between two activities."""
    dist_km = math.hypot(
        float(a2["x"]) - float(a1["x"]), float(a2["y"]) - float(a1["y"])) / 1000.0
    return (dist_km * ENERGY_CONSUMPTION_RATE) / BATTERY_CAPACITY


def check_contiguity(schedule, activities):
    """
    No waiting around: an activity must begin the moment you arrive from the
    previous one, so a legal schedule has no gaps and no overlaps.

        start[i] == start[i-1] + duration[i-1] + travel_time(i-1, i)

    This is forced by is_feasible() in src/scheduling.c, which requires the
    ARRIVAL time to land inside [earliest_start, latest_start]. There is no way
    to sit idle and start later -- the only way to delay is to stay longer at the
    activity you are already at. Nothing else in this suite checks it, so a gap
    or an overlap would otherwise go unnoticed.

    All three quantities are in 5-minute intervals.
    """
    acts = {int(row["id"]): row for _, row in activities.iterrows()}
    for i in range(1, len(schedule)):
        prev_row = schedule.iloc[i - 1]
        row = schedule.iloc[i]
        a1 = acts.get(int(prev_row["act_id"]))
        a2 = acts.get(int(row["act_id"]))
        if a1 is None or a2 is None:
            continue
        expected = (
            _interval(prev_row["start_time"])
            + int(prev_row["duration"])
            + _travel_intervals(a1, a2)
        )
        if _interval(row["start_time"]) != expected:
            return False
    return True


def check_times(schedule, activities):
    for _, row in schedule.iterrows():
        input_act = activities[activities['id'] == row['act_id']]
        if len(input_act) == 0:
            continue
        input_act = input_act.iloc[0]
        start_interval = int(float(row['start_time']) * 60 / 5)  # 5-minute intervals
        if start_interval < input_act['earliest_start']:
            return False
        if start_interval > input_act['latest_start']:
            return False
    return True


def check_charging(schedule, activities):
    for _, row in schedule.iterrows():
        if row['charge_duration'] > 0:
            input_act = activities[activities['id'] == row['act_id']]
            if len(input_act) == 0:
                continue
            if input_act.iloc[0]['is_charging'] != 1:
                return False
    return True


def check_durations(schedule, activities):
    for i, row in schedule.iterrows():
        if i == len(schedule) - 1:
            continue
        input_act = activities[activities['id'] == row['act_id']]
        if len(input_act) == 0:
            continue
        input_act = input_act.iloc[0]
        if row['duration'] < input_act['min_duration']:
            return False
        if row['duration'] > input_act['max_duration']:
            return False
    return True


def check_des_duration(schedule, activities):
    """
    Behavioural check: a scheduled duration should land nearer its DESIRED value
    than its floor. check_durations() above only asserts the duration is legal
    (inside [min, max]); this asserts it is in the right PLACE inside that range.

    Comparative rather than absolute on purpose -- no tolerance to tune, and it
    stays valid when des_duration changes. It is deliberately lenient about mild
    deviation; it is hunting the "pinned to min_duration" failure mode.

    Only meaningful on a fixture with enough schedule slack for the activity to
    actually reach its desired duration. Without slack this measures feasibility,
    not preference.
    """
    checked = 0
    for i, row in schedule.iterrows():
        if i == len(schedule) - 1:
            continue  # the final activity's duration is forced to fill the horizon
        input_act = activities[activities['id'] == row['act_id']]
        if len(input_act) == 0:
            continue
        input_act = input_act.iloc[0]
        # No duration term applies to home (group 1 in the CSV) or service
        # stations -- see update_utility() in src/scheduling.c.
        if input_act['group'] == 1 or input_act['is_service_station'] == 1:
            continue
        checked += 1
        to_desired = abs(row['duration'] - input_act['des_duration'])
        to_floor = abs(row['duration'] - input_act['min_duration'])
        if to_desired >= to_floor:
            return False
    # A fixture that schedules nothing must not pass silently -- that is how
    # duration_bounds.csv sat green while testing nothing at all.
    return checked > 0


def check_horizon(schedule):
    # start_time is in hours, duration is in 5-minute intervals
    for _, row in schedule.iterrows():
        start_hr = float(row['start_time'])
        dur_hr = float(row['duration']) * 5 / 60.0
        if start_hr + dur_hr > 24.0 + 1e-9:
            return False
    return True


def check_service_station(schedule, activities):
    # If we visit a service station activity, we expect charge_duration > 0
    schedule_with_ss = schedule.merge(
        activities[['id', 'act_type', 'is_service_station']],
        left_on='act_id',
        right_on='id',
        how='left'
    )
    ss_rows = schedule_with_ss[schedule_with_ss['is_service_station'] == 1]
    for _, row in ss_rows.iterrows():
        if row['charge_duration'] <= 0:
            return False
    return True


def check_no_repeats(schedule, activities):
    schedule_with_groups = schedule.merge(
        activities[['id', 'group']],
        left_on='act_id',
        right_on='id'
    )
    non_home = schedule_with_groups[schedule_with_groups['group'] != 1]
    group_counts = non_home['group'].value_counts()
    for count in group_counts:
        if count > 1:
            return False
    return True


def _visited_ids(lib, activities, start_battery, sigma, seed, params):
    """Run the DP quietly and return the set of activity ids on the best path."""
    activities_array, num_activities = initialise_and_personalise_activities(activities)
    lib.set_fixed_initial_soc(c_double(start_battery))
    lib.set_utility_error_std_dev(c_double(sigma))
    lib.set_random_seed(c_int(seed))

    result = run_dp(lib, activities_array, num_activities, params)
    if result is None:
        lib.free_bucket()
        return None
    best_label, _ = result
    ids, node = set(), best_label
    while node:
        ids.add(node.contents.act_id)
        node = node.contents.previous
    lib.free_bucket()
    return ids


def run_twin_error_sharing_test(lib, n_seeds=120):
    """
    Duplicating an activity into charge/no-charge twins must not change how often
    that activity is chosen. Twins share a base_id, so they share their
    participation/start/duration/travel error draws; without that sharing the pair
    gets two independent draws and wins more often than the single row purely
    because E[max(e1, e2)] > E[e].

    Both fixtures here have charging switched off on every row, so the twins are
    utility-identical and any difference in visit rate is the artefact alone.
    """
    base = Path(__file__).parent
    single_path = base / "twin_sharing_single.csv"
    twin_path = base / "twin_sharing_twins.csv"
    if not single_path.exists() or not twin_path.exists():
        return "SKIP"

    single = pd.read_csv(single_path)
    twins = pd.read_csv(twin_path)
    params = initialize_utility()
    # Make the shop marginal so the visit decision is actually noise-sensitive;
    # the calibrated short/long terms would otherwise make it unconditional.
    # At this ASC the single-row fixture is visited on roughly half of the seeds,
    # which is where the duplication artefact shows up most strongly. If the shop
    # is always or never visited the comparison below passes vacuously.
    params["asc"][3] = 2.0
    params["short"][3] = 0.0
    params["long"][3] = 0.0
    params["early"][3] = 0.0
    params["late"][3] = 0.0

    n_single = n_twin = 0
    for seed in range(n_seeds):
        s = _visited_ids(lib, single, 0.3, 1.0, seed, params)
        t = _visited_ids(lib, twins, 0.3, 1.0, seed, params)
        if s and 1 in s:
            n_single += 1
        if t and (1 in t or 2 in t):
            n_twin += 1
        # Twins share a group, so elementarity must stop both appearing at once.
        if t and 1 in t and 2 in t:
            return "FAIL"

    # Guard against a vacuous pass: if utility parameters drift so the shop is
    # always or never chosen, both counts match trivially and the test proves
    # nothing. Fail loudly instead of going quietly green.
    if n_single in (0, n_seeds):
        return "FAIL"

    return "PASS" if n_single == n_twin else "FAIL"


def run_des_duration_test(lib, csv_file="des_duration.csv", start_battery=0.5):
    """
    Behavioural realism: scheduled durations should land near their desired value.

    Kept out of run_test() deliberately. run_test() applies every structural check
    to every fixture; this one is a statement about preferences, and the other
    fixtures were not built with the schedule slack needed to satisfy it. Running
    it against its own purpose-built fixture keeps the structural suite readable.
    """
    csv_path = Path(__file__).parent / csv_file
    if not csv_path.exists():
        return "SKIP"

    activities = pd.read_csv(csv_path)
    activities_array, num_activities = initialise_and_personalise_activities(activities)
    params = initialize_utility()

    lib.set_fixed_initial_soc(c_double(start_battery))
    lib.set_utility_error_std_dev(c_double(0.0))   # deterministic
    lib.set_random_seed(c_int(42))

    result = run_dp(lib, activities_array, num_activities, params)
    if result is None:
        lib.free_bucket()
        return "FAIL"

    best_label, _ = result
    schedule = extract_schedule(best_label, activities_array, activities)
    schedule = schedule.sort_values('start_time').reset_index(drop=True)
    verdict = "PASS" if check_des_duration(schedule, activities) else "FAIL"
    lib.free_bucket()
    return verdict


def _activity_index(activities):
    """id -> row, built once. _path_utility() is called on every enumerated
    schedule, so rebuilding this from the DataFrame inside it made the optimality
    test ~5x slower than the whole rest of the suite."""
    return {int(row["id"]): row for _, row in activities.iterrows()}


def _travel_tables(acts):
    """Travel time and SoC cost for every ordered pair, precomputed for the same
    reason: they depend only on the pair, never on the schedule around it."""
    tables = {}
    for i, a1 in acts.items():
        for j, a2 in acts.items():
            tables[(i, j)] = (_travel_intervals(a1, a2), _travel_soc(a1, a2))
    return tables


def _path_utility(path, acts, n, params, start_soc, travel):
    """
    Mirror of update_utility() in src/scheduling.c, written independently so it
    can disagree with the C if one of them is wrong.

    Handles travel and the SoC that travel burns. Charging is NOT modelled -- that
    would pull in the charge-cost, delta-SoC and gamma terms plus the charging
    error draws. run_optimality_test() refuses any fixture that charges.

    path is [(act_id, start_interval, duration_intervals), ...]. Utility is
    accumulated on ENTERING each activity, exactly as the C does: entering an
    activity pays its ASC, the travel to reach it and its start-time deviation,
    and settles up the duration deviation of the activity just left.
    """
    utility = 0.0
    soc = start_soc

    for k in range(1, len(path)):
        prev_id, _, prev_duration = path[k - 1]
        cur_id, cur_start, _ = path[k]
        prev_act, cur_act = acts[prev_id], acts[cur_id]
        # The C subtracts 1 from the CSV group, so home becomes group 0.
        prev_group = int(prev_act["group"]) - 1
        cur_group = int(cur_act["group"]) - 1

        travel_intervals, travel_soc = travel[(prev_id, cur_id)]

        # No charging, so SoC only ever falls, and it falls on the drive over.
        # This is the SoC the activity starts with (soc_at_activity_start in C).
        soc -= travel_soc

        utility += params["asc"][cur_group]
        utility += TRAVEL_TIME_PENALTY * travel_intervals

        if prev_group != 0 and not int(prev_act["is_service_station"]):
            utility += params["short"][prev_group] * TIME_INTERVAL * max(
                0, int(prev_act["des_duration"]) - prev_duration)
            utility += params["long"][prev_group] * TIME_INTERVAL * max(
                0, prev_duration - int(prev_act["des_duration"]))

        if cur_group != 0 and not int(cur_act["is_service_station"]):
            utility += params["early"][cur_group] * TIME_INTERVAL * max(
                0, int(cur_act["des_start_time"]) - cur_start)
            utility += params["late"][cur_group] * TIME_INTERVAL * max(
                0, cur_start - int(cur_act["des_start_time"]))

        # SoC anxiety, which dawn and dusk are exempt from.
        if cur_id != 0 and cur_id != n - 1:
            utility += THETA_SOC * max(0.0, SOC_THRESHOLD - soc)

    return utility


def _brute_force_best(activities, params, start_soc):
    """
    Enumerate every legal schedule and return the best (utility, path).

    Only tractable on a deliberately small fixture. The feasibility rules mirrored
    here are the ones from is_feasible() that can bite on a no-charging fixture:
    time windows, duration bounds, group elementarity (home exempt, matching
    mem_contains() in src/utils.c), a non-negative SoC after every drive, and
    enough time left to reach the next activity and still get home.
    """
    acts = _activity_index(activities)
    travel = _travel_tables(acts)
    n = len(activities)
    middle = [i for i in range(n) if i != 0 and i != n - 1]
    best = (float("-inf"), None)

    def extend(sequence, index, time, soc, built):
        nonlocal best
        act_id = sequence[index]
        act = acts[act_id]

        if time < int(act["earliest_start"]) or time > int(act["latest_start"]):
            return

        if index == len(sequence) - 1:
            # The final activity is stretched to fill the horizon.
            duration = HORIZON - 1 - time
            if int(act["min_duration"]) <= duration <= int(act["max_duration"]):
                path = built + [(act_id, time, duration)]
                utility = _path_utility(path, acts, n, params, start_soc, travel)
                if utility > best[0]:
                    best = (utility, path)
            return

        next_id = sequence[index + 1]
        nxt = acts[next_id]
        # Travel and its SoC cost do not depend on how long we stay, so hoist them.
        travel_intervals, travel_soc = travel[(act_id, next_id)]
        soc_on_arrival = soc - travel_soc
        if soc_on_arrival < 0:
            return
        home_run = travel[(next_id, n - 1)][0]
        latest = int(nxt["latest_start"])
        next_min = int(nxt["min_duration"])

        for duration in range(int(act["min_duration"]), int(act["max_duration"]) + 1):
            arrival = time + duration + travel_intervals
            # Staying longer only pushes arrival later, so once either of these
            # trips we can stop rather than keep testing longer stays.
            if arrival > latest:
                break
            if arrival + next_min + home_run >= HORIZON - 1:
                break
            extend(sequence, index + 1, arrival, soc_on_arrival,
                   built + [(act_id, time, duration)])

    for size in range(len(middle) + 1):
        for chosen in itertools.permutations(middle, size):
            seen = set()
            legal = True
            for act_id in chosen:
                group = int(acts[act_id]["group"])
                if group != 1:  # home is exempt from the no-repeat rule
                    if group in seen:
                        legal = False
                        break
                    seen.add(group)
            if legal:
                extend([0] + list(chosen) + [n - 1], 0, 0, start_soc, [])

    return best


def run_optimality_test(lib, csv_file="optimality.csv", start_battery=0.5):
    """
    Does the DP find the BEST schedule, not just a legal one?

    Every other test in this suite checks that the schedule obeys the constraints.
    None of them would notice if the DP returned a valid but second-best answer --
    a dominance rule that discards too much, or a bug in the utility accumulation,
    would slip through all of them.

    So: enumerate every legal schedule for a tiny fixture and check the DP found
    the top one. Two assertions, in order:

      1. the utility the C reports for its own path matches _path_utility()
      2. no enumerated schedule beats it

    Doing (1) first matters. _path_utility() is a hand-copy of update_utility(),
    and if someone changes the C without changing the copy, (1) fails and says the
    copy has drifted -- rather than (2) failing and blaming the DP for a
    disagreement that is really the test's fault.
    """
    csv_path = Path(__file__).parent / csv_file
    if not csv_path.exists():
        return "SKIP"

    activities = pd.read_csv(csv_path)

    # _path_utility() models travel but not charging.
    if activities["is_charging"].sum() != 0:
        return "SKIP"

    activities_array, num_activities = initialise_and_personalise_activities(activities)
    params = initialize_utility()

    lib.set_fixed_initial_soc(c_double(start_battery))
    lib.set_utility_error_std_dev(c_double(0.0))   # deterministic
    lib.set_random_seed(c_int(42))

    result = run_dp(lib, activities_array, num_activities, params)
    if result is None:
        lib.free_bucket()
        return "FAIL"

    best_label, _ = result
    dp_utility = float(best_label.contents.utility)
    schedule = extract_schedule(best_label, activities_array, activities)
    schedule = schedule.sort_values("start_time").reset_index(drop=True)
    lib.free_bucket()

    dp_path = [
        (int(row["act_id"]), _interval(row["start_time"]), int(row["duration"]))
        for _, row in schedule.iterrows()
    ]

    # (1) our copy of the utility function still agrees with the C
    acts = _activity_index(activities)
    mirrored = _path_utility(dp_path, acts, num_activities, params, start_battery,
                             _travel_tables(acts))
    if abs(mirrored - dp_utility) > 1e-6:
        print("    optimality: _path_utility() no longer matches update_utility() in "
              "src/scheduling.c -- update the copy before trusting this test")
        return "FAIL"

    # (2) nothing legal beats what the DP found
    best_utility, _ = _brute_force_best(activities, params, start_battery)
    if best_utility > dp_utility + 1e-6:
        print(f"    optimality: DP returned {dp_utility:.4f} but a legal schedule "
              f"scores {best_utility:.4f}")
        return "FAIL"

    return "PASS"


def run_test(lib, csv_file, start_battery):
    csv_path = Path(__file__).parent / csv_file
    if not csv_path.exists():
        return "SKIP"

    activities = pd.read_csv(csv_path)
    activities_array, num_activities = initialise_and_personalise_activities(activities)
    params = initialize_utility()

    lib.set_fixed_initial_soc(c_double(start_battery))
    lib.set_utility_error_std_dev(c_double(0.0))
    lib.set_random_seed(c_int(42))

    result = run_dp(lib, activities_array, num_activities, params)
    if result is None:
        return "FAIL"

    best_label, _ = result
    schedule = extract_schedule(best_label, activities_array, activities)
    schedule = schedule.sort_values('start_time').reset_index(drop=True)

    if not check_battery(schedule):
        return "FAIL"
    if not check_travel_consumption(schedule):
        return "FAIL"
    if not check_contiguity(schedule, activities):
        return "FAIL"
    if not check_times(schedule, activities):
        return "FAIL"
    if not check_charging(schedule, activities):
        return "FAIL"
    if not check_durations(schedule, activities):
        return "FAIL"
    if not check_service_station(schedule, activities):
        return "FAIL"
    if not check_no_repeats(schedule, activities):
        return "FAIL"
    if not check_horizon(schedule):
        return "FAIL"

    return "PASS"


def main():
    print("Running tests...")

    lib_path = compile_code()
    lib = CDLL(lib_path)

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
    lib.set_random_seed.argtypes = [c_int]
    lib.set_fixed_initial_soc.argtypes = [c_double]
    lib.set_utility_error_std_dev.argtypes = [c_double]

    results = []

    results.append(("travel_consumption", run_test(lib, "travel_consumption.csv", 0.5)))
    lib.free_bucket()

    results.append(("charging_rates", run_test(lib, "charging_rates.csv", 0.3)))
    lib.free_bucket()

    results.append(("time_windows", run_test(lib, "time_windows.csv", 0.5)))
    lib.free_bucket()

    results.append(("soc_never_negative", run_test(lib, "soc_never_negative.csv", 0.1)))
    lib.free_bucket()

    results.append(
        ("soc_never_exceeds_100", run_test(lib, "soc_never_exceeds_100.csv", 0.95))
    )
    lib.free_bucket()

    results.append(("duration_bounds", run_test(lib, "duration_bounds.csv", 0.5)))
    lib.free_bucket()

    results.append(("service_station", run_test(lib, "service_station.csv", 0.3)))
    lib.free_bucket()

    results.append(("no_group_repeats", run_test(lib, "no_group_repeats.csv", 0.5)))
    lib.free_bucket()

    results.append(("horizon_constraint", run_test(lib, "horizon_constraint.csv", 0.5)))
    lib.free_bucket()

    # Behavioural: durations should sit near des_duration, not pinned to min.
    # Runs on its own fixture via its own runner -- see run_des_duration_test.
    results.append(("des_duration", run_des_duration_test(lib)))

    # Charge/no-charge twin rows (see duplicate_for_choice in prepare_sheffield_data.py).
    # check_no_repeats doubles as the elementarity guard here: both twins share a
    # group, so a schedule containing both would fail.
    results.append(("charge_choice", run_test(lib, "charge_choice.csv", 0.3)))
    lib.free_bucket()

    # base_id error-draw sharing (frees its own buckets internally).
    results.append(("twin_error_sharing", run_twin_error_sharing_test(lib)))

    # Optimality: brute-force every legal schedule and check the DP found the best
    # one. The only tests here that check the answer is RIGHT rather than merely
    # legal -- see run_optimality_test.
    #
    # Two fixtures. The simple one is 4 activities with no travel, so it isolates
    # the duration/timing terms. The complex one is 6 activities with real
    # distances, a SoC low enough for the anxiety term to bite, and two shops
    # sharing a group so elementarity actually rules paths out.
    results.append(("optimality_simple", run_optimality_test(lib, "optimality.csv", 0.5)))
    results.append(("optimality_complex",
                    run_optimality_test(lib, "optimality_complex.csv", 0.35)))

    print()
    for name, verdict in results:
        print(f"  {verdict:4}  {name}")

    verdicts = [verdict for _, verdict in results]
    passed = verdicts.count("PASS")
    failed = verdicts.count("FAIL")

    print(f"\nPassed: {passed}, Failed: {failed}")

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
