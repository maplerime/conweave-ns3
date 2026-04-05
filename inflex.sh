#!/bin/bash

cecho(){  # source: https://stackoverflow.com/a/53463162/2886168
    RED="\033[0;31m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    # ... ADD MORE COLORS
    NC="\033[0m" # No Color

    printf "${!1}${2} ${NC}\n"
}

cecho "GREEN" "Running Load Balancing Experiments (ECMP, DRILL, Conweave, Inflex)"

TOPOLOGY="fat_k16_5pods_256perPod_100G_400G_OS1" # k=16, 5pods, 256/pod, 1280 hosts
NETLOAD="50" # network load 50%
RUNTIME="1.0" # 1000ms to match flow file T_1000ms
FLOW_FILE="L_2.00_CDF_AliStorage2019_N_320_T_1000ms_B_100_flow.txt"

cecho "YELLOW" "\n----------------------------------"
cecho "YELLOW" "TOPOLOGY: ${TOPOLOGY}"
cecho "YELLOW" "NETWORK LOAD: ${NETLOAD}"
cecho "YELLOW" "TIME: ${RUNTIME}"
cecho "YELLOW" "FLOW FILE: ${FLOW_FILE}"
cecho "YELLOW" "----------------------------------\n"

# ECMP mode
cecho "GREEN" "Run ECMP experiment..."
cecho "YELLOW" "Running: lb=fecmp, flow: ${FLOW_FILE}"
python3 run.py --lb fecmp --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1

# DRILL mode
cecho "GREEN" "\n----------------------------------"
cecho "GREEN" "Run DRILL experiment..."
cecho "YELLOW" "Running: lb=drill, flow: ${FLOW_FILE}"
python3 run.py --lb drill --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1

# Conweave mode
cecho "GREEN" "\n----------------------------------"
cecho "GREEN" "Run Conweave experiment..."
cecho "YELLOW" "Running: lb=conweave, flow: ${FLOW_FILE}"
python3 run.py --lb conweave --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1

# Inflex mode
cecho "GREEN" "\n----------------------------------"
cecho "GREEN" "Run Inflex experiment..."
cecho "YELLOW" "Running: lb=inflex, flow: ${FLOW_FILE}"
python3 run.py --lb inflex --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1

cecho "GREEN" "\nAll experiments launched in parallel!"
