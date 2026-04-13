#!/bin/bash

cecho(){
    RED="\033[0;31m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    NC="\033[0m"
    printf '%b%s %b\n' "${!1}" "${2}" "${NC}"
}

cecho "GREEN" "Testing Reorder Buffer with 2 Flows"
cecho "YELLOW" "Flow 1: 80KB, tag=1 (per-packet ECMP with reorder)"
cecho "YELLOW" "Flow 2: 8KB, tag=2 (DRILL)"
cecho "YELLOW" "----------------------------------"

TOPOLOGY="topo_1280_400G_400G_OS1"
BANDWIDTH="400"
NETLOAD="50"
RUNTIME="0.01"

FLOW_FILE="test_reorder_2flows.txt"

cecho "GREEN" "Running Hybrid mode..."
python3 run.py --lb hybrid --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file config/${FLOW_FILE} --fecmp_bg 0

cecho "GREEN" "----------------------------------"
cecho "GREEN" "Checking results..."
ls -la mix/output/*/out_fct.txt 2>/dev/null | tail -1 || echo "No FCT file found"

if [ -f mix/output/*/out_fct.txt ]; then
    cecho "GREEN" "Flow completion times:"
    head -20 mix/output/*/out_fct.txt
fi
