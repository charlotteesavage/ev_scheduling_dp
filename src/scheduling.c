/*  Algorythm developped by Fabian Torres & Pierre Hellich
    Semester project Fall 2023

    Given an EV driver's activity preferences and charging constraints,
    what activities should they do, when, and where should they charge?

    */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <stdbool.h>
#include "scheduling.h"
#include "utils.h"

/// global _constants
int time_interval;
double speed;
double travel_time_penalty;
int horizon;
// int num_activities;
int max_num_activities; // max no of activities, to cap search
L_list **bucket;
Activity *activities = NULL;
int DSSR_count;
double total_time;
Label *final_schedule;

// New terms //
double battery_capacity = 60; // capacity in kwh
double soc_full = 1.0;
double soc_threshold = 0.3;           // in %, minimum for comfort
double energy_consumption_rate = 0.2; // kwh_per_km

double initial_soc = 1.0; // this is pulled from a uniform distribution
// uniform dist between 0.3 and 1, with option to go below 0.3 for small %

double slow_charge_power = 7.0;
double fast_charge_power = 22.0;
double rapid_charge_power = 50.0;

// fraction of battery charged PER TIME INTERVAL
double slow_charge_rate;  // for power 7 kW
double fast_charge_rate;  // for power 22 kW
double rapid_charge_rate; // for power 50 kW

// tariffs
double home_off_peak_price = 0.07;    // GBP/kWh, https://www.moneysavingexpert.com/utilities/ev-energy-tariffs/
double home_slow_charge_price = 0.26; // GBP/kWh, for a battery of 60 kWh
double AC_charge_price = 0.52;        // GBP/kWh
double public_dc_charge_price = 0.79; // GBP/kWh
double free_charging = 0;             // when charging is free
// https://www.which.co.uk/reviews/new-and-used-cars/article/electric-car-charging-guide/how-much-does-it-cost-to-charge-an-electric-car-a8f4g1o7JzXj

double tou_peak_factor = 1.5;
double tou_midpeak_factor = 2.5;
double tou_offpeak_factor = 1;

int peak_start = 12;     // 12:00
int peak_end = 18;       // 18:00
int midpeak1_start = 8;  // 8:00
int midpeak1_end = 12;   // 12:00
int midpeak2_start = 18; // 18:00
int midpeak2_end = 21;   // 21:00

/// utility_parameters
double asc_parameters[5];
double early_parameters[5];
double late_parameters[5];
double long_parameters[5];
double short_parameters[5];

// // flex params, if needed
// int flex, mid_flex, not_flex;

// new utility terms
double gamma_charge_work = -3.59;     // inconvenience of charging at work activity
double gamma_charge_non_work = -4.34; // inconvenience of charging at non-work activity
double gamma_charge_home = -3.34;     // inconvenience of charging at home
double theta_soc = -80;               // low battery SOC penalty param
double beta_delta_soc = 25;           // charged battery SOC penalty param
double beta_charge_cost = -0.3;       // param for charging costs per mode

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// INITIALISATION /////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

int get_count() { return DSSR_count; }
double get_total_time() { return total_time; }
Label *get_final_schedule() { return final_schedule; }

void initialize_charge_rates(void) // initialise these rates per eqn (39) in paper
// fraction of battery increase PER TIME INTERVAL in hours
{
    double fraction_of_hours_per_interval = time_interval / 60;                                 // 5 min = 0.0833 hours
    slow_charge_rate = (slow_charge_power / battery_capacity) * fraction_of_hours_per_interval; // fraction of battery charged per time_interval
    fast_charge_rate = (fast_charge_power / battery_capacity) * fraction_of_hours_per_interval;
    rapid_charge_rate = (rapid_charge_power / battery_capacity) * fraction_of_hours_per_interval;
}

// void initialise_charge_prices(void) // put charge prices into GBP per time interval
// {
// }

void set_general_parameters(int pyhorizon, double pyspeed, double pytravel_time_penalty, int pytime_interval,
                            double *asc, double *early, double *late, double *longp, double *shortp
                            // int pyflexible, int pymid_flex, int pynot_flex
)
{
    speed = pyspeed;
    travel_time_penalty = pytravel_time_penalty;
    horizon = pyhorizon;
    time_interval = pytime_interval;
    // flex = pyflexible;
    // mid_flex = pymid_flex;
    // not_flex = pynot_flex;
    initialize_charge_rates();

    // printf("speed = %f, travel_time_penalty = %f, curfew_time = %d, max_outside_time = %d, max_travel_time = %d, peak_hour_time1 = %d, peak_hour_time2 = %d, time_interval = %d\n",
    //         speed, travel_time_penalty, curfew_time, max_outside_time, max_travel_time, peak_hour_time1,
    //         peak_hour_time2, horizon, time_interval);

    for (int i = 0; i < 5; i++)
    {
        asc_parameters[i] = asc[i];
        early_parameters[i] = early[i];
        late_parameters[i] = late[i];
        long_parameters[i] = longp[i];
        short_parameters[i] = shortp[i];
        // printf("asc[%d] = %f, early[%d] = %f, late[%d] = %f, longp[%d] = %f, shortp[%d] = %f\n",
        //        i, asc_parameters[i], i, early_parameters[i], i, late_parameters[i],
        //        i, long_parameters[i], i, short_parameters[i]);
    }
};

void set_activities(Activity *activities_data, int pynum_activities)
{
    activities = activities_data;
    num_activities = pynum_activities;
}

/* Allocates memory for and initializes a new Label with the specified Activity */
static Label *create_label(Activity *aa)
{
    Label *L = malloc(sizeof(Label));
    L->act_id = 0;
    L->time = aa->min_duration; // double check this, make sure it is in minutes
    L->start_time = 0;
    L->utility = 0;
    L->deviation_start = 0;
    L->deviation_dur = 0;
    L->duration = aa->min_duration;
    L->previous = NULL;
    L->act = aa;
    L->mem = malloc(sizeof(Group_mem));
    L->mem->g = 0;
    L->mem->next = NULL;
    L->mem->previous = NULL;

    L->soc_at_activity_start = initial_soc; // battery state of charge at the start of activity 𝑎
    L->current_soc = initial_soc;
    L->charge_duration = 0;
    L->delta_soc = 0; // clarify what is meant by this cf (10)
    L->charge_cost = 0;

    // L->delta_soc_during_interval = 0; // SOC increase during a single time interval, if occurring
    // L->total_delta_soc = 0; // total SOC increase over charging period so far
    // L->soc = initial_soc; // check this - might be parsed from individual data

    return L;
};

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// HELPER FUNCTIONS /////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

static double distance_x(Activity *a1, Activity *a2) // this will change as we will just reference a skim matrix
{
    // Distance in metres
    double dx = (double)(a2->x - a1->x);
    double dy = (double)(a2->y - a1->y);
    double dist = sqrt(dx * dx + dy * dy);
    return dist;
};

static int travel_time(Activity *a1, Activity *a2)
{
    double dist = distance_x(a1, a2); // this will change too
    int time = (int)(dist / speed);
    time = (int)(ceil((double)time / time_interval) * time_interval); // Round down to the nearest 5-minute interval
    int travel_time_horizon = time / time_interval;                   // Divide by 5 to fit within the 0-289 time horizon
    return travel_time_horizon;
};

static double energy_consumed_soc(Activity *a1, Activity *a2) // energy consumed in SOC going from one activity to another
{
    double distance_km = distance_x(a1, a2) / 1000;
    double energy_kWh = energy_consumption_rate * distance_km;

    // convert to a percent of the battery capacity (ie represent in SOC)
    double soc_consumed = energy_kWh / battery_capacity;

    return soc_consumed;
};

static void get_charge_rate_and_price(Activity *a, double result[2])
// need to make sure label charge modes are parsed to ints per .h file for this to work
// all charge rates are in % terms of battery capacity, not absolute terms
{
    double charge_rate = 0.0;
    double charge_price = 0.0;

    switch (a->charge_mode)
    {
    case 1: // not charging
        charge_rate = 0;
        charge_price = 0;
        break;

    case 2:
        charge_rate = slow_charge_rate;
        if (a->activity_type == 0)
        {
            charge_price = home_slow_charge_price;
        }
        if (a->activity_type != 0)
        {
            charge_price = AC_charge_price;
        }
        break;

    case 3:
        charge_rate = fast_charge_rate;
        charge_price = AC_charge_price;
        break;

    case 4:
        charge_rate = rapid_charge_rate;
        charge_price = public_dc_charge_price;
        break;
    }
    result[0] = charge_rate;
    result[1] = charge_price;
}

static double get_tou_factor(int time)
{
    int hour = (time * time_interval) / 60; // gives the time in 24hour standard

    if (hour >= peak_start && hour < peak_end)
    {
        return tou_peak_factor;
    }
    else if ((hour >= midpeak1_start && hour < midpeak1_end) || (hour >= midpeak2_start && hour < midpeak2_end))
    {
        return tou_midpeak_factor;
    }
    else
    {
        return tou_offpeak_factor;
    }
}

//////////////////////////////////////////////////////////////////////////////////////////////////////////
/////////////////////// BIG FUNCTIONS ////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

/*  Determines if an Activity a can be added to a sequence ending in label L.
    It returns 1 if it's feasible and 0 if it's not.
    Note - a is the considered activity, but does not yet have fixed duration
    or charging participation.
    As such, most constraints apply to the considered activity, a, except for
    duration and charging-based constraints which must be applied to L
    */

static int is_feasible(Label *L, Activity *a)
{
    // need to check charging stuff here
    // charge time - for new function for activity transition,

    // update_SOC to cap the charging time min(act_duration, time to full)
    // need to check the SOC can't go negative

    if (L == NULL)
    { // if no Label, 'a' cannot be added
        return 0;
    }
    if (L->act_id != 0 && a->id == 0)
    { // exclude dawn if it's not the 1st activity of the label
        return 0;
    }

    // CASE 1: Continuing at SAME activity
    if (L->act_id == a->id)
    { // If the current activity in L is the same as a, check the duration
        if (L->duration + 1 > a->max_duration)
        { // max duration
            return 0;
        }
        // if it is the same activity, you need the same charging state as the previous one
        if (a->is_charging)
        {
            // constraint 35
            if (a->charge_mode == 0)
            {
                return 0;
            }

            if (L->previous != NULL && L->act->charge_mode != a->charge_mode)
            {
                return 0;
            } // check continuity of charge mode, only from second activity

            // check constraint 26:
            double results[2];
            get_charge_rate_and_price(a, results);
            double charge_rate = results[0]; // this is the change in SOC for the time interval
            // double charge_price = results[1];

            // double delta_soc = charge_rate * time_interval / 60;

            if (L->current_soc + charge_rate > soc_full)
            {
                return 0;
            } // constraint 26

            // if (L->charge_duration <= 0)
            // {
            //     return 0;
            // } //constraint 31, do NOT need this here because we only check on activity a
        }

        if (a->is_service_station)
        {
            if (!a->is_charging)
            {
                return 0;
            }
        }
        return 1;
    }

    // Case 2: different activity from before

    if (L->act_id != a->id)
    // when act changes, constraints are checked AND utility gets updated
    { // If act of L isn't the same as a, do some checks
        if (L->previous != NULL && L->previous->act_id == a->id)
        { // is the previous activity the same as a ? pas sur de l'interet
            return 0;
        }
        if (L->act_id == num_activities - 1)
        { // Ensuring the current activity isn't the last one
            return 0;
        }
        if (L->duration < L->act->min_duration)
        { // Verifying the minimum duration of the current activity
            return 0;
        }

        int tt = travel_time(L->act, a);
        // current time + travel_time for a + min duration for a + time for returning home > end of horizon
        // Ie enough time left in the horizon to add this activity
        if (L->time + tt + a->min_duration +
                travel_time(a, &activities[num_activities - 1]) >=
            horizon - 1)
        {
            return 0;
        }
        // Making sure the new activity starts and ends within its allowed time window : signes changed !
        if (L->time + tt < a->earliest_start)
        {
            return 0;
        }
        // if current time + travel time to next activity is less than the latest possible start time of the next activity,
        // not allowed
        if (L->time + tt > a->latest_start)
        {
            return 0;
        }
        // if we have already done this particular activity
        if (mem_contains(L, a))
        {
            // printf("\n mem_contains = %d", mem_contains(L,a));
            return 0;
        }

        // SOC constraint: must be non-negative after travel
        double soc_after_travel = L->current_soc - energy_consumed_soc(L->act, a);
        if (soc_after_travel < 0)
        {
            return 0;
        }

        // constraint 35
        if (a->is_charging && a->charge_mode == 0)
        {
            return 0;
        }
        // if (a->is_charging)
        // {
        //     double results[2];
        //     get_charge_rate_and_price(a, results);
        //     double charge_rate = results[0];
        //     // double charge_price = results[1];

        //     // double delta_soc = charge_rate * time_interval / 60;

        //     if (soc_after_travel + charge_rate > soc_full)
        //     {
        //         return 0;
        //     } // constraint 26, but makes sure we don't pick an overzealous charge mode
        // }

        if (a->is_service_station)

        {
            if (!a->is_charging) // constraint 33
            {
                return 0;
            }
        }
        return 1;
    }
};

// function to fix all the variables
// and check the constraints at the end of an activity

/*  checks if Label L1 dominates Label L2 based on certain criteria.
    This aims to maximise the utility function

    Without pruning, the number of labels explodes exponentially
    Dominance identifies labels that are strictly worse than others and can be safely discarded
    This is the key to computational efficiency

    0 = no dominance
    1 = L1 dominates by default because L2 is NULL
    2 = L1 dominates L2 based on the criteria */

static int dominates(Label *L1, Label *L2)
{
    if (L1 == NULL)
    {
        return 0;
    }
    if (L2 == NULL)
    {
        return 1;
    }
    if (L1->act_id != L2->act_id)
    {
        return 0;
    }

    if (L1->utility >= L2->utility)
    { // L1 a une meilleure utility que L2

        // if(L1->time <= L2->time){
        //     return 2;
        // }

        /*  S'assure que tous les group de L2 sont dans L1, sinon ca veut dire que L2 peut etre moins bien pcq elle nn'a pas encore fait un group.
            Au contraire si L1 est meilleur alors que il n'a meme pas fait tous les groupes de L2, ca veut dire que son choice set est tjrs plus grand */
        if (dom_mem_contains(L2, L1))
        { // be sure of order

            // Exact method v2
            if (L1->time <= L2->time)
            {
                return 2;
            }

            // // Exact method v1
            // if(L1->duration == L2->duration){return 2;}
            // // if(L1->utility - duration_Ut[L1->acity][L1->duration] <= L2->utility - duration_Ut[L2->acity][L2->duration]){
            // int group = L1->act->group;
            // int des_dur = L1->act->des_duration;
            // if(
            //     L1->utility
            //     + short_parameters[group] * time_interval * fmax(0, des_dur - L1->duration - 2)
            //     + long_parameters[group] * time_interval * fmax(0, L1->duration - des_dur - 2)
            //     <=
            //     L2->utility
            //     + short_parameters[group] * time_interval * fmax(0, des_dur - L2->duration - 2)
            //     + long_parameters[group] * time_interval * fmax(0, L2->duration - des_dur - 2) ){
            //     return 2;
            // }
        }
    }
    return 0;
};

/* Calculate the utility of a label based on its starting activity and the duration of the one that just finished */
static double update_utility(Label *L)
// make sure to check that you start a new activity in label_update before this is calculated
// function on the basis of minutes
// time horizons differences are multiplied to be expressed in minutes from the parameters

//     group_to_type = {
//     0: "Home",
//     1: "Education",
//     2: "Errands",
//     3: "Escort",
//     4: "Leisure",
//     5: "Shopping",
//     6: "Work",
//     7: "ServiceStation",
// }

// check if charging constrants can be used to deal with service stations instead
// time window constraints between 7am and 11pm, not allowed outside that
{
    // should have an error term, but don't need a cost penalty
    // cost of travel is associated with EV cost only, don't account for EV tax, parking etc
    // to be listed as assumptions at beginning of paper

    int activity_type = L->act->activity_type;
    Activity *act = L->act;

    Label *previous_L = L->previous;
    Activity *previous_act = previous_L->act;
    int previous_activity_type = previous_act->activity_type;

    L->utility = previous_L->utility;

    L->utility += asc_parameters[activity_type];
    L->utility += travel_time_penalty * travel_time(previous_act, act);

    // service station has no duration penalties - its only penalties come from cost of charge
    // so only apply these below to non-home and non service station activities

    // PENALTY FOR FINISHING PREVIOUS ACTIVITY (duration deviation)
    if (previous_activity_type != 0 && !previous_act->is_service_station)
    {
        L->utility += short_parameters[previous_activity_type] * time_interval *
                      fmax(0, previous_act->des_duration - previous_L->duration);
        L->utility += long_parameters[previous_activity_type] * time_interval *
                      fmax(0, previous_L->duration - previous_act->des_duration);
    }

    // PENALTY FOR STARTING NEW ACTIVITY (timing deviation)
    if (activity_type != 0 && !act->is_service_station)
    {
        L->utility += early_parameters[activity_type] * time_interval *
                      fmax(0, act->des_start_time - L->start_time);
        L->utility += late_parameters[activity_type] * time_interval *
                      fmax(0, L->start_time - act->des_start_time);
    }

    // calculate the utility change from charging at finished activity
    if (previous_act->is_charging)
    {
        if (previous_activity_type == 6)
        {
            L->utility += gamma_charge_work;
        }
        else if (previous_activity_type == 0)
        {
            L->utility += gamma_charge_home;
        }
        else
        {
            L->utility += gamma_charge_non_work;
        }

        L->utility += theta_soc * fmax(0, soc_threshold - previous_L->soc_at_activity_start);
        double total_delta_soc = previous_L->current_soc - previous_L->soc_at_activity_start;
        L->utility += beta_delta_soc * total_delta_soc;
        if (previous_L->previous != NULL) // if the previous act is not empty (ie it is after dawn), need to calc the charge cost
        {
            double interval_charge_cost = previous_L->charge_cost - previous_L->previous->charge_cost;
            L->utility += beta_charge_cost * interval_charge_cost;
        }
        else
        {
            L->utility += beta_charge_cost * previous_L->charge_cost;
        }
    }

    return L->utility;
};

// only go to update utility if it is a new act
// SOC is updated in update_label
// if charging, calculate the remaining uncharged amount, working in 5 min interval
// calc remaining spare capacity, if it is >0 then charge until full
// max amount of charge possible in that interval

// /*  Generates a new label L based on an existing label current_label and an activity a */
static Label *update_label_from_activity(Label *current_label, Activity *a)
// This function updates labels by one time interval (5 mins)
//   1. Check if new activity first
//   2. If new: transition to new activity (update utility, advance timestamp, reduce SOC)
//   3. If not new: do simple time update
//   4. Update charging regardless of new or not
{
    Label *new_label = malloc(sizeof(Label));
    new_label->previous = current_label;
    new_label->act = a;
    new_label->act_id = a->id;
    new_label->deviation_start = current_label->deviation_start;
    new_label->deviation_dur = current_label->deviation_dur;

    // STEP 1: Check if new activity
    if (a->id != current_label->act_id)
    {
        // Transition to new activity:
        // - Calculate start time (current time + travel time)
        // - Update memory
        // - Reduce SOC for travel
        // - Initialize time and duration

        new_label->start_time = current_label->time + travel_time(current_label->act, a);
        new_label->mem = unionLinkedLists(current_label->mem, a->memory, a->activity_type);

        // do we want the below to be by interval, or do it across min_duration???
        if (a->id == num_activities - 1)
        {                                                              // d'ou le saut chelou a la fin : DUSK (pas de utility pour dusk)
            new_label->duration = horizon - new_label->start_time - 1; // set to 0 before
            new_label->time = horizon - 1;                             // pq pas le temps actuel (pour uen 3e var de starting time)
        }
        else
        {
            new_label->duration = a->min_duration;
            new_label->time = new_label->start_time + new_label->duration;
        }

        // Reduce SOC by travel consumption
        double soc_consumed = energy_consumed_soc(current_label->act, a);
        new_label->soc_at_activity_start = current_label->current_soc - soc_consumed;
        new_label->current_soc = new_label->soc_at_activity_start; // initialise the soc in case of charging

        // Initialize charging variables for new activity
        new_label->charge_duration = 0;
        new_label->delta_soc = 0;
        new_label->charge_cost = current_label->charge_cost; // inherit cumulative charging cost

        // STEP 1b: Calculate charging for first interval of new activity (if charging)
        //  This must happen BEFORE calling update_utility so that utility calculation has access to charging data
        //  need to adapt this to deal with the situation where
        //  charging will be completed BEFORE activity is over
        //  - so there will be time spent idle at the charger
        if (a->is_charging)
        {
            double results[2];
            get_charge_rate_and_price(a, results);
            double charge_rate = results[0];
            double charge_price = results[1];
            // double max_possible_charge = charge_rate * (time_interval / 60.0);
            new_label->delta_soc = fmin(soc_full - new_label->current_soc, charge_rate);
            new_label->current_soc += new_label->delta_soc;
            new_label->charge_duration = time_interval;

            // Calculate charging cost for this first interval
            double tou_factor = get_tou_factor(new_label->start_time);
            double energy_charged_kwh = new_label->delta_soc * battery_capacity;
            double interval_cost = charge_price * tou_factor * energy_charged_kwh;
            new_label->charge_cost += interval_cost;
        }

        // Update utility ONLY when moving to new activity
        new_label->utility = update_utility(new_label);

        // Calculate deviation penalties for activity transitions
        // **SERVICE STATION HANDLING**: No deviation penalties
        if (!a->is_service_station && a->activity_type != 0)
        {
            new_label->deviation_start += abs(new_label->start_time - a->des_start_time);
        }

        if (!current_label->act->is_service_station && current_label->act->activity_type != 0)
        {
            new_label->deviation_dur += abs(current_label->duration - current_label->act->des_duration);
        }
    }
    else // SAME ACTIVITY - simple time update
    {
        // Continue at same activity - just advance by one time interval
        new_label->start_time = current_label->start_time;
        new_label->time = current_label->time + time_interval; // advance by 1 time interval
        new_label->duration = current_label->duration + time_interval;
        new_label->mem = copyLinkedList(current_label->mem);

        // Inherit SOC and charging cost (will be updated below if charging)
        // no decrease in SOC possible because no travel involved
        new_label->current_soc = current_label->current_soc;
        new_label->soc_at_activity_start = current_label->soc_at_activity_start;
        new_label->charge_duration = current_label->charge_duration;
        new_label->delta_soc = 0;
        new_label->charge_cost = current_label->charge_cost;

        // Utility stays the same - no update for continuing activity
        new_label->utility = current_label->utility;

        // STEP 2: Update charging for continuing activity
        // Only update SOC and costs here - NO utility changes
        if (a->is_charging && new_label->current_soc < soc_full)
        {
            new_label->charge_duration += time_interval;

            double results[2];
            get_charge_rate_and_price(a, results);
            double charge_rate = results[0];
            double charge_price = results[1];

            // Calculate how much we can charge in this interval
            // Limited by remaining battery capacity
            // double max_possible_charge = charge_rate * (time_interval / 60.0);
            new_label->delta_soc = fmin(soc_full - new_label->current_soc, charge_rate);
            new_label->current_soc += new_label->delta_soc;

            // Calculate charging cost for this interval
            double tou_factor = get_tou_factor(new_label->time);
            double energy_charged_kwh = new_label->delta_soc * battery_capacity;
            double interval_cost = charge_price * tou_factor * energy_charged_kwh;
            new_label->charge_cost += interval_cost;

            // All utility changes happen only for new activities
        }
    }

    return new_label;
}

/*  Finds the label with the max utility value from the list.
    Returns the label with the max utility value. */
Label *find_best(L_list *B, int o)
{
    double max = -INFINITY;
    Label *bestL = NULL;
    L_list *li = B;
    while (li != NULL)
    {
        // printf("%s", "\n Hello there");
        if (li->element != NULL)
        {
            if (li->element->utility > max)
            {
                bestL = li->element;
                max = bestL->utility;
            }
        }
        li = li->next;
    }
    if (bestL == NULL)
    {
        printf("%s", "\n Solution is not feasible, check activities parameters.");
    }
    else
    {
        if (o)
        {
            printf("\n Best solution value = %.2f \n", bestL->utility);
            // printf("%s ", "[");
            recursive_print(bestL);
            // printf("%s \n", "]");
        }
    }
    return bestL;
};

/*  To detect cycles based on the group of activities within a sequence of labels and,
    if a cycle is detected, update the memory of some labels in the sequence
    "this combination has been done before" */
int DSSR(Label *L)
{
    // printf("\n DSSR");
    Label *p1 = L;
    int cycle = 0;
    int c_activity = 0;
    int group_activity = 0;

    while (p1 != NULL && cycle == 0)
    { // iterates through the labels starting from L in the reverse direction until it reaches the beginning
        while (p1 != NULL && (p1->act_id == num_activities - 1 || p1->act_id == num_activities - 2))
        { // skips labels that correspond to the last activity // group == 0 ?
            p1 = p1->previous;
        }
        Label *p2 = p1;
        while (p2 != NULL && p2->act_id == p1->act_id)
        { //  skips labels that have the same activity as p.
            p2 = p2->previous;
        }
        while (p2 != NULL && cycle == 0)
        { //  checks for a cycle by looking for a previous label with the same group as p. If found, records the activity and group,
            if (p2->act->activity_type == p1->act->activity_type)
            {
                cycle = 1;
                c_activity = p1->act_id;
                group_activity = p1->act->activity_type;
            }
            p2 = p2->previous;
        }
        p1 = p1->previous;
    }
    if (cycle)
    { // ou est ce que c'est utilise de toute facon ? et pq ca marche pas pour chaque label au fur et a mesure ?
        Label *p3 = p1;
        while (p3 != NULL && p3->act_id == c_activity)
        {
            p3 = p3->previous;
        }
        // printf("acity >> %d \n", c_activity);
        while (p3 != NULL && p3->act_id != c_activity)
        {
            // printf("intermedaire >> %d \n", p3->acity);
            add_memory(p3->act_id, group_activity); // add une activite dans une liste qu'on ira checker si on va rajouetr le meme grouep ?
            p3 = p3->previous;
        }
    }
    return cycle;
};

/* Dynamic Programming */
void DP()
{
    // printf("\n Dynamic Programming");
    if (bucket == NULL)
    {
        printf(" BUCKET IS NULL %d", 0);
    }

    Label *ll = create_label(&activities[0]); // Initialise label with Dawn as first activity
    bucket[ll->time][0].element = ll;         // store this label in the first position bucket structure

    for (int h = ll->time; h < horizon - 1; h++) // for all time intervals from 0 to 288 (horizon = 289, the number of 5 min intervals in a day)
    {
        for (int act_index = 0; act_index < num_activities; act_index++) // for each activity in num_activities
        {
            L_list *list = &bucket[h][act_index]; // create a linked list node,
            // get all labels at state (h, act_index)
            // create a linked list from the bucket entry at [h][act_index]

            while (list != NULL) // for each label in the cell i.e. in the linked list
            {
                // int myBool = list!=NULL;
                // printf("myBool: %s\n", myBool ? "true" : "false");

                Label *L = list->element; // for the current label in the l_list

                for (int a1 = 0; a1 < num_activities; a1++)
                { // for all the activities

                    if (is_feasible(L, &activities[a1]))
                    { // if activity is not feasible, pass directly to the next activity

                        Label *L1 = update_label_from_activity(L, &activities[a1]); // what would the label look like after this activity?

                        // But : garder le minimum de L_list pour le temps au nouveau label et l'activite a1
                        // aim: keen the minimum value from L_list
                        // checks the min value in the linked list and retains that one
                        int dom = 0;
                        L_list *list_1 = &bucket[L1->time][a1];
                        L_list *list_2 = &bucket[L1->time][a1];

                        while (list_1 != NULL)
                        {

                            list_2 = list_1;

                            // If a label in the bucket is dominated by L1, this label is removed from the list (bucket)
                            if (dominates(L1, list_1->element))
                            {
                                // printf("\n Dominance \n");
                                list_1 = remove_label(list_1);
                            }
                            // If L1 is dominated by a label in the bucket, no further comparason is needed for L1 and it's discarded
                            else
                            {
                                if (dominates(list_1->element, L1))
                                {
                                    // printf("\n Dominance \n");
                                    free(L1);
                                    dom = 1; // (dom = 1) => bucket domine L1
                                    break;   // exit the while
                                }
                                list_1 = list_1->next; // pour evaluer la prochaine L_list
                            };
                        }

                        // si L1 n'est domine par aucun des label de la list du bucket, il faut l'y rajouter
                        if (!dom)
                        {
                            if (list_2->element == NULL)
                            {
                                list_2->element = L1;
                            }
                            else
                            { // juste rajoute un label a la fin d'une Label_list
                                L_list *Ln = malloc(sizeof(L_list));
                                Ln->element = L1;
                                list_2->next = Ln;
                                Ln->next = NULL;
                                Ln->previous = list_1;
                            }
                        } // end if not dominance
                    } // end if feasible
                } // end for a1
                list = list->next; // passe au prochain label de la liste L_label
            } // end while
        } // end for a0
    } // end for h
};