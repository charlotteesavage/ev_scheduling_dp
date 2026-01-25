
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <stdbool.h>
#include "scheduling.h"
#include "utils.h"

void recursive_print(Label *L)
{
    if (L != NULL)
    {
        if (L->previous != NULL)
        {
            recursive_print(L->previous);
        }
        printf("(act = %d, type/group_no = %d, start = %d, duration = %d, time = %d), ", L->act_id, L->act->group, L->start_time, L->duration, L->time);
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
// Note: This function is internal to utils.c only
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
// Note: This function is internal to utils.c only
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
   first free the memory of every L_list componenent, then of the bucket itself */
void free_bucket()
{
    for (int i = 0; i < horizon; i++)
    {
        for (int j = 0; j < max_num_activities; j++)
        {
            L_list *L = &bucket[i][j];
            delete_list(L);
            // &bucket[i][j] == NULL;
            bucket[i][j].element = NULL;
        }
        free(bucket[i]);
        bucket[i] = NULL;
    }
    free(bucket);
    bucket = NULL;
};

Group_mem *createNode(int data)
{
    /* Purpose: Allocates memory for and initializes a new Group_mem node with provided data */
    Group_mem *newNode = (Group_mem *)malloc(sizeof(Group_mem));
    newNode->g = data;
    newNode->next = NULL;
    newNode->previous = NULL;
    return newNode;
};

/* Function to copy a linked list */
Group_mem *copyLinkedList(Group_mem *head)
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
// static int group_mem_contains_value(Group_mem *head, int value)
// {
//     Group_mem *curr = head;
//     while (curr != NULL)
//     {
//         if (curr->g == value)
//         {
//             return 1;
//         }
//         curr = curr->next;
//     }
//     return 0;
// }

// static void group_mem_append_unique(Group_mem **head, Group_mem **tail, int value)
// {
//     if (group_mem_contains_value(*head, value))
//     {
//         return;
//     }
//     Group_mem *node = createNode(value);
//     if (*head == NULL)
//     {
//         *head = node;
//         *tail = node;
//         return;
//     }
//     (*tail)->next = node;
//     node->previous = *tail;
//     *tail = node;
// }

/*  Creates a new linked list that contains nodes representing the union of head1 and head2
    plus an additional node with g as pipi.*/
Group_mem *unionLinkedLists(Group_mem *head1, Group_mem *head2, int pipi)
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
L_list *remove_label(L_list *L)
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
void add_memory(int at, int c)
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

/* checks if the activity_type of activity a is already done during the label L
    peut etre a supprimer pcq un label peut etre creer a partir d'un autre label, mais poursuivre la meme actviite donc ca retournera 1 a cause de la meme activite que l'on check
    si on met une difference de acity ca peut revenir au meme
    au final meme pas utilise !
    si on le met en fonction, tout group meme devient inutile ??
    modifier la fonction et faire un print de s'ils sont differents seulement !!*/
int contains(Label *L, Activity *a)
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

/* checks if the activity_type of activity a is already in the group memory of label L  */
// blocks adding an activity if its activity_type is already in memory
int mem_contains(Label *L, Activity *a)
{
    if (a->group == 0)
    {
        return 0;
    }
    Group_mem *gg = L->mem; // see if the group memory in Label matches the activity_type of a.
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
int dom_mem_contains(Label *L1, Label *L2)
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

// seeds the random number generator for drand48()
void seed_random(unsigned int seed)
{
    srand48(seed);
}

// generates a random number from a normal distribution using Box-Muller transform
double normal_random(double mean, double std_dev)
{
    double r1;
    double r2;

    // Ensure r1 is never exactly 0 to avoid log(0) = -infinity
    do {
        r1 = drand48();
    } while (r1 == 0.0);

    r2 = drand48();

    // Box-Muller transform: convert uniform to standard normal
    double x = sqrt(-2.0 * log(r1)) * cos(2.0 * M_PI * r2);

    // Scale and shift to desired mean and std_dev
    return mean + std_dev * x;
}
