
/*  Algorythm developped by Fabian Torres & Pierre Hellich
    Semester project Fall 2023                              */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
// #include <stdbool.h>
#include "scheduling.h"
#include "utils.h"


int main(int argc, char *argv[])
{

    clock_t start_time, end_time;
    start_time = clock();

    // it's populating or updating the "bucket" with feasible solutions or labels
    // bucket = pour chaque time horizon et pour chaque activite, voici un schedule ?
    create_bucket(horizon, max_num_activities);
    DP();

    // It's presumably the final set of solutions or labels that the algorithm is interested in
    L_list *li = &bucket[horizon - 1][max_num_activities - 1]; // li points to last element of bucket (pointer of pointer)
                                                           // ie la liste de label ou la journee est finie par la derniere activitee DUSK

    DSSR_count = 0;
    while (DSSR(find_best(li, 0)))
    { // detect cycles in the current best solution
        // while(DSSR_count < 10 && DSSR(find_best(li, 1))){
        // printf("\n While loop");
        free_bucket();
        create_bucket(horizon, max_num_activities);
        DP();
        DSSR_count++;
        // if(DSSR_count >= 40){  // If we've reached the maximum count, break out of the loop
        //     printf("\n Maximum DSSR_count reached.");
        //     break;
        // }
        li = &bucket[horizon - 1][max_num_activities - 1];
    };

    final_schedule = find_best(li, 0);
    end_time = clock();
    total_time = (double)(end_time - start_time) / CLOCKS_PER_SEC;
    return 0;
}