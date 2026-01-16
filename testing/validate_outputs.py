"""
Validation script to verify algorithm outputs against constraints.
This can run without needing to recompile the C code.
"""

import csv
import sys
from pathlib import Path


def load_csv(filepath):
    """Load CSV as list of dictionaries."""
    data = []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            converted = {}
            for key, value in row.items():
                try:
                    if '.' in value:
                        converted[key] = float(value)
                    else:
                        converted[key] = int(value)
                except (ValueError, AttributeError):
                    converted[key] = value
            data.append(converted)
    return data


def validate_soc_constraints(schedule):
    """Verify SOC constraints (25-27)."""
    issues = []

    for i, row in enumerate(schedule):
        soc_start = row['soc_start']
        soc_end = row['soc_end']

        # Constraint 25: SOC non-negative
        if soc_start < 0:
            issues.append(f"Row {i}: SOC_start={soc_start:.3f} < 0 (Constraint 25 violated)")
        if soc_end < 0:
            issues.append(f"Row {i}: SOC_end={soc_end:.3f} < 0 (Constraint 25 violated)")

        # Constraint 26: SOC <= battery capacity
        if soc_start > 1.0:
            issues.append(f"Row {i}: SOC_start={soc_start:.3f} > 1.0 (Constraint 26 violated)")
        if soc_end > 1.0:
            issues.append(f"Row {i}: SOC_end={soc_end:.3f} > 1.0 (Constraint 26 violated)")

        # Check for charging: SOC should only increase or stay same
        if row['is_charging'] == 1 and soc_end < soc_start:
            issues.append(f"Row {i}: Charging but SOC decreased: {soc_start:.3f} → {soc_end:.3f}")

    return issues


def validate_charging_constraints(schedule, activities):
    """Verify charging-specific constraints."""
    issues = []

    # Create activity lookup
    activity_dict = {a['id']: a for a in activities}

    for i, row in enumerate(schedule):
        act_id = row['act_id']

        if act_id not in activity_dict:
            continue

        activity = activity_dict[act_id]

        # Constraint 33: Service stations must charge
        if activity.get('is_service_station', 0) == 1:
            if row['is_charging'] != 1:
                issues.append(f"Row {i}: Service station (act {act_id}) not charging (Constraint 33)")

        # Constraint 35: Valid charge mode
        if row['is_charging'] == 1:
            if row['charge_mode'] == 0:
                issues.append(f"Row {i}: Charging with mode=0 (Constraint 35 violated)")

        # Constraint 31: Charge duration <= activity duration
        if row['charge_duration'] > row['duration']:
            issues.append(f"Row {i}: Charge duration ({row['charge_duration']}) > activity duration ({row['duration']}) (Constraint 31)")

    return issues


def validate_schedule_continuity(schedule):
    """Verify schedule timing is continuous (allowing for travel time)."""
    issues = []

    for i in range(len(schedule) - 1):
        current_end = schedule[i]['start_time'] + schedule[i]['duration']
        next_start = schedule[i+1]['start_time']

        # Travel time is the difference between activity end and next start
        travel_time = next_start - current_end

        # Allow small floating point errors
        if abs(current_end - next_start) > 0.01:
            # This is expected if activities are at different locations
            # Only flag if there's overlap (next starts before current ends)
            if travel_time < -0.01:
                issues.append(
                    f"Row {i}→{i+1}: Schedule overlap: "
                    f"activity ends at {current_end:.3f}, next starts at {next_start:.3f}"
                )
            # Otherwise it's just travel time (normal)

    return issues


def validate_soc_transitions(schedule):
    """Verify SOC transitions make sense."""
    issues = []

    for i in range(len(schedule) - 1):
        current_soc_end = schedule[i]['soc_end']
        next_soc_start = schedule[i+1]['soc_start']

        # SOC should decrease or stay same between activities (due to travel)
        if next_soc_start > current_soc_end + 0.001:  # Allow small FP error
            issues.append(
                f"Row {i}→{i+1}: SOC increased during transition: "
                f"{current_soc_end:.3f} → {next_soc_start:.3f} (should only decrease due to travel)"
            )

    return issues


def validate_horizon(schedule):
    """Verify schedule fits within 24-hour horizon."""
    issues = []

    if not schedule:
        return ["Empty schedule"]

    last_activity = schedule[-1]
    end_time = last_activity['start_time'] + last_activity['duration']

    # Horizon is 24 hours = 288 intervals of 5 minutes each
    if end_time > 24:
        issues.append(f"Schedule exceeds 24-hour horizon: ends at {end_time:.3f} hours")

    return issues


def print_schedule_summary(schedule):
    """Print summary statistics."""
    print("\n" + "="*70)
    print("SCHEDULE SUMMARY")
    print("="*70)

    print(f"\nTotal activities: {len(schedule)}")

    # Charging info
    charging_activities = [r for r in schedule if r['is_charging'] == 1]
    print(f"Charging sessions: {len(charging_activities)}")

    if charging_activities:
        total_charge_time = sum(r['charge_duration'] for r in charging_activities)
        total_charge_cost = max(r['charge_cost'] for r in schedule)  # Last value is cumulative
        print(f"Total charging time: {total_charge_time:.2f} hours")
        print(f"Total charging cost: £{total_charge_cost:.2f}")

        # Charging modes used
        modes = {}
        for r in charging_activities:
            mode = r['charge_mode']
            mode_name = {1: "Slow (7kW)", 2: "Fast (22kW)", 3: "Rapid (50kW)"}.get(mode, f"Unknown ({mode})")
            modes[mode_name] = modes.get(mode_name, 0) + 1

        print("\nCharging modes:")
        for mode, count in sorted(modes.items()):
            print(f"  {mode}: {count} session(s)")

    # SOC info
    initial_soc = schedule[0]['soc_start']
    final_soc = schedule[-1]['soc_end']
    min_soc = min(min(r['soc_start'], r['soc_end']) for r in schedule)

    print(f"\nSOC profile:")
    print(f"  Initial: {initial_soc:.1%}")
    print(f"  Final: {final_soc:.1%}")
    print(f"  Minimum: {min_soc:.1%}")

    # Time info
    end_time = schedule[-1]['start_time'] + schedule[-1]['duration']
    print(f"\nSchedule duration: {end_time:.2f} hours (out of 24)")

    # Utility
    final_utility = schedule[-1]['utility']
    print(f"Final utility: {final_utility:.2f}")


def main():
    print("\n" + "="*70)
    print("ALGORITHM OUTPUT VALIDATION")
    print("="*70)

    # Paths
    repo_root = Path(__file__).parent.parent
    optimal_dir = repo_root / "testing" / "optimal_schedules"
    activities_dir = repo_root / "testing" / "activities"

    # Find schedule files
    schedule_files = list(optimal_dir.glob("*.csv"))

    if not schedule_files:
        print(f"\n❌ No schedule files found in: {optimal_dir}")
        return 1

    print(f"\nFound {len(schedule_files)} schedule file(s)")

    # Validate each schedule
    for schedule_file in schedule_files:
        print("\n" + "="*70)
        print(f"Validating: {schedule_file.name}")
        print("="*70)

        # Load schedule
        schedule = load_csv(schedule_file)

        # Print summary
        print_schedule_summary(schedule)

        # Load corresponding activities file
        # Extract person ID from schedule filename
        activities_file = activities_dir / schedule_file.name.replace("_optimal_schedule", "")

        activities = []
        if activities_file.exists():
            activities = load_csv(activities_file)
            print(f"\nLoaded {len(activities)} activities from {activities_file.name}")
        else:
            print(f"\n⚠ Activities file not found: {activities_file.name}")

        # Run validations
        print("\n" + "-"*70)
        print("CONSTRAINT VALIDATION")
        print("-"*70)

        all_issues = []

        # SOC constraints
        print("\n✓ Checking SOC constraints (25-27)...", end=" ")
        soc_issues = validate_soc_constraints(schedule)
        if soc_issues:
            print(f"FAILED ({len(soc_issues)} issues)")
            all_issues.extend(soc_issues)
        else:
            print("PASSED")

        # Charging constraints
        if activities:
            print("✓ Checking charging constraints (31, 33, 35)...", end=" ")
            charge_issues = validate_charging_constraints(schedule, activities)
            if charge_issues:
                print(f"FAILED ({len(charge_issues)} issues)")
                all_issues.extend(charge_issues)
            else:
                print("PASSED")

        # Schedule continuity
        print("✓ Checking schedule continuity...", end=" ")
        continuity_issues = validate_schedule_continuity(schedule)
        if continuity_issues:
            print(f"FAILED ({len(continuity_issues)} issues)")
            all_issues.extend(continuity_issues)
        else:
            print("PASSED")

        # SOC transitions
        print("✓ Checking SOC transitions...", end=" ")
        soc_trans_issues = validate_soc_transitions(schedule)
        if soc_trans_issues:
            print(f"FAILED ({len(soc_trans_issues)} issues)")
            all_issues.extend(soc_trans_issues)
        else:
            print("PASSED")

        # Horizon
        print("✓ Checking 24-hour horizon...", end=" ")
        horizon_issues = validate_horizon(schedule)
        if horizon_issues:
            print(f"FAILED ({len(horizon_issues)} issues)")
            all_issues.extend(horizon_issues)
        else:
            print("PASSED")

        # Report results
        print("\n" + "-"*70)
        if all_issues:
            print(f"❌ VALIDATION FAILED: {len(all_issues)} issue(s) found")
            print("-"*70)
            for issue in all_issues:
                print(f"  • {issue}")
        else:
            print("✅ VALIDATION PASSED: All constraints satisfied!")
            print("-"*70)

    print("\n" + "="*70)
    print("VALIDATION COMPLETE")
    print("="*70)
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
