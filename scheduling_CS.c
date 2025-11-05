/*  Algorythm developped by Fabian Torres & Pierre Hellich
    Semester project Fall 2023                              */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <stdbool.h>
#include "scheduling_CS.h"

/// global _constants
int time_interval;
double speed;
double travel_time_penalty;
int horizon;
int num_activities;
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

// double slow_charge_power = 7.0;
// double fast_charge_power = 22;
// double rapid_charge_power = 50;

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

int peak_start;     // 12:00 in time intervals
int peak_end;       // 18:00 in time intervals
int midpeak1_start; // 8:00
int midpeak1_end;   // 12:00
int midpeak2_start; // 18:00
int midpeak2_end;   // 21:00

/// utility_parameters
double asc_parameters[5];
double early_parameters[5];
double late_parameters[5];
double long_parameters[5];
double short_parameters[5];
int flex, mid_flex, not_flex; // how much deviation is allowed from the preferred schedule

// new utility terms
double gamma_charge_work = -3.59;     // inconvenience of charging at work activity
double gamma_charge_non_work = -4.34; // inconvenience of charging at non-work activity
double gamma_charge_home = -3.34;
double theta_soc = -80;         // low battery SOC penalty param
double beta_delta_soc = 25;     // charged battery SOC penalty param
double beta_charge_cost = -0.3; // param for charging costs per mode

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// INITIALISATION /////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

int get_count() { return DSSR_count; }
double get_total_time() { return total_time; }
Label *get_final_schedule() { return final_schedule; }

void set_general_parameters(int pyhorizon, double pyspeed, double pytravel_time_penalty, int pytime_interval,
                            double *asc, double *early, double *late, double *longp, double *shortp,
                            int pyflexible, int pymid_flex, int pynot_flex)
{
    speed = pyspeed;
    travel_time_penalty = pytravel_time_penalty;
    horizon = pyhorizon;
    time_interval = pytime_interval;
    flex = pyflexible;
    mid_flex = pymid_flex;
    not_flex = pynot_flex;

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
    L->time = aa->min_duration;
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

    L->soc = initial_soc; // check this - might be parsed from individual data
    L->is_charging = 0;
    L->charge_mode = 0;
    L->charge_duration = 0;
    L->delta_soc = 0; // clarify what is meant by this cf (10)
    L->charge_cost = 0;

    return L;
};

static void initialize_charge_rates() // initialise these rates per eqn (39) in paper
{
    slow_charge_rate = 7.0 / battery_capacity;
    fast_charge_rate = 22.0 / battery_capacity;
    rapid_charge_rate = 50.0 / battery_capacity;
}

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// HELPER FUNCTIONS /////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

static double distance_x(Activity *a1, Activity *a2) // this will change as we will just reference a skim matrix
{
    // Distance en metres
    double dx = (double)(a2->x - a1->x);
    double dy = (double)(a2->y - a1->y);
    double dist = sqrt(dx * dx + dy * dy);
    return dist;
};

static int travel_time(Activity *a1, Activity *a2) // think this will change too
{                                                  // search TAG for reference speeds for urban traffic, cited value of why you're using a partiular speed
    double dist = distance_x(a1, a2);
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

static void get_charge_rate_and_price(Label *label, Activity *a, double result[2])
// need to make sure label charge modes are parsed to ints per .h file for this to work
{
    double charge_rate = 0.0;
    double charge_price = 0.0;

    switch (label->charge_mode)
    {
    case 1:
        charge_rate = slow_charge_rate;
        if (a->group == 0)
        {
            charge_price = home_slow_charge_price;
        }
        if (a->group != 0)
        {
            charge_price = AC_charge_price;
        }
        break;

    case 2:
        charge_rate = fast_charge_rate;
        charge_price = AC_charge_price;
        break;

    case 3:
        charge_rate = rapid_charge_rate;
        charge_price = public_dc_charge_price;
        break;
    }
    result[0] = charge_rate;
    result[1] = charge_price;
}

static double get_tou_factor(int time)
{
    int hour = (time * time_interval) / 60;

    if (hour >= peak_start && time < peak_end)
    {
        return tou_peak_factor;
    }
    else if ((hour >= midpeak1_start && time < midpeak1_end) || (hour >= midpeak2_start && time < midpeak2_end))
    {
        return tou_midpeak_factor;
    }
    else
    {
        return tou_offpeak_factor;
    }
}

void recursive_print(Label *L)
{
    if (L != NULL)
    {
        if (L->previous != NULL)
        {
            recursive_print(L->previous);
        }
        printf("(act = %d, group = %d, start = %d, duration = %d, time = %d), ", L->act_id, L->act->group, L->start_time, L->duration, L->time);
    }
};

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// BUCKET AND MEMORY STUFF /////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

/* initializes a two-dimensional dynamic array named bucket of size a by b. Each element of this array is of type L_list */
void create_bucket(int a, int b)
{
    // It allocates memory for a number of pointers to L_list
    // allocating memory for pointers, not for the actual L_list objects
    // (L_list**) is a type cast, which tells compiler to treat the returned pointer from malloc() as a pointer to a pointer to L_list
    bucket = (L_list **)malloc(a * sizeof(L_list *));
    for (int i = 0; i < a; i++)
    {
        bucket[i] = (L_list *)malloc(b * sizeof(L_list)); // For each of those pointers, it allocates memory for b L_list objects
        for (int j = 0; j < b; j++)
        { // It then initializes the properties of each L_list to NULL.
            bucket[i][j].element = NULL;
            bucket[i][j].previous = NULL;
            bucket[i][j].next = NULL;
        }
    }
};

/* recursively free memory associated with a given Group_mem and all its successors */
static void delete_group(Group_mem *L)
{
    if (L != NULL)
    {
        if (L->next != NULL)
        {
            delete_group(L->next);
        }
        free(L);
    }
};

/* frees memory associated with a given L_list and all of its successors */
static void delete_list(L_list *L)
{
    if (L->next != NULL)
    { // After reaching the last L_list in the list, it begins freeing memory.
        delete_list(L->next);
    }
    // checks if the L_list has a Label (L->element) associated with it. If so, it frees the memory of any Group_mem of the Label too
    if (L->element != NULL)
    {
        if (L->element->mem != NULL)
        {
            delete_group(L->element->mem);
        }
        free(L->element);
        L->element = NULL;
    }
};

/*  frees up the memory occupied by the bucket
    d'abord free la memory de chaque L_list componenent, then of the bucket itself */
void free_bucket()
{
    for (int i = 0; i < horizon; i++)
    {
        for (int j = 0; j < num_activities; j++)
        {
            L_list *L = &bucket[i][j];
            delete_list(L);
            &bucket[i][j] == NULL;
        }
        free(bucket[i]);
        bucket[i] = NULL;
    }
    free(bucket);
    bucket = NULL;
};

static Group_mem *createNode(int data)
{
    /* Purpose: Allocates memory for and initializes a new Group_mem node with provided data */
    Group_mem *newNode = (Group_mem *)malloc(sizeof(Group_mem));
    newNode->g = data;
    newNode->next = NULL;
    newNode->previous = NULL;
    return newNode;
};

/* Function to copy a linked list */
static Group_mem *copyLinkedList(Group_mem *head)
{
    if (head == NULL)
    {
        return NULL;
    }

    // Create a new list head
    Group_mem *newHead = createNode(head->g);
    Group_mem *newCurr = newHead;
    Group_mem *curr = head->next;

    // Copy the remaining nodes
    while (curr != NULL)
    {
        Group_mem *newNode = createNode(curr->g);
        newCurr->next = newNode;
        newNode->previous = newCurr;
        newCurr = newCurr->next;
        curr = curr->next;
    }
    return newHead;
};

/*  Creates a new linked list that contains nodes representing the union of head1 and head2
    plus an additional node with g as pipi.*/
// updates the memory when you start a new activity
static Group_mem *unionLinkedLists(Group_mem *head1, Group_mem *head2, int pipi)
{ // head2 is null, so we could simplify the function a lot
    int pp = 0;
    if (head1 == NULL || head2 == NULL)
    {
        Group_mem *newNode = createNode(pipi);
        return newNode;
    }
    Group_mem *newHead = NULL;
    Group_mem *newTail = NULL;
    Group_mem *curr1 = head1;

    while (curr1 != NULL)
    {
        Group_mem *curr2 = head2;

        while (curr2 != NULL)
        {

            if (curr1->g == curr2->g)
            {
                Group_mem *newNode = createNode(curr1->g);
                if (newHead == NULL)
                {
                    newHead = newNode;
                    newTail = newNode;
                }
                else
                {
                    newTail->next = newNode;
                    newNode->previous = newTail;
                    newTail = newTail->next;
                }
                break; // Move to the next element in the first list
            }
            curr2 = curr2->next;
        }
        curr1 = curr1->next;
    }
    if (newHead == NULL)
    {
        newHead = createNode(pipi);
        newTail = newHead;
    }
    else
    {
        newTail->next = createNode(pipi);
        newTail->next->previous = newTail;
    }
    return newHead;
};

/* Removes the label from the provided list of label and adjusts the connections of adjacent labels. */
static L_list *remove_label(L_list *L)
{
    free(L->element);
    L->element = NULL;
    L_list *L_re;
    if (L->previous != NULL && L->next != NULL)
    {
        L->previous->next = L->next;
        L->next->previous = L->previous;
        L_re = L->next;
        free(L);
        return L_re;
    }
    if (L->previous != NULL && L->next == NULL)
    {
        L->previous->next = NULL;
        L_re = NULL;
        free(L);
        return L_re;
    }
    if (L->previous == NULL && L->next != NULL)
    {
        if (L->next->next != NULL)
        {
            L->next->next->previous = L;
        }
        L_re = L->next;
        L->element = L_re->element;
        L->next = L->next->next;
        free(L_re);
        return L; // return L which has taken the values of L->next.
    }
    if (L->previous == NULL && L->next == NULL)
    {
        return NULL;
    }
};

/* Adds memory (a Group_mem node) to an activity in the global activities array */
static void add_memory(int at, int c)
{
    if (activities[at].memory == NULL)
    { // If the specified activity (indexed by at) doesn't already have memory, it initializes its memory with c.
        activities[at].memory = malloc(sizeof(Group_mem));
        activities[at].memory->g = c;
        activities[at].memory->previous = NULL;
        activities[at].memory->next = NULL;
    }
    else
    { // O/w, finds the last node in the existing memory list and adds a new Group_mem node with g set to c at the end of the list.
        Group_mem *pp = activities[at].memory;
        while (pp->next != NULL)
        {
            pp = pp->next;
        }
        pp->next = malloc(sizeof(Group_mem));
        pp->next->g = c;
        pp->next->previous = pp;
        pp->next->next = NULL;
    }
};

/* checks if the group of activity a is already done during the label L
    peut etre a supprimer pcq un label peut etre creer a partir d'un autre label, mais poursuivre la meme actviite donc ca retournera 1 a cause de la meme activite que l'on check
    si on met une difference de acity ca peut revenir au meme
    au final meme pas utilise !
    si on le met en fonction, tout group meme devient inutile ??
    modifier la fonction et faire un print de s'ils sont differents seulement !!*/
static int contains(Label *L, Activity *a)
{
    if (a->group == 0)
    {
        return 0;
    }
    while (L != NULL)
    {
        if ((L->act->group == a->group) && (L->act->id != a->id))
        {             // L->act->id = L->acity
            return 1; // If there's a match, the function returns 1 (true)
        }
        L = L->previous;
    }
    return 0;
};

/* checks if the group of activity a is already in the group memory of label L  */
// blocks adding an activity if its group is already in memory
static int mem_contains(Label *L, Activity *a)
{
    if (a->group == 0)
    {
        return 0;
    }
    Group_mem *gg = L->mem; // see if the group memory in Label matches the group of a.
    while (gg != NULL)
    {
        if (gg->g == a->group)
        {
            return 1;
        } // Returns 1 if there is a match
        gg = gg->next;
    }
    return 0;
};

/*  Determines if every group in the memory of Label L1 is also contained in the memory of Label L2
    Return 1 if True */
static int dom_mem_contains(Label *L1, Label *L2)
{
    // if(a->group == 0){return 0;}
    Group_mem *gg = L1->mem;

    while (gg != NULL)
    { // For every Group_mem in L1->mem
        Group_mem *gg2 = L2->mem;
        int contain = 0;

        while (gg2 != NULL)
        { // For every Group_mem in L2->mem
            if (gg->g == gg2->g)
            {
                contain = 1;
            }
            gg2 = gg2->next;
        }

        // If a particular Group_mem from L1->mem is not in L2->mem, the function returns 0 (false)
        if (!contain)
        {
            return 0;
        }
        gg = gg->next;
    }
    // If all Group_mem in L1->mem are found in L2->mem, it returns 1 (true)
    return 1;
};

//////////////////////////////////////////////////////////////////////////////////////////////////////////
/////////////////////// BIG FUNCTIONS ////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

/*  Determines if an Activity a can be added to a sequence ending in label L.
    It returns 1 if it's feasible and 0 if it's not. */
static int is_feasible(Label *L, Activity *a)
{
    if (L == NULL)
    { // if no Label, 'a' cannot be added
        return 0;
    }
    if (L->act_id != 0 && a->id == 0)
    { // exclude dawn if it's not the 1st activity of the label
        return 0;
    }

    if (L->act_id != a->id)
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
        // temps actuel + trajet pour a + duree min de a + trajet de a a home > fin de journee
        // Ie enough time left in the horizon to add this activity
        if (L->time + travel_time(L->act, a) + a->min_duration + travel_time(a, &activities[num_activities - 1]) >= horizon - 1)
        {
            return 0;
        }
        // Making sure the new activity starts and ends within its allowed time window : signes changed !
        if (L->time + travel_time(L->act, a) < a->earliest_start)
        {
            return 0;
        }
        if (L->time + travel_time(L->act, a) > a->latest_start)
        {
            return 0;
        }
        if (mem_contains(L, a))
        {
            // printf("\n mem_contains = %d", mem_contains(L,a));
            return 0;
        }
        if (L->soc < 0) // constraint 25
        {
            return 0;
        }
        //
        if (L->soc + L->delta_soc > soc_full) // constraint 26
        {
            return 0;
        }

        double potential_energy_needed = energy_consumed_soc(L->act, a);

        if (L->soc + L->delta_soc < potential_energy_needed) // constraint 27
        {
            return 0;
        }

        if (L->charge_duration > a->max_duration) // check this if it is enough
        {
            return 0;
        }

        if (L->is_charging == 0 && L->charge_duration < 0)
        {
            return 0;
        }

        if (a->is_service_station && !L->is_charging)
        {
            return 0;
        }

        // if(contains(L,a)){
        //     // printf("\n contains = %d", contains(L, a));
        //     return 0;
        // }
        // if(mem_contains(L,a) || contains(L, a)){
        //     if(mem_contains(L,a) != contains(L, a)){
        //         // printf("\n Difference = %d", mem_contains(L,a) - contains(L, a));
        //     }
        //     return 0;
        // }
    }
    else
    { // If the current activity in L is the same as a, check the duration
        if (L->duration + 1 > a->max_duration)
        { // max duration
            return 0;
        }
    }
    return 1; // si tout va bien
};

/*  checks if Label L1 dominates Label L2 based on certain criteria.
    Rappel : on minimise la utility function !
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

    if (L1->utility <= L2->utility)
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
{
    // should have an error term, but don't need a cost penalty
    // cost of travel is associated with EV cost only, don't account for EV tax, parking etc
    // to be listed as assumptions at beginning of paper

    int group = L->act->group;
    Activity *act = L->act;

    Label *previous_L = L->previous;
    Activity *previous_act = previous_L->act;
    int previous_group = previous_act->group;

    L->utility = previous_L->utility;

    L->utility -= asc_parameters[group];
    L->utility += travel_time_penalty * travel_time(previous_act, act);

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
    // START UTILITY OF NEW ACTIVITY
    if ((group == 1) || (group == 6))
    { // education and work --> check flex params
        L->utility += early_parameters[group] * time_interval * fmax(0, act->des_start_time - L->start_time - not_flex) + late_parameters[group] * time_interval * fmax(0, L->start_time - act->des_start_time - mid_flex);
    }
    if ((group == 2) || (group == 3) || (group == 4) || (group == 5))
    { // errands, escort, leisure, shopping,
        L->utility += early_parameters[group] * time_interval * fmax(0, act->des_start_time - L->start_time - flex) + late_parameters[group] * time_interval * fmax(0, L->start_time - act->des_start_time - mid_flex);
    }

    // service station has no duration penalties, only penalties come from cost of charge

    // DURATION UTILITY OF FINISHED ACTIVITY
    if ((group == 1) || (group == 6))
    { // work and education
        L->utility += short_parameters[previous_group] * time_interval * fmax(0, previous_act->des_duration - previous_L->duration - not_flex) + long_parameters[previous_group] * time_interval * fmax(0, previous_L->duration - previous_act->des_duration - not_flex);
    }
    if ((group == 2) || (group == 3) || (group == 4) || (group == 5))
    { // errands, escort, leisure, shopping
        L->utility += short_parameters[previous_group] * time_interval * fmax(0, previous_act->des_duration - previous_L->duration - flex) + long_parameters[previous_group] * time_interval * fmax(0, previous_L->duration - previous_act->des_duration - flex);
    }

    // service station penalty is only for long
    // check if charging constrants can be used to deal with service stations instead
    // time window constraints between 7am and 11pm, not allowed outside that

    // Charging utility terms
    if ((group == 6))
    {
        L->utility += gamma_charge_work;
    }
    //-3.34, # -3.34home slow charging relative to not charging

    else
    {
        L->utility += gamma_charge_non_work;
    }
    // SOC𝑎 represents the state of charge after travel and at the start time of activity 𝑎.
    L->utility += theta_soc * fmax(0, soc_threshold - L->soc);

    L->utility += beta_delta_soc * fmin(1 - previous_L->soc, L->delta_soc);

    L->utility += beta_charge_cost * L->charge_cost;

    return L->utility;
};

// /*  Generates a new label L based on an existing label current_label and an activity a */
static Label *update_label_from_activity(Label *current_label, Activity *a)
{
    Label *new_label = malloc(sizeof(Label));
    new_label->previous = current_label;
    new_label->act = a;
    new_label->act_id = a->id;
    new_label->deviation_start = current_label->deviation_start;
    new_label->deviation_dur = current_label->deviation_dur;

    if (a->id == current_label->act_id) // check if the activity is a continuation of old activity
    // no SOC discharge because no travel, only charging is possible
    {
        // inherit time and memory variables
        new_label->start_time = current_label->start_time;
        new_label->time = current_label->time + 1;
        new_label->duration = current_label->duration + 1;
        new_label->mem = copyLinkedList(current_label->mem);

        // inherit charging state
        new_label->is_charging = current_label->is_charging;
        new_label->charge_mode = current_label->charge_mode;

        if (new_label->is_charging) // need to check for where charging is free??
        {                           // might have to put charging price back into activity class
            new_label->charge_duration = current_label->charge_duration + 1;
            double results[2];
            get_charge_rate_and_price(new_label, a, results);
            double charge_rate = results[0];
            double charge_price = results[1];

            new_label->delta_soc = fmin(1 - current_label->soc, charge_rate * time_interval);
            new_label->soc = current_label->soc + new_label->delta_soc;

            double tou_factor = get_tou_factor(new_label->time);
            double interval_cost = charge_price * tou_factor * charge_rate * (time_interval / 60.0);
            new_label->charge_cost = current_label->charge_cost + interval_cost;
        }
        else
        { // not charging
            new_label->charge_duration = 0;
            new_label->soc = current_label->soc;
            new_label->delta_soc = 0;
            new_label->charge_cost = current_label->charge_cost;
        }
        new_label->utility = update_utility(new_label);
    }

    else // move to new activity
    {
        new_label->start_time = current_label->start_time + travel_time(current_label->act, a);
        new_label->mem = unionLinkedLists(current_label->mem, a->memory, a->group);

        double soc_consumed = energy_consumed_soc(current_label->act, a);
        new_label->soc = current_label->soc - soc_consumed;

        // just put these at 0 for now, need to check...
        new_label->is_charging = 0;
        new_label->charge_mode = 0;
        new_label->charge_duration = 0;
        new_label->delta_soc = 0;
        new_label->charge_cost = current_label->charge_cost;

        if (a->id == num_activities - 1)
        {
            new_label->duration = horizon - new_label->start_time - 1;
            new_label->time = horizon - 1;
        }
        else
        {
            new_label->duration = a->min_duration;
            new_label->time = new_label->start_time + new_label->duration;
        }
        new_label->utility = update_utility(new_label);

        // **SERVICE STATION HANDLING**: No deviation penalties
        if (!a->is_service_station)
        {
            if (a->group != 0)
            {
                new_label->deviation_start += abs(new_label->start_time - a->des_start_time);
            }
        }

        if (!current_label->act->is_service_station)
        {
            if (current_label->act->group != 0)
            {
                new_label->deviation_dur += abs(current_label->duration - current_label->act->des_duration);
            }
        }
    }
    return new_label;
}

/*  Finds the label with the minimum utility value from the list.
    Returns the label with the minimum utility value. */
Label *find_best(L_list *B, int o)
{
    double min = INFINITY;
    Label *bestL = NULL;
    L_list *li = B;
    while (li != NULL)
    {
        // printf("%s", "\n Hello there");
        if (li->element != NULL)
        {
            if (li->element->utility < min)
            {
                bestL = li->element;
                min = bestL->utility;
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
            if (p2->act->group == p1->act->group)
            {
                cycle = 1;
                c_activity = p1->act_id;
                group_activity = p1->act->group;
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

    Label *ll = create_label(&activities[0]); // Initialise label avec Dawn comme 1e activite
    bucket[ll->time][0].element = ll;         // Stocke ce label comme premier element de la L_list du temps actuel et activite 0

    for (int h = ll->time; h < horizon - 1; h++)
    { // pour tous les time horizons restant jusqu'a minuit
        for (int a0 = 0; a0 < num_activities; a0++)
        {                                  // pour toutes les activites
            L_list *list = &bucket[h][a0]; // list = liste de labels au temps h et pour l'activite a0

            while (list != NULL)
            {
                // int myBool = list!=NULL;
                // printf("myBool: %s\n", myBool ? "true" : "false");

                Label *L = list->element; // pour un certain label de la liste

                for (int a1 = 0; a1 < num_activities; a1++)
                { // pour toutes les activites

                    if (is_feasible(L, &activities[a1]))
                    { // si pas feasible, passe directement au prochain a1

                        Label *L1 = label_from_label_and_activity(L, &activities[a1]);

                        // But : garder le minimum de L_list pour le temps au nouveau label et l'activite a1
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
                            // If L1 is dominated by a label in the bucket, no further comparaison is needed for L1 and it's discareded
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
