#!/bin/bash

cecho(){  # source: https://stackoverflow.com/a/53463162/2886168
    RED="\033[0;31m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    # ... ADD MORE COLORS
    NC="\033[0m" # No Color

    printf "${!1}${2} ${NC}\n"
}

cecho "GREEN" "Running RDMA Network Load Balancing Simulations (MoE fat-tree topology)"

TOPOLOGY="fat_k8_100G_OS2.5" # fat-tree k=8, OS=2.5, 320 hosts
FLOW_FILE="moe_expert_256to8_8round_8KB.txt" # pre-generated MoE flow file
NETLOAD="50" # network load 50%
RUNTIME="0.02" # 0.02 second (traffic generation) - adjusted for 12 rounds of MoE flows

cecho "YELLOW" "\n----------------------------------"
cecho "YELLOW" "TOPOLOGY: ${TOPOLOGY}"
cecho "YELLOW" "FLOW FILE: ${FLOW_FILE}"
cecho "YELLOW" "NETWORK LOAD: ${NETLOAD}"
cecho "YELLOW" "TIME: ${RUNTIME}"
cecho "YELLOW" "----------------------------------\n"

# Lossless RDMA
#cecho "GREEN" "Run Lossless RDMA experiments..."
#python3 run.py --lb fecmp --pfc 1 --irn 0 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} 2>&1 > /dev/null & 
#sleep 5
#python3 run.py --lb letflow --pfc 1 --irn 0 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} 2>&1 > /dev/null &
#sleep 0.1
#python3 run.py --lb conga --pfc 1 --irn 0 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} 2>&1 > /dev/null &
#sleep 0.1
#python3 run.py --lb conweave --pfc 1 --irn 0 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} 2>&1 > /dev/null &
#sleep 0.1
#python3 run.py --lb drill --pfc 1 --irn 0 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} 2>&1 > /dev/null &
#sleep 0.1

# IRN RDMA
cecho "GREEN" "Run IRN RDMA experiments..."
python3 run.py --lb fecmp --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 5
python3 run.py --lb letflow --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1
python3 run.py --lb conga --pfc 0 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1
python3 run.py --lb conweave --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1
python3 run.py --lb drill --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 > /dev/null &
sleep 0.1

cecho "GREEN" "Runing all in parallel. Check the processors running on background!"
