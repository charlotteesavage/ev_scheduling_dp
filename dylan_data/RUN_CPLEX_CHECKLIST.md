# Quick Checklist - Running CPLEX

## Before You Start

- [ ] Python 3.x installed
- [ ] `pip` works

## Step 1: Install Dependencies (5 minutes)

```bash
pip install pandas numpy matplotlib geopy seaborn
```

- [ ] Dependencies installed

## Step 2: Install CPLEX (30 minutes)

**Choose ONE option:**

### Option A: Academic License (FREE - Best)
- [ ] Register at: https://www.ibm.com/academic/technology/data-science
- [ ] Download CPLEX Studio
- [ ] Install CPLEX Studio
- [ ] Install Python API: `cd /Applications/CPLEX_Studio*/cplex/python/3.*/*/; pip install .`
- [ ] Install docplex: `pip install docplex`

### Option B: Community Edition (FREE - Quick)
- [ ] Run: `pip install docplex`

### Option C: Trial (FREE - 90 days)
- [ ] Get trial from IBM website
- [ ] Install (same as Option A)

## Step 3: Verify Installation (1 minute)

```bash
python -c "from docplex.mp.model import Model; print('✓ CPLEX OK')"
```

- [ ] CPLEX verified

## Step 4: Run CPLEX Model (2 minutes)

```bash
cd dylan_data
python run_cplex_simple.py
```

- [ ] CPLEX completed
- [ ] Output file created: `dylan_optimal_schedule_cplex.csv`

## Step 5: Compare Results (1 minute)

```bash
python visualize_comparison.py
```

- [ ] Visualizations created
- [ ] Comparison complete!

---

## If CPLEX Doesn't Work

**Don't worry!** You can still:

1. Compare to Dylan's published results
2. Focus on your DP advantages (speed, open-source)
3. Use the DP results alone

Run just your DP:
```bash
python tests/test_dylan_schedule.py
```

---

## Total Time Estimate

- **With CPLEX**: ~40 minutes (mostly CPLEX installation)
- **Without CPLEX**: ~2 minutes (DP only)

---

## Questions?

See: [CPLEX_SETUP_GUIDE.md](../CPLEX_SETUP_GUIDE.md)
