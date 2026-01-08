/*
 * Test Suite for DP Scheduling Algorithm
 *
 * This file contains integration tests for the scheduling algorithm.
 * Compile with: make test
 * Run with: ./test_scheduling
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include "scheduling.h"
#include "utils.h"

// Test counters
static int tests_run = 0;
static int tests_passed = 0;
static int tests_failed = 0;

// Color codes for terminal output
#define COLOR_GREEN "\x1b[32m"
#define COLOR_RED "\x1b[31m"
#define COLOR_YELLOW "\x1b[33m"
#define COLOR_BLUE "\x1b[34m"
#define COLOR_RESET "\x1b[0m"

// Test assertion macros
#define TEST_START(name) \
    printf("\n" COLOR_BLUE "Testing: %s" COLOR_RESET "\n", name); \
    tests_run++;

#define ASSERT_TRUE(condition, message) \
    if (!(condition)) { \
        printf(COLOR_RED "  ✗ FAIL: %s" COLOR_RESET "\n", message); \
        tests_failed++; \
        return 0; \
    }

#define ASSERT_EQUAL(expected, actual, message) \
    if ((expected) != (actual)) { \
        printf(COLOR_RED "  ✗ FAIL: %s (expected: %d, got: %d)" COLOR_RESET "\n", \
               message, (int)(expected), (int)(actual)); \
        tests_failed++; \
        return 0; \
    }

#define ASSERT_DOUBLE_EQUAL(expected, actual, epsilon, message) \
    if (fabs((expected) - (actual)) > (epsilon)) { \
        printf(COLOR_RED "  ✗ FAIL: %s (expected: %.4f, got: %.4f)" COLOR_RESET "\n", \
               message, (double)(expected), (double)(actual)); \
        tests_failed++; \
        return 0; \
    }

#define ASSERT_NOT_NULL(ptr, message) \
    if ((ptr) == NULL) { \
        printf(COLOR_RED "  ✗ FAIL: %s (pointer is NULL)" COLOR_RESET "\n", message); \
        tests_failed++; \
        return 0; \
    }

#define TEST_PASS() \
    printf(COLOR_GREEN "  ✓ PASS" COLOR_RESET "\n"); \
    tests_passed++; \
    return 1;

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// HELPER FUNCTIONS FOR TESTING ///////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

// Create a simple test activity
Activity create_test_activity(int id, double x, double y, int group,
                              int earliest_start, int latest_start,
                              int min_duration, int max_duration,
                              int des_start_time, int des_duration) {
    Activity a;
    a.id = id;
    a.x = x;
    a.y = y;
    a.group = group;
    a.earliest_start = earliest_start;
    a.latest_start = latest_start;
    a.min_duration = min_duration;
    a.max_duration = max_duration;
    a.des_start_time = des_start_time;
    a.des_duration = des_duration;
    a.charge_mode = 0;
    a.is_charging = 0;
    a.is_service_station = 0;
    a.memory = NULL;
    return a;
}

// Setup default parameters for testing
void setup_default_parameters() {
    double asc[] = {0, 17.4, 16.1, 6.76, 12, 11.3, 10.6, 0, 0};
    double early[] = {0, -2.56, -1.73, -2.55, -0.031, -2.51, -1.37, 0, 0};
    double late[] = {0, -1.54, -3.42, -0.578, -1.58, -0.993, -0.79, 0, 0};
    double longp[] = {0, -0.0783, -0.597, -0.0267, -0.209, -0.133, -0.201, 0, 0};
    double shortp[] = {0, -0.783, -5.63, 0.134, -0.00764, 0.528, -4.78, 0, 0};

    int pyhorizon = 288;
    double pyspeed = 20.4 * 1.60934 * 16.667; // km/h to m/min
    double pytravel_time_penalty = 0.1;
    int pytime_interval = 5;

    set_general_parameters(pyhorizon, pyspeed, pytravel_time_penalty, pytime_interval,
                          asc, early, late, longp, shortp);
}

// Print activity details for debugging
void print_activity(Activity *a) {
    printf("    Activity ID=%d, Group=%d, Pos=(%.0f,%.0f), "
           "Window=[%d,%d], Dur=[%d,%d], DesStart=%d, DesDur=%d, "
           "Charging=%d, Mode=%d\n",
           a->id, a->group, a->x, a->y,
           a->earliest_start, a->latest_start,
           a->min_duration, a->max_duration,
           a->des_start_time, a->des_duration,
           a->is_charging, a->charge_mode);
}

// Print schedule from label
void print_schedule(Label *best) {
    if (best == NULL) {
        printf("    No schedule (NULL)\n");
        return;
    }

    printf("    Final utility: %.2f\n", best->utility);
    printf("    Schedule (in reverse order from label chain):\n");

    Label *current = best;
    int count = 0;
    while (current != NULL && count < 20) {  // Limit to prevent infinite loops
        printf("      [%d] Act=%d, Time=%d, StartTime=%d, Duration=%d, "
               "SOC=%.2f->%.2f, Utility=%.2f\n",
               count, current->act_id, current->time, current->start_time,
               current->duration, current->soc_at_activity_start,
               current->current_soc, current->utility);
        current = current->previous;
        count++;
    }
}

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// INTEGRATION TESTS //////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

// Test 1: Basic parameter initialization
int test_parameter_initialization() {
    TEST_START("Parameter Initialization");

    setup_default_parameters();

    ASSERT_EQUAL(288, horizon, "Horizon should be 288");
    ASSERT_TRUE(speed > 0, "Speed should be positive");
    ASSERT_EQUAL(5, time_interval, "Time interval should be 5");
    ASSERT_TRUE(slow_charge_rate > 0, "Slow charge rate should be initialized");
    ASSERT_TRUE(fast_charge_rate > slow_charge_rate, "Fast > Slow charge rate");
    ASSERT_TRUE(rapid_charge_rate > fast_charge_rate, "Rapid > Fast charge rate");

    TEST_PASS();
}

// Test 2: Simple 3-activity schedule (DAWN -> Activity -> DUSK)
int test_simple_schedule() {
    TEST_START("Simple 3-Activity Schedule (DAWN -> Work -> DUSK)");

    setup_default_parameters();

    // Create activities array
    Activity activities_test[3];

    // DAWN (id=0) - Must start at time 0
    activities_test[0] = create_test_activity(0, 454070, 382249, 0, 0, 0, 1, 286, 0, 0);

    // Work activity (id=1) - with charging
    activities_test[1] = create_test_activity(1, 474270, 381532, 2, 60, 276, 10, 144, 98, 80);
    activities_test[1].charge_mode = 1;
    activities_test[1].is_charging = 1;

    // DUSK (id=2) - Must end at horizon-1
    activities_test[2] = create_test_activity(2, 454070, 382249, 0, 0, 287, 1, 288, 0, 0);

    printf("  Activities:\n");
    for (int i = 0; i < 3; i++) {
        print_activity(&activities_test[i]);
    }

    set_activities(activities_test, 3);

    // Create bucket and run DP
    create_bucket(horizon, max_num_activities);
    DP();

    // Run DSSR loop (same as main.c)
    extern L_list **bucket;
    L_list *li = &bucket[horizon - 1][max_num_activities - 1];
    int dssr_count = 0;
    while (DSSR(find_best(li, 0)) && dssr_count < 10) {
        free_bucket();
        create_bucket(horizon, max_num_activities);
        DP();
        dssr_count++;
        li = &bucket[horizon - 1][max_num_activities - 1];
    }

    // Get result from bucket (not get_final_schedule which needs main() to be called)
    Label *best = find_best(li, 0);

    if (best != NULL) {
        print_schedule(best);
    }

    ASSERT_NOT_NULL(best, "Should find a feasible solution");
    ASSERT_TRUE(best->utility > -INFINITY, "Utility should be finite");
    ASSERT_EQUAL(2, best->act_id, "Final activity should be DUSK (id=2)");

    // Clean up
    free_bucket();

    TEST_PASS();
}

// Test 3: Test with realistic multi-activity schedule
int test_multi_activity_schedule() {
    TEST_START("Multi-Activity Schedule with Charging");

    setup_default_parameters();

    Activity activities_test[11];

    // Based on test_activities_person_654_fixed.csv
    // DAWN
    activities_test[0] = create_test_activity(0, 454070, 382249, 0, 0, 0, 1, 286, 0, 0);

    // Home activities
    activities_test[1] = create_test_activity(1, 454070, 382249, 0, 0, 288, 2, 288, 0, 0);
    activities_test[5] = create_test_activity(5, 454070, 382249, 0, 0, 288, 2, 288, 0, 138);
    activities_test[9] = create_test_activity(9, 454070, 382249, 0, 0, 288, 2, 288, 0, 0);

    // Other activities
    activities_test[2] = create_test_activity(2, 452811, 385797, 8, 108, 216, 2, 120, 0, 0);
    activities_test[3] = create_test_activity(3, 452551, 385259, 4, 84, 276, 2, 120, 0, 0);
    activities_test[4] = create_test_activity(4, 452211, 383737, 8, 108, 216, 2, 120, 0, 0);
    activities_test[6] = create_test_activity(6, 456492, 382027, 8, 108, 216, 2, 120, 0, 0);

    // Work with charging
    activities_test[7] = create_test_activity(7, 474270, 381532, 2, 60, 276, 10, 144, 98, 80);
    activities_test[7].charge_mode = 1;
    activities_test[7].is_charging = 1;

    // Shop with fast charging
    activities_test[8] = create_test_activity(8, 467941, 378919, 4, 84, 276, 2, 120, 200, 15);
    activities_test[8].charge_mode = 2;
    activities_test[8].is_charging = 1;

    // DUSK
    activities_test[10] = create_test_activity(10, 454070, 382249, 0, 0, 287, 1, 288, 0, 0);

    printf("  Setting up %d activities\n", 11);
    set_activities(activities_test, 11);

    // Run DP
    create_bucket(horizon, max_num_activities);
    DP();

    // Check for cycles and re-run if needed
    Label *best = get_final_schedule();
    int dssr_count = 0;

    while (best != NULL && DSSR(best) && dssr_count < 10) {
        printf("  DSSR detected cycle, re-running (iteration %d)\n", dssr_count + 1);
        free_bucket();
        create_bucket(horizon, max_num_activities);
        DP();
        dssr_count++;
        best = get_final_schedule();
    }

    if (best != NULL) {
        printf("  → Final utility: %.2f\n", best->utility);
        printf("  → DSSR iterations: %d\n", dssr_count);
    }

    ASSERT_NOT_NULL(best, "Should find a feasible solution for multi-activity schedule");
    ASSERT_TRUE(best->utility > -INFINITY, "Utility should be finite");
    ASSERT_EQUAL(10, best->act_id, "Final activity should be DUSK (id=10)");

    // Clean up
    free_bucket();

    TEST_PASS();
}

// Test 4: Infeasible scenario - impossible time windows
int test_infeasible_time_window() {
    TEST_START("Infeasible Scenario - Impossible Time Windows");

    setup_default_parameters();

    Activity activities_test[3];

    // DAWN (id=0)
    activities_test[0] = create_test_activity(0, 454070, 382249, 0, 0, 0, 1, 286, 0, 0);

    // Activity with impossible time window (too late in day with long min duration)
    activities_test[1] = create_test_activity(1, 474270, 381532, 2, 280, 281, 50, 144, 280, 50);

    // DUSK (id=2)
    activities_test[2] = create_test_activity(2, 454070, 382249, 0, 0, 287, 1, 288, 0, 0);

    set_activities(activities_test, 3);

    create_bucket(horizon, max_num_activities);
    DP();

    extern L_list **bucket;
    L_list *li = &bucket[horizon - 1][max_num_activities - 1];
    Label *best = find_best(li, 0);

    printf("  Result: %s\n", best == NULL ? "NULL (infeasible)" : "Feasible (unexpected)");
    if (best != NULL) {
        printf("  Utility: %.2f\n", best->utility);
    }

    // Should be infeasible
    ASSERT_TRUE(best == NULL, "Should be infeasible due to time window constraints");

    free_bucket();

    TEST_PASS();
}

// Test 5: Test with home-only schedule (minimal case)
int test_home_only_schedule() {
    TEST_START("Minimal Schedule (DAWN -> DUSK only)");

    setup_default_parameters();

    Activity activities_test[2];

    // DAWN (id=0)
    activities_test[0] = create_test_activity(0, 454070, 382249, 0, 0, 0, 1, 286, 0, 0);

    // DUSK (id=1) - immediately after DAWN
    activities_test[1] = create_test_activity(1, 454070, 382249, 0, 0, 287, 1, 288, 0, 0);

    set_activities(activities_test, 2);

    create_bucket(horizon, max_num_activities);
    DP();

    extern L_list **bucket;
    L_list *li = &bucket[horizon - 1][max_num_activities - 1];
    Label *best = find_best(li, 0);

    if (best != NULL) {
        print_schedule(best);
    }

    ASSERT_NOT_NULL(best, "Should find feasible solution for home-only schedule");
    ASSERT_EQUAL(1, best->act_id, "Final activity should be DUSK (id=1)");

    free_bucket();

    TEST_PASS();
}

// Test 6: Test correct structure (DAWN has id=0, DUSK has id=N-1)
int test_correct_activity_structure() {
    TEST_START("Activity Structure Validation (DAWN id=0, DUSK id=N-1)");

    setup_default_parameters();

    Activity activities_test[5];

    // DAWN - MUST have id=0
    activities_test[0] = create_test_activity(0, 454070, 382249, 0, 0, 0, 1, 286, 0, 0);

    // Regular activities
    activities_test[1] = create_test_activity(1, 474270, 381532, 2, 60, 276, 10, 144, 98, 80);
    activities_test[2] = create_test_activity(2, 467941, 378919, 4, 84, 276, 2, 120, 200, 15);
    activities_test[3] = create_test_activity(3, 454070, 382249, 0, 0, 288, 2, 288, 0, 0);

    // DUSK - MUST have id=max_num_activities-1 (which is 4 in this case)
    activities_test[4] = create_test_activity(4, 454070, 382249, 0, 0, 287, 1, 288, 0, 0);

    printf("  Checking structure:\n");
    printf("    First activity (DAWN): id=%d (should be 0)\n", activities_test[0].id);
    printf("    Last activity (DUSK): id=%d (should be 4)\n", activities_test[4].id);

    ASSERT_EQUAL(0, activities_test[0].id, "DAWN must have id=0");
    ASSERT_EQUAL(4, activities_test[4].id, "DUSK must have id=N-1 (4)");

    set_activities(activities_test, 5);

    create_bucket(horizon, max_num_activities);
    DP();

    extern L_list **bucket;
    L_list *li = &bucket[horizon - 1][max_num_activities - 1];
    Label *best = find_best(li, 0);

    ASSERT_NOT_NULL(best, "Should find feasible solution with correct structure");
    ASSERT_EQUAL(4, best->act_id, "Final activity should be DUSK (id=4)");

    free_bucket();

    TEST_PASS();
}

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// TEST RUNNER ////////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

void print_summary() {
    printf("\n");
    printf("========================================\n");
    printf("           TEST SUMMARY\n");
    printf("========================================\n");
    printf("Total tests run:    %d\n", tests_run);
    printf(COLOR_GREEN "Tests passed:       %d" COLOR_RESET "\n", tests_passed);
    if (tests_failed > 0) {
        printf(COLOR_RED "Tests failed:       %d" COLOR_RESET "\n", tests_failed);
    } else {
        printf("Tests failed:       %d\n", tests_failed);
    }
    printf("Success rate:       %.1f%%\n",
           tests_run > 0 ? (100.0 * tests_passed / tests_run) : 0.0);
    printf("========================================\n");

    if (tests_failed == 0) {
        printf(COLOR_GREEN "\nAll tests passed! ✓\n" COLOR_RESET "\n");
    } else {
        printf(COLOR_RED "\nSome tests failed! ✗\n" COLOR_RESET "\n");
    }
}

int main() {
    printf("\n");
    printf("========================================\n");
    printf("    DP SCHEDULING ALGORITHM TESTS\n");
    printf("========================================\n");

    // Run tests
    test_parameter_initialization();
    test_correct_activity_structure();
    test_home_only_schedule();
    test_simple_schedule();
    test_multi_activity_schedule();
    test_infeasible_time_window();

    // Print summary
    print_summary();

    return tests_failed > 0 ? 1 : 0;
}
