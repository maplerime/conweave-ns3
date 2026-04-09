#!/bin/bash

cecho(){  # source: https://stackoverflow.com/a/53463162/2886168
    RED="\033[0;31m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    # ... ADD MORE COLORS
    NC="\033[0m" # No Color

    printf "${!1}${2} ${NC}\n"
}

cecho "GREEN" "Running Hybrid and Inflex MoE Experiments (4 different fecmp_bg levels each)"

TOPOLOGY="topo_1280_400G_400G_OS1" # 5pods, 1280 hosts, all 400G
BANDWIDTH="400" # NIC bandwidth 400Gbps
NETLOAD="50" # network load 50%
RUNTIME="0.05" # 0.05 seconds

cecho "YELLOW" "\n----------------------------------"
cecho "YELLOW" "TOPOLOGY: ${TOPOLOGY}"
cecho "YELLOW" "NETWORK LOAD: ${NETLOAD}"
cecho "YELLOW" "TIME: ${RUNTIME}"
cecho "YELLOW" "----------------------------------\n"

# ========== Hybrid mode with different fecmp_bg levels ==========
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Run Hybrid experiments"
cecho "GREEN" "=========================================="

# fecmp_bg = 0 (all drill)
FLOW_FILE="moe_1280group_256to8_8round_8KB.txt"
cecho "YELLOW" "Running: hybrid fecmp_bg=0 (all drill), flow: ${FLOW_FILE}"
python3 run.py --lb hybrid --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 0 2>&1 > /dev/null &

# fecmp_bg = 64
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_64fecmp.txt"
cecho "YELLOW" "Running: hybrid fecmp_bg=64, flow: ${FLOW_FILE}"
python3 run.py --lb hybrid --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 64 2>&1 > /dev/null &

# fecmp_bg = 128
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_128fecmp.txt"
cecho "YELLOW" "Running: hybrid fecmp_bg=128, flow: ${FLOW_FILE}"
python3 run.py --lb hybrid --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 128 2>&1 > /dev/null &

# fecmp_bg = 192 (all fecmp)
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_192fecmp.txt"
cecho "YELLOW" "Running: hybrid fecmp_bg=192 (all fecmp), flow: ${FLOW_FILE}"
python3 run.py --lb hybrid --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 192 2>&1 > /dev/null &

cecho "GREEN" "Waiting for Hybrid experiments to complete..."
wait

# ========== Inflex mode with different fecmp_bg levels ==========
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Run Inflex experiments"
cecho "GREEN" "=========================================="

# fecmp_bg = 0 (all drill)
FLOW_FILE="moe_1280group_256to8_8round_8KB.txt"
cecho "YELLOW" "Running: inflex fecmp_bg=0 (all drill), flow: ${FLOW_FILE}"
python3 run.py --lb inflex --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 0 2>&1 > /dev/null &

# fecmp_bg = 64
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_64fecmp.txt"
cecho "YELLOW" "Running: inflex fecmp_bg=64, flow: ${FLOW_FILE}"
python3 run.py --lb inflex --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 64 2>&1 > /dev/null &

# fecmp_bg = 128
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_128fecmp.txt"
cecho "YELLOW" "Running: inflex fecmp_bg=128, flow: ${FLOW_FILE}"
python3 run.py --lb inflex --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 128 2>&1 > /dev/null &

# fecmp_bg = 192 (all fecmp)
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_192fecmp.txt"
cecho "YELLOW" "Running: inflex fecmp_bg=192 (all fecmp), flow: ${FLOW_FILE}"
python3 run.py --lb inflex --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file ${FLOW_FILE} --fecmp_bg 192 2>&1 > /dev/null &

cecho "GREEN" "Waiting for Inflex experiments to complete..."
wait

cecho "GREEN" "\n=========================================="
cecho "GREEN" "All experiments completed!"
cecho "GREEN" "=========================================="
cecho "GREEN" "Modes: Hybrid (0/64/128/192), Inflex (0/64/128/192)"
