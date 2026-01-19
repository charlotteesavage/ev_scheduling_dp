# Quick Start Guide

## 1️⃣ Simple 3-Day Test (Learn the Basics)

```bash
cd testing_latest
python simple_multi_day_example.py
```

**What it does**: Runs 3 days where each day's ending SOC becomes next day's starting SOC.

---

## 2️⃣ Full Week Simulation

```bash
python multi_day_testing.py --num-days 7 --starting-soc 0.30
```

**What it does**:
- Simulates 7 days with SOC carryover
- Starts with 30% battery
- Saves detailed results to `multi_day_results/`
- Shows if the schedule is sustainable

---

## 3️⃣ Test Random SOC Variability

```bash
python random_soc_testing.py
```

**What it does**:
- Runs 10 times with different random starting SOC
- Shows how starting battery level affects outcomes
- Tests reproducibility of random generation

---

## 4️⃣ Custom Tests

### Low Battery Challenge
```bash
python multi_day_testing.py --num-days 7 --starting-soc 0.20 --min-soc 0.15
```

### Month-Long Simulation
```bash
python multi_day_testing.py --num-days 30 --starting-soc 0.50
```

### Different Person
```bash
python multi_day_testing.py --person person_259 --num-days 7
```

---

## Understanding the Output

### ✓ Good Signs
- "Net positive SOC trend" or "SOC is approximately stable"
- Simulation completes all requested days
- Average daily SOC change ≥ 0%

### ⚠️ Warning Signs
- "Net negative SOC trend detected"
- "WARNING: SOC below minimum threshold"
- Simulation stops before completing all days
- Average daily SOC change < -1%

---

## Key Files Created

| File | Purpose |
|------|---------|
| `multi_day_testing.py` | Full multi-day simulation |
| `simple_multi_day_example.py` | Learning example |
| `random_soc_testing.py` | Random SOC testing |
| `MULTI_DAY_TESTING_README.md` | Detailed documentation |

---

## Common Commands

```bash
# Basic week test
python multi_day_testing.py --num-days 7

# Test with 50% starting battery
python multi_day_testing.py --num-days 7 --starting-soc 0.50

# Month test with custom output
python multi_day_testing.py --num-days 30 --output-dir my_results

# Low battery resilience test
python multi_day_testing.py --num-days 7 --starting-soc 0.20

# Random SOC comparison
python random_soc_testing.py
```

---

## Need Help?

1. **Detailed guide**: Read [MULTI_DAY_TESTING_README.md](MULTI_DAY_TESTING_README.md)
2. **Full documentation**: Read [../TESTING_GUIDE.md](../TESTING_GUIDE.md)
3. **Code examples**: Look at [simple_multi_day_example.py](simple_multi_day_example.py)

---

## Using time(NULL) for Random Seeds

### In Python
```python
import time

# Use current time as seed (equivalent to C's time(NULL))
seed = int(time.time())
lib.set_random_seed(c_int(seed))
```

### For Multiple Runs
```python
for i in range(10):
    # Different seed each iteration
    seed = int(time.time() * 1000) + i  # Millisecond precision
    lib.set_random_seed(c_int(seed))
    # ... run test ...
```

---

## Quick Workflow

1. Start simple:
   ```bash
   python simple_multi_day_example.py
   ```

2. Try a week:
   ```bash
   python multi_day_testing.py --num-days 7
   ```

3. Check the results in `multi_day_results/`

4. Analyze the `multi_day_summary.csv`

5. Experiment with different settings!
