/*  Algorythm developped by Fabian Torres & Pierre Hellich 
    Semester project Fall 2023                              */ 

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>

/////////////////////////////////////////////////////////////
///////////////////////// STRUCT ////////////////////////////
/////////////////////////////////////////////////////////////

typedef struct Group_mem Group_mem;                         // retiens le groupe d'une activite, et les groupes des activites avant et apres
struct Group_mem{
    int g;
    Group_mem* previous;
    Group_mem* next;
};

typedef struct Activity {
    int id; // this is a unique node identifier, different from group_id
    int earliest_start;
    int latest_start;
    int min_duration;
    int max_duration;
    int x;
    int y;
    int group;
    Group_mem* memory;
    int des_duration;
    int des_start_time;
}Activity;

typedef struct Label Label;                                 // holds data about a particular state or decision at a certain step in the process
struct Label {                                              // like a tree
    int acity;                                              // additional activity identifier ? (faster than L->act->id)
    int time;                                               // current time 
    int start_time; // lets you compute the start time penalty (early/late) and check the activity's time window
    // ^^ feeds the cost update when you enter a *new* activity
    int duration;                                           // time since the start of current activity
    //  ^^ lets you check min/max duration constraint & compute the duration penalty (short/long)
    // for the activity that just finished when you switch to the next activity (see update_utility)
    int deviation_start;
    int deviation_dur;
    // ^^ running sums of absolute deviations from desired start time/duration
    // they are book-keeping/diagnostic features and could be used in dominance rules
    // They are not the objective, the real penalties are added inside update_utility
    double utility;                                         // cumulative utility
    Group_mem * mem; // pointer to a linked list of visited groups
    // the label's resource that encodes "what has already been done" at the group level
    // this is the implementation of R - set of activities/groups that are no longer feasible
    // no longer feasible to re-choose because of elementarity/policy rules
    Label* previous; // back pointer to prev label
    Activity* act; // every feasibility check and cost update needs this metadata
};

// Label_List
typedef struct L_list L_list;    
struct L_list{
    Label* element;
    L_list* previous;
    L_list* next;
};

/// global _constants 
int time_interval;
double speed;
double travel_time_penalty;                       
int horizon;
int num_activities;
L_list** bucket;
Activity* activities = NULL;
int DSSR_count;
double total_time;
Label* final_schedule;

/// utility_parameters 
double asc_parameters[5];
double early_parameters[5];
double late_parameters[5];
double long_parameters[5];
double short_parameters[5];
int flex, mid_flex, not_flex;
int h8, h12, h13, h17, h20;

// scenario constraints
int leisure_close, shop_close, education_close, work_close, outings_limitation, early_curfew, finding_balance;

//////////////////////////////////////////////////////////////////////////////////////////////////////////
///////////////////// INITIALISATION /////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

int get_count() {return DSSR_count;}
double get_total_time() {return total_time;}
Label* get_final_schedule() {return final_schedule;}

void set_general_parameters(int pyhorizon, double pyspeed, double pytravel_time_penalty, 
                            int H8, int H12, int H13, int H17, int H20, int pytime_interval,
                            double* asc, double* early, double* late, double* longp, double* shortp,
                            int pyflexible, int pymid_flex, int pynot_flex){
    speed = pyspeed;
    travel_time_penalty = pytravel_time_penalty;
    horizon = pyhorizon;
    time_interval = pytime_interval;
    h8 = H8; 
    h12 = H12; 
    h13 = H13; 
    h17 = H17; 
    h20 = H20; 
    flex = pyflexible; 
    mid_flex = pymid_flex;
    not_flex = pynot_flex;

    // printf("speed = %f, travel_time_penalty = %f, curfew_time = %d, max_outside_time = %d, max_travel_time = %d, peak_hour_time1 = %d, peak_hour_time2 = %d, time_interval = %d\n",
    //         speed, travel_time_penalty, curfew_time, max_outside_time, max_travel_time, peak_hour_time1,
    //         peak_hour_time2, horizon, time_interval);

        for(int i = 0; i < 5; i++) {
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

void set_scenario_constraints(int* scenario_const) {
    leisure_close = scenario_const[0];
    shop_close = scenario_const[1];
    education_close = scenario_const[2];
    work_close = scenario_const[3];
    outings_limitation = scenario_const[4];
    early_curfew = scenario_const[5];
    finding_balance = scenario_const[6];
    // printf("leisure_close = %d, shop_close = %d, education_close = %d, work_close = %d, outings_limitation = %d, early_curfew = %d, finding_balance = %d \n",
            // leisure_close, shop_close, education_close, work_close, outings_limitation, early_curfew, finding_balance);
}

void set_activities(Activity* activities_data, int pynum_activities){
    activities = activities_data;
    num_activities = pynum_activities;
}

double distance_x(Activity* a1, Activity* a2){
    // Distance en metres
    double dx = (double)(a2->x - a1->x);
    double dy = (double)(a2->y - a1->y);
    double dist = sqrt(dx * dx + dy * dy);
    return dist; 
};

int travel_time(Activity* a1, Activity* a2){
    double dist = distance_x(a1, a2);
    int time = (int)(dist/speed);       
    time = (int)(ceil((double)time / time_interval) * time_interval);             // Round down to the nearest 5-minute interval
    int travel_time_horizon = time / time_interval;                           // Divide by 5 to fit within the 0-289 time horizon
    return travel_time_horizon;
};

void recursive_print(Label* L){
    if(L!=NULL){
        if(L->previous!=NULL){                                      
            recursive_print(L->previous);
        }
        printf("(act = %d, group = %d, start = %d, duration = %d, time = %d), ", L->acity, L->act->group, L->start_time, L->duration, L->time);
    }
};

/* initializes a two-dimensional dynamic array named bucket of size a by b. Each element of this array is of type L_list */
void create_bucket(int a, int b){
    // It allocates memory for a number of pointers to L_list
    // it's allocating memory for pointers, not for the actual L_list objects
    // (L_list**) is a type cast, which tells compiler to treat the returned pointer from malloc() as a pointer to a pointer to L_list
    bucket = (L_list**)malloc(a * sizeof(L_list*));                 
    for (int i = 0; i < a; i++) {
        bucket[i] = (L_list*)malloc(b * sizeof(L_list));        // For each of those pointers, it allocates memory for b L_list objects
        for (int j = 0; j < b; j++){                            // It then initializes the properties of each L_list to NULL.
            bucket[i][j].element = NULL;
            bucket[i][j].previous = NULL;
            bucket[i][j].next = NULL;
        }
    } 
};

/* recursively free memory associated with a given Group_mem and all its successors */ 
void delete_group(Group_mem* L){
    if(L!=NULL){
        if(L->next!= NULL){
            delete_group(L->next);
        }
        free(L);
    }
};

/* frees memory associated with a given L_list and all of its successors */ 
void delete_list(L_list* L){
    if(L->next!= NULL){                                         // After reaching the last L_list in the list, it begins freeing memory.
        delete_list(L->next);
    }
    // checks if the L_list has a Label (L->element) associated with it. If so, it frees the memory of any Group_mem of the Label too
    if(L->element != NULL){
        if(L->element->mem != NULL){
            delete_group(L->element->mem);
        }
        free(L->element);
        L->element = NULL;
    }  
};

/*  frees up the memory occupied by the bucket 
    d'abord free la memory de chaque L_list componenent, then of the bucket itself */ 
void free_bucket(){    
    for (int i = 0; i < horizon; i ++) {
        for(int j = 0; j < num_activities; j ++){
            L_list* L = &bucket[i][j];
            delete_list(L);
            &bucket[i][j] == NULL;
        }
        free(bucket[i]);
        bucket[i]=NULL;
    }
    free(bucket);
    bucket = NULL;
};

Group_mem* createNode(int data) {
    /* Purpose: Allocates memory for and initializes a new Group_mem node with provided data */
    Group_mem* newNode = (Group_mem*)malloc(sizeof(Group_mem));
    newNode->g = data;
    newNode->next = NULL;
    newNode->previous = NULL;
    return newNode;
};

/* Function to copy a linked list */ 
Group_mem* copyLinkedList(Group_mem* head) {
    if (head == NULL) {
        return NULL;
    }

    // Create a new list head
    Group_mem* newHead = createNode(head->g);
    Group_mem* newCurr = newHead;
    Group_mem* curr = head->next;

    // Copy the remaining nodes
    while (curr != NULL) {
        Group_mem* newNode = createNode(curr->g);
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
Group_mem* unionLinkedLists(Group_mem* head1, Group_mem* head2, int pipi) { // head2 is null, so we could simplify the function a lot
    int pp = 0;
    if (head1 == NULL || head2 == NULL) {
        Group_mem* newNode = createNode(pipi);
        return newNode;
    }
    Group_mem* newHead = NULL;
    Group_mem* newTail = NULL;
    Group_mem* curr1 = head1;
    
    while (curr1 != NULL) {
        Group_mem* curr2 = head2;
     
        while (curr2 != NULL) {
            
            if (curr1->g == curr2->g) {
                Group_mem* newNode = createNode(curr1->g);
                if (newHead == NULL) {
                    newHead = newNode;
                    newTail = newNode;
                } else {
                    newTail->next = newNode;
                    newNode->previous = newTail;
                    newTail = newTail->next;
                }
                break;  // Move to the next element in the first list
            }
            curr2 = curr2->next;
        }
        curr1 = curr1->next;
    }
    if(newHead == NULL){
        newHead = createNode(pipi);
        newTail = newHead;
    }else{
        newTail->next = createNode(pipi);
        newTail->next->previous = newTail;
    }
    return newHead;
};

/* Allocates memory for and initializes a new Label with the specified Activity */
Label* create_label(Activity* aa){
    Label* L = malloc(sizeof(Label));
    L->acity  = 0;
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
    return L;
};

/* Removes the label from the provided list of label and adjusts the connections of adjacent labels. */
L_list* remove_label(L_list* L){
    free(L->element);
    L->element=NULL;
    L_list* L_re;
    if(L->previous != NULL && L->next != NULL){
        L->previous->next = L->next;
        L->next->previous = L->previous;
        L_re = L->next;
        free(L);
        return L_re;
    }
    if(L->previous != NULL && L->next == NULL){
        L->previous->next = NULL;
        L_re = NULL;
        free(L);
        return L_re;
    }
    if(L->previous == NULL && L->next != NULL){
        if(L->next->next != NULL){L->next->next->previous = L;}
        L_re = L->next;
        L->element = L_re->element;
        L->next = L->next->next;
        free(L_re);
        return L;                                                   //return L which has taken the values of L->next.
    }
    if(L->previous == NULL && L->next == NULL){
        return NULL;
    }
};

/* Adds memory (a Group_mem node) to an activity in the global activities array */
void add_memory(int at, int c){
    if(activities[at].memory== NULL){                           // If the specified activity (indexed by at) doesn't already have memory, it initializes its memory with c.
        activities[at].memory = malloc(sizeof(Group_mem));
        activities[at].memory->g = c;
        activities[at].memory->previous = NULL;
        activities[at].memory->next = NULL;
    }
    else{                                                       // O/w, finds the last node in the existing memory list and adds a new Group_mem node with g set to c at the end of the list.
        Group_mem* pp = activities[at].memory;
        while(pp->next != NULL){
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
int contains(Label* L, Activity* a){
    if(a->group == 0){return 0;}
    while(L != NULL){
        if((L->act->group == a->group) && (L->act->id != a->id)){           // L->act->id = L->acity
            return 1;                                                       // If there's a match, the function returns 1 (true)
        }
        L= L->previous;
    }
    return 0;
};

/* checks if the group of activity a is already in the group memory of label L  */
// blocks adding an activity if its group is already in memory
int mem_contains(Label* L, Activity* a){ 
    if(a->group == 0){return 0;}
    Group_mem* gg = L->mem;                                 // see if the group memory in Label matches the group of a.
    while(gg!= NULL){
        if(gg->g == a->group){return 1;}                    // Returns 1 if there is a match
        gg=gg->next;
    }
    return 0;
};

/*  Determines if every group in the memory of Label L1 is also contained in the memory of Label L2 
    Return 1 if True */
int dom_mem_contains(Label* L1, Label* L2){
    // if(a->group == 0){return 0;}
    Group_mem* gg = L1->mem;

    while(gg!= NULL){                                                       // For every Group_mem in L1->mem
        Group_mem* gg2 = L2->mem;
        int contain = 0; 

        while(gg2 != NULL){                                                 // For every Group_mem in L2->mem
            if(gg->g == gg2->g){
                contain = 1;
            }
            gg2 = gg2->next;
        }

        // If a particular Group_mem from L1->mem is not in L2->mem, the function returns 0 (false)
        if(!contain){return 0;} 
        gg=gg->next;
    }
    // If all Group_mem in L1->mem are found in L2->mem, it returns 1 (true)
    return 1;
};

//////////////////////////////////////////////////////////////////////////////////////////////////////////
/////////////////////// BIG FUNCTIONS ////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

/*  Determines if an Activity a can be added to a sequence ending in label L 
    in the current scenario simulated */
int is_constrained_by_scenario(Label* L, Activity* a){
    if(education_close && a->group==1){                                                         
        return 1;
    }          
    if(work_close && a->group==2){                                                         
        return 1;
    }  
    if(leisure_close && a->group==3){                                                         
        return 1;
    }          
    if(shop_close && a->group==4){                                  
        return 1;
    }
    int time = L->time;
    // outings_limitation, early_curfew, finding_balance;
    if((outings_limitation && a->group==4) 
        && (time < h8 || time >= h12)){             
        // printf("outings_limitation %d",outings_limitation);                                 
        return 1;
    }          
    if(early_curfew && (time >= h17)){       
        // printf("early_curfew %d",early_curfew);                                      
        return 1;
    }          
    if((finding_balance && a->group==1) 
        && (time < h13 || time >= h17)){      
        // printf("time = %d, group = %d \n", L->time, a->group);                                         
        return 1;
    }    
    if((finding_balance && a->group==2) 
        && (time < h8 || time >= h12)){    
        // printf("time = %d, group = %d \n", L->time, a->group);                                               
        return 1;
    }    
    if(finding_balance && (time >=  h20)){ 
        // printf("time = %d, group = %d \n", L->time, a->group);                                                  
        return 1;
    }    
    return 0;
}

/*  Determines if an Activity a can be added to a sequence ending in label L. 
    It returns 1 if it's feasible and 0 if it's not. */
int is_feasible(Label* L, Activity* a){
    if(L == NULL){                                                          // if no Label, 'a' cann't be added
        return 0;
    }                                            
    if(L->acity != 0 && a->id == 0){                                        // exclude dawn if it's not the 1st activity of the label
        return 0;
    }
    if(is_constrained_by_scenario(L, a)){           
        return 0;
    }

    if(L->acity != a->id){                                                  // If act of L isn't the same as a, do some checks
        if(L->previous !=NULL && L->previous->acity == a->id ){             // is the previous activity the same as a ? pas sur de l'interet
            return 0;
        }
        if(L->acity == num_activities -1){                                  // Ensuring the current activity isn't the last one
            return 0;
        }
        if(L->duration <  L->act->min_duration){                            // Verifying the minimum duration of the current activity
            return 0;
        }
        // temps actuel + trajet pour a + duree min de a + trajet de a a home > fin de journee 
        // Ie enough time left in the horizon to add this activity
        if(L->time + travel_time(L->act, a) + a->min_duration + travel_time(a, &activities[num_activities-1]) >= horizon -1){
            return 0;
        }
        // Making sure the new activity starts and ends within its allowed time window : signes changed !
        if(L->time + travel_time(L->act, a) < a->earliest_start){
            return 0;
        }
        if(L->time + travel_time(L->act, a) > a->latest_start){
            return 0;
        }
        if(mem_contains(L,a)){
            // printf("\n mem_contains = %d", mem_contains(L,a));
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
    else{                                   // If the current activity in L is the same as a, check the duration
        if(L->duration + 1 > a->max_duration){        // max duration  
            return 0;
        }
    }
    return 1;                               // si tout va bien
};


/*  checks if Label L1 dominates Label L2 based on certain criteria. 
    Rappel : on minimise la utility function ! 
    0 = no dominance 
    1 = L1 dominates by default because L2 si NULL
    2 = L1 dominates L2 based on the criteria */
int dominates(Label* L1, Label* L2){ 
    if(L1 == NULL){return 0;}
    if(L2 == NULL){return 1;}
    if(L1->acity != L2->acity){return 0;}

    if(L1->utility <= L2->utility){                     // L1 a une meilleure utility que L2

        // if(L1->time <= L2->time){
        //     return 2;
        // }

        /*  S'assure que tous les group de L2 sont dans L1, sinon ca veut dire que L2 peut etre moins bien pcq elle nn'a pas encore fait un group.
            Au contraire si L1 est meilleur alors que il n'a meme pas fait tous les groupes de L2, ca veut dire que son choice set est tjrs plus grand */
        if(dom_mem_contains(L2,L1)){ // be sure of order

            // Exact method v2
            if(L1->time <= L2->time){
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

/* Calculate the utility of a label based on it's startung activity and the duration of the one that just finished */
double update_utility(Label* L){
    
    int group = L->act->group;
    Activity* act = L->act;

    Label* previous_L = L->previous;
    Activity* previous_act = previous_L->act; 
    int previous_group = previous_act->group;

    L->utility = previous_L->utility;

    L->utility -= asc_parameters[group];
    L->utility += travel_time_penalty*travel_time(previous_act, act); 
    // if (group != 2){ // NO TRAVEL TIME PENALTY TO GO TO WORK
    //     L->utility += travel_time_penalty*travel_time(previous_act, act); 
    // }
    
    // time horizons differences are multiplied to be expressed in minutes from the parameters
    // DURATION UTILITY OF FINISHED ACTIVITY
    if((previous_group == 1) || (previous_group == 2)){ // work and education
        L->utility += short_parameters[previous_group] * time_interval * fmax(0, previous_act->des_duration - previous_L->duration - not_flex)
                    + long_parameters[previous_group] * time_interval * fmax(0, previous_L->duration - previous_act->des_duration - not_flex);
    }
    if((previous_group == 3) || (previous_group == 4)){ // shop and leisure
        L->utility += short_parameters[previous_group] * time_interval * fmax(0, previous_act->des_duration - previous_L->duration - flex)
                    + long_parameters[previous_group] * time_interval * fmax(0, previous_L->duration - previous_act->des_duration - flex);
    }
    // START UTILITY OF NEW ACTIVITY
    if((group == 1) || (group == 2)){ // work and education
        L->utility += early_parameters[group] * time_interval * fmax(0, act->des_start_time - L->start_time - not_flex) 
                    + late_parameters[group] * time_interval * fmax(0, L->start_time - act->des_start_time - mid_flex);
    }
    if((group == 3) || (group == 4)){ // shop and leisure
        L->utility += early_parameters[group] * time_interval * fmax(0, act->des_start_time - L->start_time - flex) 
                    + late_parameters[group] * time_interval * fmax(0, L->start_time - act->des_start_time - mid_flex);
    }
    return L->utility;
};


/*  Generates a new label L based on an existing label L0 and an activity a */
Label* label_from_label_and_activity(Label* L0, Activity* a){
    Label* L = malloc(sizeof(Label));
    L->previous = L0;
    L->act = a;
    L->acity = a->id;
    L->deviation_start = L0->deviation_start;
    L->deviation_dur = L0->deviation_dur;
    
    if(a->id == L0->acity){                                                             // check whether the state remains in the same activity 
        L->start_time = L0->start_time;
        L->time = L0->time + 1;      
        L->duration = L0->duration+1;              
        L->utility = L0->utility;     
        L->mem = copyLinkedList(L0->mem);                                               // copy group memory of L0
    }
    else{ 
        L->start_time = L0->time + travel_time(L0->act, a);
        L->mem = unionLinkedLists(L0->mem, a->memory, a->group);                        // new group memory baed on old one and new activity group
        if(a->id == num_activities-1){                                                  // d'ou le saut chelou a la fin : DUSK (pas de utility pour dusk)
            L->duration = horizon - L->start_time -1;                                      // set to 0 before 
            L->time = horizon-1;                                                        // pq pas le temps actuel (pour uen 3e var de starting time)
        }
        else{   
            L->duration = a->min_duration;
            L->time = L->start_time + L->duration;         
        }
        L->utility = update_utility(L);                                                 // only when a new activity is added to the label. Already L->utility = L0->utility
        
        // UPDATE DEVIATIONS : 
        if (a->group != 0){ // pas d'impact pour la maison
            L->deviation_start += abs(L->start_time - a->des_start_time);
        }
        if (L0->act->group != 0){ // pas d'impact pour la maison
            L->deviation_dur += abs(L0->duration - L0->act->des_duration);
        }
    }
    return L;
};

/*  Finds the label with the minimum utility value from the list. 
    Returns the label with the minimum utility value. */
Label* find_best(L_list* B, int o){
    double min = INFINITY;
    Label* bestL = NULL;
    L_list* li = B;
    while (li != NULL){
        // printf("%s", "\n Hello there");
        if (li->element != NULL){
            if(li->element->utility < min){
                bestL = li->element;
                min = bestL->utility;
            }
        }
        li = li->next;
    }
    if(bestL == NULL){
        printf("%s", "\n Solution is not feasible, check activities parameters.");
    }
    else{
        if(o){
            printf("\n Best solution value = %.2f \n" , bestL->utility);
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
int DSSR(Label* L){
    // printf("\n DSSR");
    Label* p1 = L;
    int cycle = 0;
    int c_activity = 0;
    int group_activity = 0;

    while (p1 != NULL && cycle == 0){                            // iterates through the labels starting from L in the reverse direction until it reaches the beginning 
        while(p1 != NULL && (p1->acity == num_activities-1 || p1->acity == num_activities-2)){       // skips labels that correspond to the last activity // group == 0 ? 
            p1 = p1->previous;
        }
        Label* p2 = p1;
        while(p2 != NULL && p2->acity == p1->acity){             //  skips labels that have the same activity as p.
            p2 = p2->previous;
        }
        while(p2 != NULL && cycle == 0){                        //  checks for a cycle by looking for a previous label with the same group as p. If found, records the activity and group,
            if (p2->act->group == p1->act->group){
                cycle = 1;
                c_activity = p1->acity;
                group_activity = p1->act->group;
            }
            p2 = p2->previous;
        }
        p1 = p1->previous;
    }
    if(cycle){      // ou est ce que c'est utilise de toute facon ? et pq ca marche pas pour chaque label au fur et a mesure ? 
        Label* p3 = p1;
        while(p3 != NULL && p3->acity == c_activity){
            p3 = p3->previous;
        }
        // printf("acity >> %d \n", c_activity);
        while(p3 != NULL && p3->acity != c_activity){
            // printf("intermedaire >> %d \n", p3->acity);
            add_memory(p3->acity, group_activity);                // add une activite dans une liste qu'on ira checker si on va rajouetr le meme grouep ? 
            p3 = p3->previous;
        }
    }
    return cycle;
};

/* Dynamic Programming */
void DP (){
    // printf("\n Dynamic Programming");
    if(bucket == NULL){
        printf(" BUCKET IS NULL %d", 0); 
    }

    Label * ll = create_label(&activities[0]);                      // Initialise label avec Dawn comme 1e activite
    bucket[ll->time][0].element = ll;                               // Stocke ce label comme premier element de la L_list du temps actuel et activite 0

    for(int h = ll->time; h < horizon-1; h++){                      // pour tous les time horizons restant jusqu'a minuit
        for (int a0 = 0; a0 < num_activities; a0++){               // pour toutes les activites
            L_list* list = &bucket[h][a0];                          // list = liste de labels au temps h et pour l'activite a0

            while(list!=NULL){
                // int myBool = list!=NULL;
                // printf("myBool: %s\n", myBool ? "true" : "false");

                Label* L = list->element;                           // pour un certain label de la liste

                for(int a1 = 0; a1 < num_activities; a1 ++){        // pour toutes les activites

                    if(is_feasible(L, &activities[a1])){               // si pas feasible, passe directement au prochain a1

                        Label* L1 = label_from_label_and_activity(L, &activities[a1]);       

                        // But : garder le minimum de L_list pour le temps au nouveau label et l'activite a1
                        int dom = 0;
                        L_list* list_1 = &bucket[L1->time][a1];
                        L_list* list_2 = &bucket[L1->time][a1];

                        while(list_1 != NULL){   

                            list_2 = list_1;

                            // If a label in the bucket is dominated by L1, this label is removed from the list (bucket) 
                            if(dominates(L1, list_1->element)){
                                // printf("\n Dominance \n");
                                list_1 = remove_label(list_1);
                            }
                            // If L1 is dominated by a label in the bucket, no further comparaison is needed for L1 and it's discareded  
                            else{
                                if(dominates(list_1->element, L1)){ 
                                    // printf("\n Dominance \n");
                                    free(L1);
                                    dom = 1;                        // (dom = 1) => bucket domine L1
                                    break;                          // exit the while
                                }
                                list_1 = list_1->next;              // pour evaluer la prochaine L_list
                            };
                        }

                        // si L1 n'est domine par aucun des label de la list du bucket, il faut l'y rajouter
                        if(!dom){                                   
                            if(list_2->element == NULL){
                                list_2->element = L1;
                            }
                            else{                                   // juste rajoute un label a la fin d'une Label_list
                                L_list* Ln = malloc(sizeof(L_list));
                                Ln->element = L1;
                                list_2->next = Ln;
                                Ln->next = NULL;
                                Ln->previous = list_1;
                            }
                        }                                           // end if not dominance
                    }                                               // end if feasible
                }                                                   // end for a1
                list = list->next;                                  // passe au prochain label de la liste L_label
            }                                                       // end while
        }                                                           // end for a0
    }                                                               // end for h
};

//////////////////////////////////////////////////////////////////////////////////////////////////////////
/////////////////////// MAIN /////////////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////////////////////

int main(int argc, char* argv[]){

    clock_t start_time, end_time;
    start_time = clock();

    // it's populating or updating the "bucket" with feasible solutions or labels
    // bucket = pour chaque time horizon et pour chaque activite, voici un schedule ? 
    create_bucket(horizon, num_activities);
    DP();                                            
    
    // It's presumably the final set of solutions or labels that the algorithm is interested in 
    L_list* li = &bucket[horizon-1][num_activities-1];                      // li points to last element of bucket (pointer of pointer)
                                                                            // ie la liste de label ou la journee est finie par la derniere activitee DUSK
    
    DSSR_count = 0;
    while(DSSR(find_best(li, 0))){                                          // detect cycles in the current best solution 
    // while(DSSR_count < 10 && DSSR(find_best(li, 1))){    
        // printf("\n While loop");
        free_bucket();
        create_bucket(horizon, num_activities);
        DP();
        DSSR_count++;
        // if(DSSR_count >= 40){  // If we've reached the maximum count, break out of the loop
        //     printf("\n Maximum DSSR_count reached.");
        //     break;
        // }
        li = &bucket[horizon-1][num_activities-1];
    };
    
    final_schedule = find_best(li, 0);
    end_time = clock();  
    total_time = (double)(end_time - start_time) / CLOCKS_PER_SEC;
    return 0;
}