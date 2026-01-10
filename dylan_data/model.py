import random
from matplotlib import pyplot as plt
import pandas as pd
import numpy as np
# from data_utils import cplex_to_df, create_dicts, plot_schedule, plot_mode
from data_utils import cplex_to_df, create_dicts
import pickle
import googlemaps
import json
import math
from docplex.mp.model import Model
from docplex.mp.conflict_refiner import ConflictRefiner

def optimize_schedule(df = None, travel_times = None, distances = None, n_iter = 1, initial_soc=None, plot_every = 3, mtmc = True, parameters = None, var = 1, deterministic = False, plot_mode = False):
    '''
    Optimize schedule using CPLEX solver, given timing preferences and travel time matrix.
    Can produce a graphical output if specified (by argument plot_every)
    travel_times = used to be 2d nest Orig X Dest, changed to 3d nest Mode X Orig X Dest --> need to add mode in dictionary
    '''
    print('var for error terms:', var)  
    period = 24
    modes = ["driving", "bicycling", "transit", "walking"]
    charge_modes = [ "notcharge", "slow", "fast", "rapid"]
    #flexibility of start time, duration and early/late arrival
    if parameters is None:
        # p_st_e = {'F': 0,'M': -0.61,'R': -2.4}#penalties for early arrival
        # p_st_l = {'F': 0,'M': -2.4,'R': -9.6}  #penalties for late arrival
        # p_dur_s = {'F': -0.61,'M': -2.4,'R': -9.6}#penalties for short duration
        # p_dur_l = {'F': -0.61,'M': -2.4,'R': -9.6}#penalties for long duration
        
        p_t = -1 #penalty for travel time
        p_soc= -24/20 #penalty for SOC 5 per hour for parking, 20% soc per hour, charging cost for per hour Most network rapid chargers cost 74p/kwh which is about £19 for 30 minutes of charging (as of May 2023).
        #values of time (Weis et al, 2021 - table 6)
        
        vot = {"driving": 13.2,
        "bicycling": 9.9,
        "transit": 12.3,
        "walking": 6
        }

        #values of leisure and work (Schmid et al 2019):
        vot_act ={"home": 25.2,
            "work" : -20.6,
            "education": -20.6,
            "shopping": 25.2,
            "errands_services": 25.2,
            "business_trip": -20.6,
            "leisure": 25.2,
            "escort": 25.2
        }

        #penalties travel cost
        p_t_cost = {mode : p_t/vot[mode] for mode in modes}
        
        soc_threshold=0.3 #Example:30% SOC threshold for range anxiety
        adjustment_tuning_acti=1 # 0.8 tuning parameter for the adjustment of the activity constant
        p_act_cost =-1
        p_lowsoc_cost=-86.810 #-86.810 #penalty for charging at low SOC the remaining range of the vehicle 0.2 kwh per km multiplied by 60 kwh battery capacity
        p_charge_cost=-0.3 #0.3 p_t/vot['driving'] #0.2 penalty charging cost per kwh
        adjustment_tuning_charge=5 #5 tuning parameter for the adjustment of the charging inconvenience penalty
        p_soc_charged=25 #20 penalty for SOC charged to the battery capacity
        # soc influence on activity participation 1+0.6*soc

        #inconvenience charge constants 3 adjusted to reflect the inconvenience of charging
        inconvenience_charge_const= {1:-3.34, # -3.34home slow charging relative to not charging
            2 :-3.59, #work slow and fast charging
            3: -4.34, # 4.34education, slow and fast charging
            4: -4.34, #shopping slow and fast charging // consider the free charging at shopping
            5: -4.34, #errands slow and fast charging
            6: -4.34, #service station super rapid charging
            8: -4.34, #leisure,slow and fast charging
            9: -4.34} #escort slow and fast charging
        #error terms
                                   
        soc_threshold=0.3# Example:30% SOC threshold for range anxiety
        # charging cost per hour as the base level 2 cost is estimated using level 2 cost cofficiency cost_level2=1.1
        
        costs_charging_type1 = {1:1.5, #1.5,2.1, #home slow charging
            2: 2.7, #work slow charging
            3: 2.7, #education, slow charging
            4: 2.7, #shopping slow charging // consider the free charging at shopping
            5: 2.7, #errands slow charging
            6: 2.7, #service station super rapid charging
            8: 2.7, #leisure,slow charging
            9: 2.7} #escort slow charging
        costs_charging_type2 = {1:15, #4.2, #home and community fast charging
            2 :12, #work fast charging
            3: 12, #education,fast charging
            4: 12, #shopping fast charging // consider the free charging at shopping
            5: 12, #errands  fast charging
            6: 12, #service station super rapid charging
            8: 12, #leisure,slow and fast charging
            9: 12} #escort slow and fast charging
        costs_charging_type3 = {1:4.2, #home slow charging
            2 :12, #work slow and fast charging
            3: 12, #education, slow and fast charging
            4: 12, #shopping slow and fast charging // consider the free charging at shopping
            5: 12, #errands slow and fast charging
            6: 37, #service station super rapid charging
            8: 12, #leisure,slow and fast charging
            9: 12} #escort slow and fast charging
        
        #travel costs (BfS 2018)
        costs_travel = {"driving": 0.37,
        "bicycling": 0,
        "transit": 0.03,
        "walking": 0
        }

        #activity costs (derived from Schmid et al 2021, from EVE dataset with household budgets)
        costs_activity = {1: 0, #home
            2: 0, #work #need to change for wage
            3: 0, #education,
            4: 16.8, #shopping
            5: 36.4, #errands
            6: 0, #37service station #need to change for charging orginal 0  30 minutes charging duration for rapid charging 80% SOC
            8: 12, #leisure,
            9: 0} #escort
        #participation constants 0.6 adjusted
        act_participations_const = {1: 0, #home  oasis part-c estiamtes
            2 :10.6, #work #need to change for wage+
            3: 17.4, #education,+
            4: 11.3, #shopping -(16.8-11.3)=-5.5
            5: 16.1, #errands   -(36.4-16.1)=-20.3
            6: 0, # 3 default seeting 6 service station #need to change for charging orginal 0  30 minutes charging duration for rapid charging 80% SOC
            8: 8.74, #leisure,12
            9: 6.76} #escort +
        
        sd=math.sqrt(var) #standard deviation for the error terms
        error_w = np.random.normal(scale = sd, size = 2) #acitivty choice
        error_x = np.random.normal(scale = sd, size = 4) #discretization start time: 4h time blocks
        error_d = np.random.normal(scale = sd, size = 6) #during
        error_z = np.random.normal(scale = sd, size = 2) #sequence
        error_c1 = np.random.normal(scale = sd, size = 2) #charging mode 1
        error_c2 = np.random.normal(scale = sd, size = 2) #charging mode 2
        error_c3 = np.random.normal(scale = sd, size = 2) #charging mode 3


        preferences = None
    else:
        p_st_e = {'F': parameters['p_st_e_f'],'M':parameters['p_st_e_m'],'R': parameters['p_st_e_r']}
        p_st_l = {'F': parameters['p_st_l_f'],'M': parameters['p_st_l_m'],'R':parameters['p_st_l_r']}
        p_dur_s = {'F': parameters['p_dur_s_f'],'M': parameters['p_dur_s_m'],'R': parameters['p_dur_s_r']}
        p_dur_l = {'F': parameters['p_dur_l_f'],'M': parameters['p_dur_l_m'],'R': parameters['p_dur_l_r']}

        p_t = parameters['p_t']

        error_w = parameters['error_w']
        error_x = parameters['error_x']
        error_d = parameters['error_d']
        error_z = parameters['error_z']
        error_c1 = parameters['error_c1']
        error_c2 = parameters['error_c2']
        error_c3 = parameters['error_c3']


        pref_st = {1: parameters['d_st_h'],2: parameters['d_st_w'],3: parameters['d_st_edu'],4: parameters['d_st_s'],
        5: parameters['d_st_er'],6: parameters['d_st_b'],8: parameters['d_st_l'], 9: parameters['d_st_es']}

        pref_dur = {1: parameters['d_dur_h'],2: parameters['d_dur_w'],3: parameters['d_dur_edu'],4: parameters['d_dur_s'],
        5: parameters['d_dur_er'],6: parameters['d_dur_b'],8: parameters['d_dur_l'], 9: parameters['d_dur_es']}

        preferences = [pref_st, pref_dur]



    if deterministic:
        EV_error = 0
    else:
        EV_error =np.random.gumbel()
    
   
    #dictionaries containing data used to define decision variable, constraits and the objective function for the cplex optimization
    keys, location, feasible_start, feasible_end, des_start, des_duration, flex_early, flex_late, flex_short, flex_long, group, mode, act_id,charger_access= create_dicts(df, preferences)
    

    m = Model()
    m.parameters.optimalitytarget = 3 #global optimum for non-convex models

    #decision variables 
    x = m.continuous_var_dict(keys, lb = 0, name = 'x') #start time 
    z = m.binary_var_matrix(keys, keys, name = 'z') #activity sequence indicator - binary variable that is 1 if acitvity b is scheduled immediately after acitivty a, where a!=b
    d = m.continuous_var_dict(keys, lb = 0, name = 'd') #activity duration
    w = m.binary_var_dict(keys, name = 'w') #indicator of  activity choice - binary variable that is 1 if activity a is selected in the schedule and  0 otherwise
    tt = m.continuous_var_dict(keys, lb = 0, name = 'tt') #travel time 
    tc = m.continuous_var_dict(keys, lb = 0, name = 'tc') #travel cost
    peak_TOU= m.continuous_var_dict(keys, lb = 1, name = 'pk') #parking cost

    # decision variables for charging
    charging = m.binary_var_dict(keys, name='charging') #binary variables that take the value of 1 if charging is scheduled at activity a and 0 otherwise
    charge_time_type1=m.continuous_var_dict(keys,lb=0,name="charge_time_slow") # continuous variables that represent the duration of charging at activity a using a slow charger
    charge_time_type2=m.continuous_var_dict(keys,lb=0,name="charge_time_fast") #charging duration level2
    charge_time_type3=m.continuous_var_dict(keys,lb=0,name="charge_time_rapid") #charging duration level3
    #charger types
    charger_level1 = m.binary_var_dict(keys, name='charging_type_slow')  # binary variables that take the value of 1 if level 1 charger is used for charging at activity a and 0 otherwise
    charger_level2 = m.binary_var_dict(keys, name='charging_type_fast') # binary variables that take the value of 1 if level 2 charger is used for charging at activity a and 0 otherwise
    charger_level3 = m.binary_var_dict(keys, name='charging_type_rapid') # binary variables that take the value of 1 if level 3 charger is used for charging at activity a and 0 otherwise
    #soc
    soc = m.continuous_var_dict(keys, lb=0.00, ub=1.00, name='soc_battery')  # SOC at the start of activity a
    charging_duration = m.continuous_var_dict(keys, lb=0, name='charging_duration') #charging duration
    md_car = m.binary_var_dict(keys, name = 'md') #mode of transportation (availability) variable that is 1 if private mode m is available for acitivity a and 0 otherwise
    
    max_charging=8  # maximum charging duration 8 hours

    min_slow=0.5    # minimum charging duration 0.5 hours for slow charger
    min_fast=0.2    # minimum charging duration 0.2 hours for fast charger
    min_rapid=0.1   # minimum charging duration 0.1 hours for rapid charger

    charging_rate_slow=0.08 # 7kw/60 8 percent per hour SOC increase
    charging_rate_fast=0.36  # 22kw/60 25 percent per hour SOC increase
    charging_rate_rapid=0.80 # 50/60 percent per hour SOC increase

    energy_consumption_rate=0.2   #0.2 kwh per km energy consumption rate per km                          
    Soc_FULL=1.00 # 100% SOC
    battery_cap_60=60 #kwh example value
    


    # Update SOC during acitity charging and travel consumptions

    for a in keys:
    
                # charging SOC constraints
                m.add_constraint(soc[a]>=0.1) # charging constraint 1
                #  SOC capacity constraints
                m.add_constraint(soc[a]+charge_time_type1[a]*charging_rate_slow+charge_time_type2[a]*charging_rate_fast+charge_time_type3[a]*charging_rate_rapid<=Soc_FULL) # SOC constraint 9 ensure charging duration is less than the time for full charging for the battery

                # sufficient SOC to complete a subsequent trip           
                m.add_constraints(soc[a]+charge_time_type1[a]*charging_rate_slow+charge_time_type2[a]*charging_rate_fast+charge_time_type3[a]*charging_rate_rapid>=z[a,b]*energy_consumption_rate*distances[location[a]][location[b]]/battery_cap_60 for b in keys) # charging constraint 6 ensure SOC sufficient to complete the subsequent trip
                
                #ensure SOC consistency between acitivities
                m.add_constraints(Soc_FULL*(z[a,b]-1)<=soc[a]+charge_time_type1[a]*charging_rate_slow+charge_time_type2[a]*charging_rate_fast+charge_time_type3[a]*charging_rate_rapid-z[a,b]*energy_consumption_rate*distances[location[a]][location[b]]/battery_cap_60-soc[b] for b in keys) # SOC constraint 7 ensure SOC consistency between acitivities
                m.add_constraints(Soc_FULL*(1-z[a,b])>=soc[a]+charge_time_type1[a]*charging_rate_slow+charge_time_type2[a]*charging_rate_fast+charge_time_type3[a]*charging_rate_rapid-z[a,b]*energy_consumption_rate*distances[location[a]][location[b]]/battery_cap_60-soc[b] for b in keys) # SOC constraint 8 ensure SOC consistency between acitivities
                
                #charging duration constraints to ensure that the charging duration is larger than 0 when charging is scheduled
                # ensure that the charging duration is no longer to 0 when charging is scheduled.
                m.add_constraint(min_slow*charger_level1[a]<= charge_time_type1[a])# similar to the contrain w[a]<=d[a] below charging constraint 5 ensure charging duration is larger than 0 when charging is sheduled, Driver must charge their vehicles ---must charger when charging is scheduled
                m.add_constraint(min_fast*charger_level2[a]<= charge_time_type2[a])# charging constraint 5 ensure charging duration is larger than 0 when charging is sheduled, Driver must charge their vehicles ---must charger when charging is scheduled
                m.add_constraint(min_rapid*charger_level3[a]<= charge_time_type3[a])# charging constraint 5 ensure charging duration is larger than 0 when charging is sheduled, Driver must charge their vehicles ---must charger when charging is scheduled

                m.add_constraint(charge_time_type1[a]<=d[a]) #charging constraint 10 This ensures that the charge time for type 1 does not exceed the total charging duration available for charger
                m.add_constraint(charge_time_type1[a]<=max_charging*charger_level1[a]) #charging constraint 11  This constraint uses the big-M method to control the relationship between the charger type and the charge time type 1. If charger_type[a] is 0 (indicating charger type 0), then charge_time_type1[a] must be 0 because 0 * M = 0. If charger_type[a] is 1, charge_time_type1[a] can be any value up to M (which should be set to a reasonable upper bound or the maximum charge time).
                m.add_constraint(charge_time_type2[a]<=d[a]) #charging constraint 12 Similar to constraint 10, this ensures that the charge time for type 2 does not exceed the total charging duration.
                m.add_constraint(charge_time_type2[a]<=max_charging*charger_level2[a]) #charging constraint 13 This also uses the big-M method, but inversely related to charger_type[a]. If charger_type[a] is 1, then charge_time_type2[a] must be 0 because 1 - 1 = 0, and 0 * M = 0. If charger_type[a] is 0, then charge_time_type2[a] can be up to M.
                m.add_constraint(charge_time_type3[a]<=d[a]) #charging constraint 14 Similar to constraint 10, this ensures that the charge time for type 3 does not exceed the total charging duration.
                m.add_constraint(charge_time_type3[a]<=charger_level3[a]) #charging constraint 15 This also uses the big-M method, but inversely related to charger_type[a]. If charger_type[a] is 1, then charge_time_type3[a] must be 0 because 1 - 1 = 0, and 0 * M = 0. If charger_type[a] is 0, then charge_time_type3[a] can be up to M.
                # m.add_constraint(charge_time_type1[a]+charge_time_type2[a]+charge_time_type3[a]==charging_duration[a]) #charging constraint 14 This constraint ensures that the total charging time is exactly the sum of the times spent charging using type 1 and type 2 chargers.
                
                m.add_constraint(charger_level1[a]+charger_level2[a]+charger_level3[a]==charging[a]) #charging constraint 15 This constraint ensures that the charging either is scheduled using level 1, level2, and level 3 or not schduled.
                m.add_constraint(charging[a]<=w[a]) #charging constraint 16 This constraint ensures that charging is scheduled only at scheduled activity locations

                if group[a] in ["rapid"]: #charging at service stations constraint 10
                    m.add_constraint(charging[a]==w[a])#ensure that charging is scheduled at service stations when the service station activity is selected.
                    m.add_constraint(charge_time_type3[a]==d[a]) #ensure that  no level 3 is used for recharger and level 3 charger is not available at other locations
                    m.add_constraint(charger_level3[a]==charging[a]) # the usage of charger levels
                   
                else:
                    m.add_constraint(charger_level3[a]==0) #ensure that  no level 3 is used for recharger and level 3 charger is not available at other locations
                # only slow charger available at home
                if group[a] in ["home", "dawn", "dusk"]:
                   m.add_constraint(charger_level2[a]==0)
                #   add precedence constraints for escort activities to ensure that the escort activity is scheduled after the dawn activity if the escort activity is selected
               
    
    m.add_constraints(charging[a] == 0 for a in keys if charger_access[a] =='NO') #no charging at no charger locations at home, dawn, dusk and escort Constraint 11

  
    error_w = m.piecewise(0, [(k,error_w[k]) for k in [0,1]], 0)#choice
    error_c1 = m.piecewise(0, [(k,error_c1[k]) for k in [0,1]], 0)#charging constraint 14
    error_c2 = m.piecewise(0, [(k,error_c2[k]) for k in [0,1]], 0)#charging constraint 
    error_c3 = m.piecewise(0, [(k,error_c3[k]) for k in [0,1]], 0)#charging constraint 14
    error_z = m.piecewise(0, [(k,error_z[k]) for k in [0,1]], 0)#sequence
    error_x = m.piecewise(0, [(a, error_x[b]) for a,b in zip(np.arange(0, 24, 6), np.arange(4))], 0)#start time
    error_d = m.piecewise(0, [(a, error_d[b]) for a,b in zip([0, 1, 3, 8, 12, 16], np.arange(6))], error_d[-1]) #duration
    # no error terms associated with travel cost
    #constraints 
    TOU=m.piecewise(0, [(a,b) for a,b in zip([0, 8, 12, 18, 21, 24],[1.0, 1.5, 2.5, 1.5, 1.0, 1.0])], 0) #time of use tariff
    # m.add_constraint(peak_factor==10*TOU(x[a])) #peak factor for peak charging
    m.add_constraint(peak_TOU[a]==TOU(x[a]+2.5)) #peak factor for peak charging

    for a in keys:
        ct_sequence = m.add_constraints(z[a,b] + z[b,a] <= 1 for b in keys if b != a) #2.23 ensure a.b follow each other once
        ct_sequence_dawn = m.add_constraint(z[a,'dawn'] == 0 ) #2.24
        ct_sequence_dusk = m.add_constraint(z['dusk',a] == 0 ) #2.24
        ct_sameact = m.add_constraint(z[a,a] == 0)
        ct_times_inf = m.add_constraints(x[a] + d[a] + tt[a] - x[b] >= (z[a,b]-1)*period for b in keys)#27  time consistency
        ct_times_sup = m.add_constraints(x[a] + d[a] + tt[a] - x[b] <= (1-z[a,b])*period for b in keys)#28  time consistency between two activities
        ct_traveltime = m.add_constraint(tt[a] == m.sum(z[a,b]*travel_times[mode[a]][location[a]][location[b]] for b in keys))  #? why it is sum ensuring one
        ct_travelcost = m.add_constraint(tc[a] == m.sum(z[a,b]*costs_travel[mode[a]]*distances[location[a]][location[b]] for b in keys))#????????????????

        if group[a] in ["home", "dawn", "dusk"]:
            ct_car_home = m.add_constraint(md_car[a] == 1) #private mode is available for activities starting from home

       
        if mode[a] == "driving":
            ct_car_avail = m.add_constraint(w[a] <= md_car[a])#only allow private mode to take place

        ct_car_consist_neg = m.add_constraints(md_car[a] >=  md_car[b] + z[a,b] - 1 for b in keys)
        ct_car_consist_pos = m.add_constraints(md_car[b] >=  md_car[a] + z[a,b] - 1 for b in keys)

        ct_nullduration = m.add_constraint(w[a]*0.2 <= d[a])# ??????simplified using 1 as the minimum duration
        ct_noactivity = m.add_constraint(d[a] <= w[a]*period)#22
        ct_tw_start = m.add_constraint(x[a] >= feasible_start[a])#34
        ct_tw_end = m.add_constraint(x[a] + d[a] <= feasible_end[a])#35

        #if not mtmc: #no duplicates in MTMC !
        ct_duplicates = m.add_constraint(m.sum(w[b] for b in keys if group[b] == group[a])<=1)#29

        if a != 'dawn':
            ct_predecessor = m.add_constraint(m.sum(z[b,a] for b in keys if b !=a) == w[a])#25
        if a != 'dusk':
            ct_successor = m.add_constraint(m.sum(z[a,b] for b in keys if b !=a) == w[a] )#26

    ct_period = m.add_constraint(m.sum(d[a] + tt[a] for a in keys)==period)#18
    ct_startdawn = m.add_constraint(x['dawn'] == 0)
    # charging Change
    # soc at the beginning of the day  charging constraint 15
    ct_startdawn_soc = m.add_constraint(soc['dawn'] ==initial_soc) #initial soc at the beginning of the day is the same as the soc at the end of the previous day for example we can call it soc at the end of the previous day
    ct_enddusk = m.add_constraint(x['dusk']+ d['dusk'] == period)

    #objective function// large charge penalty leads to the selection of more activities as it maixmizes the utility 2.
    m.maximize(m.sum(w[a]*(
    #participation
    # soc_influence[soc[a]]*act_participations_const[act_id[a]] #participation utility
    adjustment_tuning_acti*act_participations_const[act_id[a]] #participation utility
    #penalties start time
    +flex_early[a] * m.max(des_start[a]-x[a], 0)
    +flex_late[a] * m.max(x[a]-des_start[a], 0)

    #penalties duration
    +flex_short[a] * m.max(des_duration[a]-d[a], 0)
    +flex_long[a] * m.max(d[a] - des_duration[a], 0)

    #penalties travel (time and cost)  #update the charging travel time and cost
    +(p_t) * (tt[a]) #travel time
    +(p_t_cost[mode[a]])*tc[a]

    #activity cost  
    +(p_act_cost)*costs_activity[act_id[a]]
    
    #charging inconvenience penalty
    +adjustment_tuning_charge*inconvenience_charge_const[act_id[a]]*(charger_level1[a]+charger_level2[a]+charger_level3[a]) #charging inconvenience penalty  
    
    #participation constant for activity and charging at low SOC; adjusted the constant to reflect the impact of SOC on participation
    +p_lowsoc_cost*m.max(soc_threshold-soc[a],0) # penalty for charging at low SOC soc is negative so penalty is positive, if not adding charging[a] the penalty is applied to all activities
   
    #penalty for not fully charged SOC
    +p_soc_charged*(charging_rate_slow*charge_time_type1[a]+charging_rate_fast*charge_time_type2[a]+charging_rate_rapid*charge_time_type3[a])
    
    # penalty for charging cost charging[a]=charge_level1[a]+charge_level2[a]+charge_level3[a]
    +p_charge_cost*(costs_charging_type1[act_id[a]]*charge_time_type1[a] + costs_charging_type2[act_id[a]]*charge_time_type2[a] + costs_charging_type3[act_id[a]]*charge_time_type3[a])
    +p_charge_cost*(peak_TOU[a]-1)*(costs_charging_type1[act_id[a]]*8 + costs_charging_type2[act_id[a]]*2.5 + costs_charging_type3[act_id[a]]*0.8)
    )   
    #error terms
    + error_w(w[a])
    + error_x(x[a])
    + error_d(d[a])
    + error_c1(charger_level1[a])  #charging error
    + error_c2(charger_level2[a])  #charging error
    + error_c3(charger_level3[a])  #charging error
   

    + m.sum(error_z(z[a,b]) for b in keys) for a in keys)+ EV_error) 


   
    solution = m.solve()
    #m.print_information()
    figure = None
    solution_df = None
    mode_figure = None 
    figure_soc= None    
    try:
        solution_value = solution.get_objective_value()

    except:
        solution_value = None
        print('Could not find a solution - see details')
        print(m.solve_details)
        print('------------------')
        return None

    solution_df = cplex_to_df(w, x, d, tt, md_car, mode, keys, act_id, location,charger_access,soc,charging,charging_duration, charge_time_type1, charge_time_type2, charge_time_type3, charger_level1, charger_level2, charger_level3) #transform into pandas dataframe: add charging sessions charing and charging during for each activities that are decision variables

       
    print(solution_df)
    if plot_mode:
        mode_figure = plot_mode(solution_df)
    
    return solution_df, figure, solution_value, mode_figure
