#!/bin/bash
# Script to set up and run the Dylan CPLEX notebook

echo "Setting up environment for Dylan CPLEX notebook..."

# Create a virtual environment if it doesn't exist
if [ ! -d "venv_dylan" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv_dylan
fi

# Activate virtual environment
source venv_dylan/bin/activate

# Install required packages
echo "Installing required packages..."
pip install --upgrade pip
pip install pandas numpy matplotlib scipy jupyter ipykernel
pip install geopy seaborn googlemaps
pip install ray  # For parallel processing

# Install CPLEX (requires license)
# Option 1: If you have IBM CPLEX installed locally
# pip install /path/to/cplex/python

# Option 2: Academic/Community Edition (free for students/academics)
# pip install docplex

echo ""
echo "======================================================================"
echo "IMPORTANT: CPLEX Installation"
echo "======================================================================"
echo "This notebook requires IBM CPLEX solver."
echo ""
echo "Options:"
echo "1. Academic/Student: Free Community Edition"
echo "   Visit: https://www.ibm.com/academic/technology/data-science"
echo "   Then run: pip install docplex"
echo ""
echo "2. If you have CPLEX installed locally:"
echo "   Run: pip install /Applications/CPLEX_Studio*/cplex/python/3.*/[your-platform]"
echo ""
echo "3. Alternative: Try running without CPLEX to see data structure only"
echo "======================================================================"
echo ""

# Launch Jupyter
echo "Starting Jupyter notebook..."
echo "Navigate to: dylan_data/solution_analysis.ipynb"
jupyter notebook

deactivate
