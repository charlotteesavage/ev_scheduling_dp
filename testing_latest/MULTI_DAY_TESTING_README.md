# Multi-Day EV Scheduling Simulation Guide

This guide explains how to run multi-day simulations where each day's ending State of Charge (SOC) becomes the next day's starting SOC.

## Overview

The multi-day simulation allows you to:
- Test how EV schedules evolve over multiple days
- Track SOC trends (is the person charging enough to sustain their schedule?)
- Identify if a charging strategy is sustainable long-term
- See how low starting SOC affects activity feasibility

## Files

1. **multi_day_testing.py** - Full-featured multi-day simulation script
2. **simple_multi_day_example.py** - Minimal example for learning
3. **testing_check.py** - Base testing script (already exists)

## Quick Start

### Option 1: Simple 3-Day Test

```bash
cd testing_latest
python simple_multi_day_example.py
```

This runs a simple 3-day simulation starting with 30% SOC and prints results to console.

### Option 2: Full Multi-Day Simulation

```bash
cd testing_latest
python multi_day_testing.py --num-days 7 --starting-soc 0.30
```

**Common usage examples:**

```bash
# Run a 7-day week simulation
python multi_day_testing.py --num-days 7 --starting-soc 0.40

# Run a 30-day month simulation
python multi_day_testing.py --num-days 30 --starting-soc 0.50

# Test with low starting SOC
python multi_day_testing.py --num-days 7 --starting-soc 0.20 --min-soc 0.15

# Test different person
python multi_day_testing.py --person person_259 --num-days 7

# Save to custom directory
python multi_day_testing.py --num-days 7 --output-dir my_results/week1
```

## Command Line Arguments

### multi_day_testing.py

| Argument | Default | Description |
|----------|---------|-------------|
| `--person` | `person_ending_1263` | Folder name under testing_latest/ |
| `--csv` | `activities_with_charge_values.csv` | Activities CSV filename |
| `--num-days` | `7` | Number of days to simulate |
| `--starting-soc` | `0.30` | Starting SOC for day 1 (0.0 to 1.0) |
| `--min-soc` | `0.20` | Minimum SOC threshold - stops if SOC drops below |
| `--output-dir` | Auto-generated | Directory to save results |

## How It Works

### The SOC Carryover Process

1. **Day 1**: Starts with the specified `--starting-soc` (e.g., 30%)
2. **Day 1 runs**: Person does activities, may charge, ends with some SOC (e.g., 35%)
3. **Day 2**: Starts with Day 1's ending SOC (35%)
4. **Day 2 runs**: Activities and charging happen, ends with new SOC (e.g., 32%)
5. **Day 3**: Starts with Day 2's ending SOC (32%)
6. **Process repeats** for all days

### Stopping Conditions

The simulation stops if:
1. All requested days complete successfully
2. SOC drops below `--min-soc` threshold (default 20%)
3. No feasible solution is found (activities not achievable with current SOC)

## Understanding the Output

### Console Output

For each day, you'll see:
```
============================================================
DAY 1 - Starting SOC: 30.00%
============================================================
Initializing 15 activities...
Running DP Algorithm...
DP completed in 2.34 seconds

Day 1 Summary:
  Initial SOC: 30.00%
  Final SOC: 35.00%
  SOC Change: +5.00%
  Utility: 45.23
  Charging sessions: 2
  Total charging time: 1.50 hours
  Total charging cost: £3.45
```

### Aggregate Statistics

At the end, you'll see:
```
AGGREGATE STATISTICS
============================================================
Total days simulated: 7
Starting SOC (Day 1): 30.00%
Ending SOC (Day 7): 38.00%
Total SOC change: +8.00%
Average daily SOC change: +1.14%
Total utility (sum): 315.67
Average daily utility: 45.10
Total charging sessions: 14
Total charging time: 10.50 hours
Total charging cost: £24.15
Total computation time: 16.45 seconds

✓ Net positive SOC trend detected
   Average daily gain: +1.14%
```

### Saved Files

Results are saved to `testing_latest/multi_day_results/<person>/`:

- `day_001_schedule.csv` - Detailed schedule for day 1
- `day_002_schedule.csv` - Detailed schedule for day 2
- ... (one file per day)
- `multi_day_summary.csv` - Summary of all days

### Summary CSV Format

The `multi_day_summary.csv` contains:
| Column | Description |
|--------|-------------|
| Day | Day number |
| Initial_SOC | Starting SOC for that day |
| Final_SOC | Ending SOC for that day |
| SOC_Change | Daily SOC change (positive = charged more than consumed) |
| Utility | Total utility for that day |
| Charging_Sessions | Number of times charged |
| Charging_Time_hrs | Total hours spent charging |
| Charging_Cost_GBP | Total cost in £ |
| Computation_Time_sec | Time to compute schedule |

## Use Cases

### 1. Sustainability Testing

**Question**: Is this charging behavior sustainable over a week?

```bash
python multi_day_testing.py --num-days 7 --starting-soc 0.50
```

Look at the "Average daily SOC change" in the output:
- **Negative**: Person is using more battery than they're charging → unsustainable
- **~Zero**: Person is charging enough to maintain SOC → sustainable
- **Positive**: Person is charging more than needed → very sustainable (or over-charging)

### 2. Low Battery Resilience

**Question**: What happens if the person starts the week with low battery?

```bash
python multi_day_testing.py --num-days 7 --starting-soc 0.20 --min-soc 0.15
```

This tests whether the schedule is still feasible with low starting SOC.

### 3. Long-Term Trends

**Question**: What happens over a month?

```bash
python multi_day_testing.py --num-days 30 --starting-soc 0.50
```

Look for patterns:
- Does SOC trend upward or downward?
- Are there day-to-day fluctuations?
- Does the pattern stabilize?

### 4. Different Starting Conditions

**Question**: How does starting SOC affect outcomes?

```bash
# Test 1: Start high
python multi_day_testing.py --num-days 7 --starting-soc 0.80 --output-dir results/high_soc

# Test 2: Start medium
python multi_day_testing.py --num-days 7 --starting-soc 0.50 --output-dir results/med_soc

# Test 3: Start low
python multi_day_testing.py --num-days 7 --starting-soc 0.25 --output-dir results/low_soc
```

Then compare the `multi_day_summary.csv` files from each test.

## Interpreting Results

### SOC Trends

**Net Negative Trend** (⚠️ Warning shown)
- Average daily SOC change < -1%
- Person is not charging enough to sustain their activities
- Will eventually be unable to complete schedule
- **Action**: Person needs to charge more frequently or for longer

**Stable Trend** (✓ Good)
- Average daily SOC change between -1% and +1%
- Charging is balanced with energy consumption
- Schedule is sustainable long-term

**Net Positive Trend** (✓ Good)
- Average daily SOC change > +1%
- Person is charging more than they consume
- May indicate over-charging or conservative behavior
- Schedule is definitely sustainable

### Early Termination

If simulation stops before completing all days:

**"SOC below minimum threshold"**
- The person ran out of battery
- Indicates insufficient charging in previous days
- Try with higher `--starting-soc` or lower `--min-soc`

**"No feasible solution found"**
- The algorithm couldn't find a valid schedule
- Usually means SOC is too low to reach necessary activities
- The schedule is not achievable with current battery level

## Advanced: Custom Analysis

You can load the saved CSVs for custom analysis:

```python
import pandas as pd
import matplotlib.pyplot as plt

# Load summary
summary = pd.read_csv('testing_latest/multi_day_results/person_ending_1263/multi_day_summary.csv')

# Plot SOC over time
plt.figure(figsize=(10, 6))
plt.plot(summary['Day'], summary['Initial_SOC'], marker='o', label='Daily Starting SOC')
plt.plot(summary['Day'], summary['Final_SOC'], marker='s', label='Daily Ending SOC')
plt.xlabel('Day')
plt.ylabel('State of Charge')
plt.title('SOC Evolution Over Time')
plt.legend()
plt.grid(True)
plt.savefig('soc_trend.png')

# Plot daily charging costs
plt.figure(figsize=(10, 6))
plt.bar(summary['Day'], summary['Charging_Cost_GBP'])
plt.xlabel('Day')
plt.ylabel('Charging Cost (£)')
plt.title('Daily Charging Costs')
plt.savefig('charging_costs.png')
```

## Troubleshooting

### Error: "Missing activities file"
- Check that the `--person` folder exists under `testing_latest/`
- Check that the CSV file exists in that folder

### Error: Import errors from testing_check.py
- Make sure you're running from the `testing_latest/` directory
- Ensure `testing_check.py` is in the same directory

### Simulation stops immediately
- Check your `--min-soc` value - it might be too high
- Check your `--starting-soc` - it might be too low for the schedule
- Verify your activities CSV has valid data

### SOC doesn't change much between days
- This could be normal if the person charges exactly what they consume
- Check individual day schedules to see charging patterns

## Modifications

### Using Different Activities Each Day

If you want different activities for each day (e.g., weekday vs weekend patterns):

```python
# In multi_day_testing.py, modify run_multi_day_simulation():

# Define activity files
weekday_file = "activities_weekday.csv"
weekend_file = "activities_weekend.csv"

for day in range(1, num_days + 1):
    # Choose activities based on day of week
    if day % 7 in [6, 7]:  # Weekend (Saturday=6, Sunday=7)
        activities_df = pd.read_csv(weekend_file)
    else:  # Weekday
        activities_df = pd.read_csv(weekday_file)

    # Rest of the code...
```

### Adding Overnight Charging

If you want to simulate overnight charging at home between days:

```python
# Add this between days in run_multi_day_simulation():

# Simulate overnight home charging (8 hours at 7kW)
overnight_charge = (7.0 / 60.0) * 8  # kWh
overnight_soc_increase = overnight_charge / 60.0  # Assuming 60kWh battery
current_soc = min(1.0, current_soc + overnight_soc_increase)
print(f"  Overnight charging: +{overnight_soc_increase:.2%} SOC")
```

## Questions?

For help with the code, check:
1. This README
2. Comments in `multi_day_testing.py`
3. The simple example in `simple_multi_day_example.py`
