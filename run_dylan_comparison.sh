#!/bin/bash
# Master script to run complete Dylan comparison pipeline

set -e  # Exit on error

echo "================================================================================"
echo "                    DYLAN DP vs CPLEX COMPARISON PIPELINE"
echo "================================================================================"

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo ""
echo "Working directory: $SCRIPT_DIR"
echo ""

# Step 1: Convert Dylan's data
echo "================================================================================"
echo "STEP 1: Converting Dylan's schedule to DP format"
echo "================================================================================"
echo ""

if [ ! -f "dylan_data/dylan_schedule.csv" ]; then
    echo "❌ ERROR: dylan_data/dylan_schedule.csv not found!"
    echo "Please ensure the file exists before running this script."
    exit 1
fi

python3 dylan_data/convert_dylan_to_dp.py

if [ $? -ne 0 ]; then
    echo "❌ ERROR: Conversion failed!"
    exit 1
fi

echo ""
echo "✓ Conversion complete!"
echo ""

# Step 2: Run DP algorithm
echo "================================================================================"
echo "STEP 2: Running DP algorithm on Dylan's schedule"
echo "================================================================================"
echo ""

python3 tests/test_dylan_schedule.py

if [ $? -ne 0 ]; then
    echo "❌ ERROR: DP algorithm failed!"
    exit 1
fi

echo ""
echo "✓ DP algorithm complete!"
echo ""

# Step 3: Generate visualizations
echo "================================================================================"
echo "STEP 3: Generating comparison visualizations"
echo "================================================================================"
echo ""

# Check if matplotlib is installed
python3 -c "import matplotlib" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  WARNING: matplotlib not installed. Skipping visualizations."
    echo "To install: pip3 install matplotlib pandas numpy"
    echo ""
else
    python3 dylan_data/visualize_comparison.py

    if [ $? -ne 0 ]; then
        echo "⚠️  WARNING: Visualization failed, but continuing..."
    else
        echo ""
        echo "✓ Visualizations complete!"
        echo ""
    fi
fi

# Summary
echo "================================================================================"
echo "                              PIPELINE COMPLETE!"
echo "================================================================================"
echo ""
echo "Generated files:"
echo "  ✓ dylan_data/dylan_schedule_dp_format.csv       - Converted schedule"
echo "  ✓ dylan_data/dylan_optimal_schedule_dp.csv      - DP optimized schedule"
echo "  ✓ dylan_data/dylan_dp_metrics.csv               - Key metrics"

if python3 -c "import matplotlib" 2>/dev/null; then
    echo "  ✓ dylan_data/comparison_gantt.png               - Activity timelines"
    echo "  ✓ dylan_data/comparison_soc.png                 - SOC trajectories"
    echo "  ✓ dylan_data/comparison_charging.png            - Charging patterns"
    echo "  ✓ dylan_data/comparison_metrics.png             - Metrics comparison"
fi

echo ""
echo "Next steps:"
echo "  1. Review the results:"
echo "     cat dylan_data/dylan_optimal_schedule_dp.csv"
echo ""
echo "  2. View visualizations (if generated):"
echo "     open dylan_data/comparison_*.png"
echo ""
echo "  3. (Optional) Compare with CPLEX:"
echo "     jupyter notebook dylan_data/solution_analysis.ipynb"
echo ""
echo "================================================================================"
