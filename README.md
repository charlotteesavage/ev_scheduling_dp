# Activity Scheduling Optimizer

A dynamic programming-based activity scheduling optimizer that uses a hybrid C/Python implementation for efficient computation of individual activity schedules.

## Project Overview

This project implements an activity scheduling optimization algorithm that:
- Uses dynamic programming to find optimal activity schedules
- Interfaces C code (for performance) with Python (for data processing)
- Processes population data and activity constraints
- Supports multiple scenario simulations

## System Requirements

### Dependencies
- **Python**: 3.9 or higher
- **C Compiler**: GCC (for compiling shared libraries)
- **Operating System**: macOS or Linux

### Python Packages
Core dependencies (specified in `pyproject.toml`):
- `numpy >= 2.0.0` - Numerical computations and array operations
- `pandas >= 2.0.0` - Data manipulation and CSV handling
- `tqdm >= 4.60.0` - Progress bars for long-running computations

Development dependencies:
- `pytest >= 8.0.0` - Testing framework

## Installation

### Using Conda (Recommended)

If using the conda environment `dp_new`:

```bash
# Activate the environment
conda activate dp_new

# Install dependencies from pyproject.toml
pip install -e .
```

### Alternative: Create New Environment

```bash
# Create new conda environment
conda create -n dp_new python=3.12
conda activate dp_new

# Install dependencies
pip install -e .
```

## Project Structure

```
scheduling_code/
├── pyproject.toml           # Project dependencies and metadata
├── README.md                # This file
├── Makefile                 # Build configuration for C code
├── main_slice_cs.py         # Main scheduling script
├── test_compilation.py      # C compilation test script
├── scheduling_CS.c          # Core scheduling algorithm (C)
├── scheduling_CS.h          # C header file
├── scheduling_main.c        # C main function
├── scheduling_CS.so         # Compiled shared library (generated)
└── cloe_covid_paper_code/   # Additional code from original paper
```

## Building the Project

The C code must be compiled into a shared library before running the Python scripts.

### Using Make:
```bash
make
```

### Manual Compilation:
```bash
gcc -m64 -O3 -shared -fPIC -o scheduling_CS.so scheduling_CS.c scheduling_main.c -lm
```

### Testing Compilation:
```bash
python test_compilation.py
```

## Usage

### Running the Optimizer

```bash
python main_slice_cs.py
```

The main script will:
1. Compile the C code automatically
2. Load population and activity data
3. Run optimization for specified scenarios
4. Save results to the output directory

### Configuration

Key parameters in `main_slice_cs.py`:
- `LOCAL`: Geographic location (default: "NewYork")
- `TIME_INTERVAL`: Time discretization in minutes (default: 5)
- `HORIZON`: Time horizon for scheduling (24 hours)
- `num_act_to_select`: Number of activities to consider per individual (default: 15)

## Development

### Running Tests

```bash
# Test C compilation and library loading
python test_compilation.py

# Run pytest (if tests are added)
pytest
```

### Code Structure

The project uses Python's `ctypes` library to interface with C code:
- **Python side**: Data loading, preprocessing, result handling
- **C side**: Performance-critical optimization algorithms

## Notes for Supervisors

This is a research project implementing activity scheduling optimization. The hybrid C/Python approach allows for:
- **Performance**: C implementation for computationally intensive DP algorithms
- **Flexibility**: Python for data processing and analysis
- **Reproducibility**: Explicit dependency management via `pyproject.toml`

The algorithm optimizes individual daily schedules considering:
- Activity time windows and durations
- Travel times between locations
- Utility functions for schedule quality
- Various scenario constraints (e.g., COVID-19 restrictions)

## License

[Add your license information here]

## Contact

Charlotte Savage - [Your contact information]
