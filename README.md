# EV Activity Scheduling (DP + Charging)

This repo contains a dynamic programming (label-setting) scheduler for an individual EV user’s daily activity plan. The core algorithm is implemented in C (`src/`) and is typically run from Python via `ctypes` for convenience and data handling.

## What it does
- Takes a set of candidate activities (time windows, min/max durations, locations, etc.) from a CSV.
- Runs a label-setting DP over a 24h horizon (5-minute intervals) to select an “optimal” activity sequence.
- Tracks EV-specific state and constraints inside the recursion, including travel energy use (SoC depletion) and charging (SoC increase + charging cost).
- Writes an output schedule CSV with start times, durations, SoC trajectory, charging time, and cumulative utility/cost.

## Main entrypoint (manual schedule checker)
The main “schedule checker” script is:
- `testing_latest/testing_check.py`

It:
- Compiles the C code into a shared library (`testing_latest/scheduling.so`) when needed.
- Loads an activities CSV, runs the DP, and writes an output schedule CSV under `testing_latest/optimal_schedules/`.

Run it with:
```bash
python3 testing_latest/testing_check.py
```

## Unit/validation tests
Validation tests live in:
- `testing_latest/validation_tests/`

Run them with:
```bash
python3 testing_latest/validation_tests/run_validation_tests.py
```

## Notes
- `environment.yml` contains the conda environment used by the Makefile helper (`make py-testing-check`) (defaults to the `dp_new` env; override with `DP_CONDA_ENV`).
- The C-only build (executable) is available via `make`, but most workflows use the Python scripts in `testing_latest/`.
