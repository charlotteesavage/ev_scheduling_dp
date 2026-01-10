# Running Dylan's CPLEX Notebook

## Quick Start: View Data Without CPLEX

The easiest way to understand the Dylan example without installing CPLEX:

```bash
cd dylan_data
python explore_dylan_data.py
```

This will show you:
- The schedule structure
- Travel times format
- Input/output format differences between CPLEX and your DP algorithm

---

## Option 1: Run Full CPLEX Notebook (Requires License)

### Prerequisites

1. **Install IBM CPLEX** - Choose one:

   **A. Academic/Student (FREE)**
   - Register at: https://www.ibm.com/academic/technology/data-science
   - Download CPLEX Studio (free for students/academics)
   - Get Community Edition license

   **B. Trial Version**
   - Get 90-day trial: https://www.ibm.com/products/ilog-cplex-optimization-studio

   **C. Full License**
   - Purchase commercial license

### Installation Steps

```bash
# 1. Create virtual environment
python3 -m venv venv_dylan
source venv_dylan/bin/activate  # On Windows: venv_dylan\Scripts\activate

# 2. Install Python packages
pip install -r requirements_dylan.txt

# 3. Install CPLEX Python API
# If you installed CPLEX Studio, the Python API is at:
# Mac: /Applications/CPLEX_Studio*/cplex/python/3.*/x86-64_osx/
# Linux: /opt/ibm/ILOG/CPLEX_Studio*/cplex/python/3.*/x86-64_linux/
# Windows: C:\Program Files\IBM\ILOG\CPLEX_Studio*\cplex\python\3.*\x64_win64\

pip install /path/to/your/cplex/python/*/platform_dir/

# OR for Community Edition:
pip install docplex

# 4. Launch Jupyter
jupyter notebook
```

### Running the Notebook

1. Open `solution_analysis.ipynb` in Jupyter
2. Run Cell 0 (imports) - should work without errors
3. Run Cell 2 (data loading) - loads Dylan's schedule
4. Run Cell 3 (optimization) - **requires CPLEX license**

The notebook will:
- Load Dylan's schedule from `dylan_schedule.csv`
- Add a service station for charging
- Run 10 iterations with variance=10
- Optimize with CPLEX solver
- Save results to pickle files

### Expected Output

```
Var: 10
dict_participation: {...}
dict_st: {...}
dict_dur: {...}
dict_charging: {...}
...
Solving time: X.XX seconds.
Out of home: 10
```

---

## Option 2: View Notebook in VS Code (Read-Only)

VS Code can render Jupyter notebooks without running them:

1. Open VS Code
2. Install "Jupyter" extension if not already installed
3. Open `dylan_data/solution_analysis.ipynb`
4. View cells and structure (but cannot run optimization)

---

## Option 3: Alternative - Run Your DP Algorithm Instead

Instead of running the CPLEX notebook, adapt Dylan's data to your DP algorithm:

```bash
# 1. Convert Dylan's format to DP format
python dylan_data/convert_dylan_to_dp.py

# 2. Run DP algorithm on Dylan's data
python tests/test_dylan_schedule.py
```

This approach:
- **Pros**: No CPLEX license needed, uses your existing C code
- **Cons**: Different optimization method (DP vs CPLEX), results may differ

---

## Understanding the CPLEX Results

If you successfully run the notebook, you'll get:

### Output Files (pickle format)
- `stats_dict_part.pkl` - Activity participation
- `stats_dict_st.pkl` - Start times
- `stats_dict_dur.pkl` - Durations
- `stats_dict_charging.pkl` - Charging decisions
- `stats_dict_soc*.pkl` - State of charge data
- `stats_dict_charger_*.pkl` - Charger type selections

### Reading Results

```python
import pickle

# Load results
with open('stats_dict_charging_var.pkl', 'rb') as f:
    results = pickle.load(f)

# Results structure:
# [dict_soc, dict_charging, dict_charging_dur]
# Each dict maps activity_id -> list of values across iterations
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'docplex'"
→ Install CPLEX: `pip install docplex` (requires license)

### "CPLEX Error 32201: No license found"
→ You need a valid CPLEX license. Get academic/trial license.

### "ImportError: cannot import name 'optimize_schedule'"
→ Make sure you're in `dylan_data/` directory or add to Python path:
```python
import sys
sys.path.append('dylan_data')
```

### Notebook won't run but you want to see structure
→ Use: `python explore_dylan_data.py` (no CPLEX needed)

---

## Comparison: CPLEX vs Your DP Algorithm

| Feature | CPLEX (Dylan) | Your DP Algorithm |
|---------|---------------|-------------------|
| **Solver** | IBM CPLEX (commercial) | Custom C implementation |
| **License** | Required (paid/academic) | Open source |
| **Time representation** | Continuous (hours) | Discrete (5-min intervals) |
| **Optimization** | Mixed-integer programming | Dynamic programming |
| **Solution quality** | Global optimum | Pareto-optimal |
| **Speed** | ~10 sec/iteration | Sub-second typical |
| **Charging model** | 3 types, cost-optimized | 3 types, utility-based |

---

## Summary

**Easiest path**: Run `python explore_dylan_data.py` to understand the data

**With CPLEX license**: Follow Option 1 to run full notebook

**Without CPLEX**: Use Option 3 to test with your DP algorithm

**Question?** The data structure is now clear - you can adapt Dylan's format to your DP input format!
