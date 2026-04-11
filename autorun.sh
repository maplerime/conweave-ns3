#!/bin/bash

cecho(){  # source: https://stackoverflow.com/a/53463162/2886168
    RED="\033[0;31m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    NC="\033[0m" # No Color
    printf "${!1}${2} ${NC}\n"
}

cecho "GREEN" "Running ECMP, Hybrid and Inflex MoE Experiments with BG Conflicts (Hybrid/Inflex: 4 fecmp_bg levels each)"

TOPOLOGY="topo_1280_400G_400G_OS1" # 5pods, 1280 hosts, all 400G
BANDWIDTH="400" # NIC bandwidth 400Gbps
NETLOAD="50" # network load 50%
RUNTIME="0.01" # 0.05 seconds

cecho "YELLOW" "\n----------------------------------"
cecho "YELLOW" "TOPOLOGY: ${TOPOLOGY}"
cecho "YELLOW" "NETWORK LOAD: ${NETLOAD}"
cecho "YELLOW" "TIME: ${RUNTIME}"
cecho "YELLOW" "BG CONFLICT: 2% (16 SD pairs, 12 flows/pair for 192 BG)"
cecho "YELLOW" "AUTO-STOP: Enabled (kills when 16384 MoE flows complete)"
cecho "YELLOW" "----------------------------------\n"

# Array to store PIDs of simulations
declare -a SIM_PIDS
declare -a SIM_DIRS
declare -a SIM_NAMES

# Function to monitor and kill simulation when MoE flows complete
monitor_simulation() {
    local pid=$1
    local output_dir=$2
    local name=$3
    local target_moe=16384

    # Wait for FCT file to be created
    while [ ! -f "${output_dir}/${output_dir##*/}_out_fct.txt" ]; do
        sleep 0.1
        # Check if process is still running
        if ! kill -0 $pid 2>/dev/null; then
            return
        fi
    done

    local fct_file="${output_dir}/${output_dir##*/}_out_fct.txt"

    # Monitor FCT file completion
    while true; do
        # Check if process is still running
        if ! kill -0 $pid 2>/dev/null; then
            break
        fi

        # Count completed MoE flows (excluding background flows)
        # MoE flows have tag=2, BG flows have tag=1
        # We count lines in FCT file (includes all flows, need to subtract BG)
        local total_flows=$(wc -l < "$fct_file" 2>/dev/null || echo "0")

        # Extract number of background flows from directory name or use a fixed mapping
        local bg_count=0
        if [[ "$name" == *"64fecmp"* ]]; then
            bg_count=64
        elif [[ "$name" == *"128fecmp"* ]]; then
            bg_count=128
        elif [[ "$name" == *"192fecmp"* ]]; then
            bg_count=192
        fi

        local moe_flows=$total_flows

        # If we have background flows, we need to subtract them
        # But actually, we should check if MoE flows (16384) are complete
        # FCT file contains all flows, so when total >= 16384 + bg_count, MoE is done
        local expected=$((target_moe + bg_count))

        if [ "$total_flows" -ge "$expected" ]; then
            cecho "GREEN" ">>> $name: MoE flows complete ($total_flows/$expected), killing simulation..."
            kill $pid 2>/dev/null
            break
        fi

        sleep 0.5
    done
}

# Function to run simulation with monitoring
run_simulation() {
    local lb_mode=$1
    local fecmp_bg=$2
    local flow_file=$3
    local name="$lb_mode${fecmp_bg:+_$fecmp_bg}"

    cecho "YELLOW" "Running: $name, flow: $flow_file"

    # Run simulation in background
    python3 run.py --lb $lb_mode --pfc 1 --irn 1 --simul_time ${RUNTIME} --netload ${NETLOAD} --topo ${TOPOLOGY} --bw ${BANDWIDTH} --flow_file $flow_file --fecmp_bg $fecmp_bg 2>&1 > /dev/null &
    local sim_pid=$!

    # Get the output directory (most recent one)
    sleep 1  # Wait for directory to be created
    local output_dir=$(ls -td mix/output/*/ | head -1)
    output_dir="mix/output/${output_dir}"

    # Start monitoring in background
    monitor_simulation $sim_pid "$output_dir" "$name" &

    SIM_PIDS+=($sim_pid)
    SIM_DIRS+=("$output_dir")
    SIM_NAMES+=("$name")
}

# ========== Pure ECMP mode ==========
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Run Pure ECMP experiment"
cecho "GREEN" "=========================================="

FLOW_FILE="moe_1280group_256to8_8round_8KB_bg_conflict2_192fecmp.txt"
run_simulation "fecmp" "" "$FLOW_FILE"

# ========== Hybrid mode with different fecmp_bg levels ==========
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Run Hybrid experiments"
cecho "GREEN" "=========================================="

# fecmp_bg = 0 (all drill)
FLOW_FILE="moe_1280group_256to8_8round_8KB.txt"
run_simulation "hybrid" "0" "$FLOW_FILE"

# fecmp_bg = 64
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_64fecmp.txt"
run_simulation "hybrid" "64" "$FLOW_FILE"

# fecmp_bg = 128
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_128fecmp.txt"
run_simulation "hybrid" "128" "$FLOW_FILE"

# fecmp_bg = 192 (all fecmp)
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_192fecmp.txt"
run_simulation "hybrid" "192" "$FLOW_FILE"

# ========== Inflex mode with different fecmp_bg levels ==========
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Run Inflex experiments"
cecho "GREEN" "=========================================="

# fecmp_bg = 0 (all drill)
FLOW_FILE="moe_1280group_256to8_8round_8KB.txt"
run_simulation "inflex" "0" "$FLOW_FILE"

# fecmp_bg = 64
FLOW_FILE="moe_1280group_256to8_8round_8KB_bg_conflict2_64fecmp.txt"
run_simulation "inflex" "64" "$FLOW_FILE"

# fecmp_bg = 128
FLOW_FILE="moe_1280group_256to8_8round_8KB_bg_conflict2_128fecmp.txt"
run_simulation "inflex" "128" "$FLOW_FILE"

# fecmp_bg = 192 (all fecmp)
FLOW_FILE="moe_1280group_256to8_8round_8KB_bg_conflict2_192fecmp.txt"
run_simulation "inflex" "192" "$FLOW_FILE"

# Wait for all simulations to complete
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Waiting for all simulations to complete..."
cecho "GREEN" "=========================================="

for pid in "${SIM_PIDS[@]}"; do
    wait $pid
done

cecho "GREEN" "\n=========================================="
cecho "GREEN" "All experiments completed!"
cecho "GREEN" "=========================================="
