# DP Scheduling Algorithm - Test Suite

This directory contains automated tests for the DP scheduling algorithm.

## Test Structure

- `test_scheduling.c` - Main test suite with integration tests
- `Makefile` - Build configuration for compiling and running tests
- `scheduling_test.h` - Test-only header exposing internal functions (if needed)

## Running the Tests

### Quick Start

```bash
cd tests
make test
```

### Individual Commands

```bash
# Compile tests
make

# Run tests
./test_scheduling

# Clean build artifacts
make clean

# Run with memory leak detection (requires valgrind)
make memcheck
```

## Test Coverage

### 1. Parameter Initialization Test
- Verifies that global parameters are correctly set
- Checks horizon, speed, time_interval
- Validates charge rate initialization

### 2. Activity Structure Validation Test
- **Critical for algorithm correctness**
- Ensures DAWN activity has `id=0`
- Ensures DUSK activity has `id=max_num_activities-1`
- This test prevents the common "no feasible solution" error

### 3. Minimal Schedule Test (DAWN -> DUSK)
- Tests the simplest possible schedule
- No intermediate activities
- Validates basic DP functionality

### 4. Simple 3-Activity Schedule
- Tests: DAWN -> Work (with charging) -> DUSK
- Validates charging functionality
- Checks utility calculation

### 5. Multi-Activity Schedule with Charging
- Based on real test data (person_654)
- Tests 11 activities including:
  - Multiple home visits
  - Work with slow charging
  - Shopping with fast charging
  - Various other activities
- Tests DSSR (cycle detection) functionality

### 6. Infeasible Time Window Test
- Tests that algorithm correctly identifies infeasible schedules
- Activity with impossible time constraints
- Should return NULL (no solution)

## Test Output

Successful test run:
```
========================================
    DP SCHEDULING ALGORITHM TESTS
========================================

Testing: Parameter Initialization
  ✓ PASS

Testing: Activity Structure Validation (DAWN id=0, DUSK id=N-1)
  Checking structure:
    First activity (DAWN): id=0 (should be 0)
    Last activity (DUSK): id=4 (should be 4)
  ✓ PASS

...

========================================
           TEST SUMMARY
========================================
Total tests run:    6
Tests passed:       6
Tests failed:       0
Success rate:       100.0%
========================================

All tests passed! ✓
```

## Common Issues

### Infeasibility Errors

If tests fail with "No feasible solution" errors, check:

1. **Activity structure**: DAWN must have `id=0`, DUSK must have `id=N-1`
2. **Time windows**: Ensure activities can fit within horizon (288 intervals)
3. **Battery capacity**: Check if distances are too large for available SOC
4. **Travel times**: Verify activities are reachable within time constraints

### Memory Leaks

Run `make memcheck` to detect memory leaks. Common sources:
- Unreleased labels in bucket
- Group_mem linked lists not freed
- Activity memory not freed

## Adding New Tests

To add a new test:

1. Create a test function following the pattern:
```c
int test_my_new_feature() {
    TEST_START("My New Feature Test");

    // Setup
    setup_default_parameters();

    // Test logic
    ASSERT_TRUE(condition, "Description");
    ASSERT_EQUAL(expected, actual, "Description");

    TEST_PASS();
}
```

2. Add the test to `main()`:
```c
int main() {
    // ... existing tests ...
    test_my_new_feature();

    print_summary();
    return tests_failed > 0 ? 1 : 0;
}
```

## Test Data

Test data files in `../data/test/`:
- `test_activities_person_654_fixed.csv` - Real activity data with correct DAWN/DUSK structure
- `test_activities_person_654_fixed_optimal_schedule.csv` - Expected optimal schedule

## Debugging Tests

To debug a specific test:

```bash
# Compile with debug symbols
gcc -g -I../include -o test_scheduling ../src/scheduling.c ../src/utils.c ../src/main.c test_scheduling.c -lm

# Run with gdb
gdb ./test_scheduling

# Set breakpoint
(gdb) break test_simple_schedule
(gdb) run
```

## CI/CD Integration

To integrate with CI/CD pipelines:

```bash
# Return exit code 0 if all tests pass, 1 if any fail
make test
echo $?  # 0 = success, 1 = failure
```

## Contributing

When adding features to the algorithm:
1. Add corresponding tests to `test_scheduling.c`
2. Run `make test` to ensure no regressions
3. Update this README with new test descriptions
