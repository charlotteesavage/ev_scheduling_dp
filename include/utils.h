#ifndef UTILS_H
#define UTILS_H

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
// #include <stdbool.h>
#include "scheduling.h"


// Utility functions
void recursive_print(Label *L);
Label *find_best(L_list *B, int o);

// Result accessors
int get_count(void);
double get_total_time(void);
Label *get_final_schedule(void);

// Memory management functions
void create_bucket(int a, int b);
void free_bucket(void);

// Group memory manipulation functions
Group_mem *createNode(int data);
Group_mem *copyLinkedList(Group_mem *head);
Group_mem *unionLinkedLists(Group_mem *head1, Group_mem *head2, int pipi);
L_list *remove_label(L_list *L);
void add_memory(int at, int c);

// Label/Activity checking functions
int contains(Label *L, Activity *a);
int mem_contains(Label *L, Activity *a);
int dom_mem_contains(Label *L1, Label *L2);

// Random number generation functions
void seed_random(unsigned int seed);
double normal_random(double mean, double std_dev);


#endif // UTILS_H