/*  Algorythm developped by Fabian Torres & Pierre Hellich
    Semester project Fall 2023                              */

#ifndef SCHEDULING_H
#define SCHEDULING_H

// #include <stdbool.h>

/////////////////////////////////////////////////////////////
///////////////////////// STRUCTS ////////////////////////////
/////////////////////////////////////////////////////////////

typedef struct Group_mem Group_mem;
// doubly linked list structure, for retaining the current, previous, and next groups of an activity
// necessary for the DSSR functionality
struct Group_mem
{
    int g;
    Group_mem *previous;
    Group_mem *next;
};

typedef struct Activity
// id encompasses unique combo of type, charging mode, and location!!!!
{
    int id; // this is a unique node identifier, different from group_id
    int earliest_start; // expressed in # of time intervals
    int latest_start;   // expressed in # of time intervals
    int min_duration;   // expressed in # of time intervals
    int max_duration;   // expressed in # of time intervals
    double x;
    double y;
    int group; // this is the activity type
    Group_mem *memory;
    int des_duration;   // expressed in # of time intervals
    int des_start_time; // expressed in # of time intervals

    int charge_mode; // Charging mode: 0=none, 1=slow, 2=fast, 3=rapid, 4=free_slow, 5=free_fast, 6=free_rapid
    int is_charging; // Is charging selected? 1 for yes, 0 for no

    int is_service_station; // 1 for yes, 0 for no

} Activity;

// decision variable for: charging, which charge mode, need a duplication of a for every kind of charging you might do
// just pick fastest charge mode available
// want to expand to pick the optimal, not just fastest in the future

typedef struct Label Label; // holds data about a particular state or decision at a certain step in the process
struct Label
{
    int act_id;     // unique activity identifier (faster than L->act->id)
    int time;       // current time in number of intervals since dawn
    int start_time; // lets you compute the start time penalty (early/late) and check the activity's time window
    // ^^ feeds the cost update when you enter a *new* activity
    int duration; // time since the start of current activity in minutes
    //  ^^ lets you check min/max duration constraint & compute the duration penalty (short/long)
    // for the activity that just finished when you switch to the next activity (see update_utility)
    int deviation_start;
    int deviation_dur;
    // ^^ running sums of absolute deviations from desired start time/duration
    // they are book-keeping/diagnostic features and could be used in dominance rules
    // They are not the objective, the real penalties are added inside update_utility

    double soc_at_activity_start; // battery state of charge at the start of activity 𝑎
    double current_soc;           // battery state in activity - relevant for charging activities
    // double delta_soc_during_interval; // SOC increase during this charging time, if occurring
    // double total_delta_soc;
    // double soc; // check this - might be parsed from individual data
    double delta_soc; // amount of battery charge increase at a single interval, expressed as % of battery capacity i.e. SOC

    int charge_duration; // cumulative time spent charging at current activity (resets to zero when move to new activity)

    // double wasted_charger_time;
    double charge_cost_at_activity_start; // cumulative charge cost at the start of activity 𝑎
    double current_charge_cost; // cumulative charging cost up to end of current interval

    double utility; // cumulative utility

    Group_mem *mem; // pointer to a linked list of visited groups
    // the label's resource that encodes "what has already been done" at the group level
    // this is the implementation of R - set of activities/groups that are no longer feasible
    // no longer feasible to re-choose because of elementarity/policy rules
    Label *previous; // back pointer to prev label
    Activity *act;   // every feasibility check and cost update needs this metadata
};

// Label_List
typedef struct L_list L_list;
struct L_list
{
    Label *element;
    L_list *previous;
    L_list *next;
};

// Global constants
extern int time_interval; // fixed width of time interval eg 5 mins
extern double speed;
extern double travel_time_penalty;
extern int horizon; // total no of time intervals
extern int max_num_activities;
extern L_list **bucket;
extern Activity *activities;
extern int DSSR_count;
extern double total_time;
extern Label *final_schedule;

// Battery/EV parameters
extern double battery_capacity;
extern double soc_threshold;
extern double energy_consumption_rate;

extern double initial_soc;

extern double slow_charge_rate;
extern double fast_charge_rate;
extern double rapid_charge_rate;

// Pricing
extern double home_off_peak_price;
extern double home_slow_charge_price;
extern double AC_charge_price;
extern double public_dc_charge_price;
extern double free_charging;

// Time of use factors
extern double tou_peak_factor;
extern double tou_midpeak_factor;
extern double tou_offpeak_factor;

extern int peak_start;
extern int peak_end;
extern int midpeak1_start;
extern int midpeak1_end;
extern int midpeak2_start;
extern int midpeak2_end;

// Utility parameters
// Note: Array size must accommodate the maximum group number used in activities
// After preprocessing (group-1): groups 0-7, so we need size 8
extern double asc_parameters[9];
extern double early_parameters[9];
extern double late_parameters[9];
extern double long_parameters[9];
extern double short_parameters[9];
// extern int flex, mid_flex, not_flex;

// Charging utility parameters
extern double gamma_charge_work;
extern double gamma_charge_non_work;
extern double gamma_charge_home;
extern double theta_soc;
extern double beta_delta_soc;
extern double beta_charge_cost;
extern double soc_full;

// Initialization functions
void initialize_charge_rates(void);
void set_general_parameters(int pyhorizon, double pyspeed, double pytravel_time_penalty,
                            int pytime_interval, double *asc, double *early, double *late,
                            double *longp, double *shortp

);
void set_activities(Activity *activities_data, int pynum_activities);
void set_fixed_initial_soc(double soc);
void clear_fixed_initial_soc(void);
void set_random_seed(unsigned int seed_value);

// Algorithm functions
void DP(void);
int DSSR(Label *L);

#endif // SCHEDULING_H
