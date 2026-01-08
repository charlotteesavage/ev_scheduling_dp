/*
 * Test Interface for DP Scheduling Algorithm
 *
 * This header exposes internal functions for testing purposes.
 * Include this ONLY in test files, not in production code.
 */

#ifndef SCHEDULING_TEST_H
#define SCHEDULING_TEST_H

#include "scheduling.h"

// Expose internal helper functions for unit testing
// These are declared as static in scheduling.c, so we need to remove static for testing
// OR we can declare wrapper functions

// Helper functions (need to be made non-static in scheduling.c for testing)
double distance_x(Activity *a1, Activity *a2);
int travel_time(Activity *a1, Activity *a2);
double energy_consumed_soc(Activity *a1, Activity *a2);
void get_charge_rate_and_price(Activity *a, double result[2]);
double get_tou_factor(int time);

// Label functions
Label *find_best(L_list *B, int o);
int is_feasible(Label *L, Activity *a);
double update_utility(Label *L);

#endif // SCHEDULING_TEST_H
