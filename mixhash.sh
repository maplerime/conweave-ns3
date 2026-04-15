#!/bin/bash

cecho(){  # source: https://stackoverflow.com/a/53463162/2886168
    RED="\033[0;31m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    NC="\033[0m" # No Color
    printf '%b%s %b\n' "${!1}" "${2}" "${NC}"
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
cecho "YELLOW" "AUTO-STOP: Disabled (auto-stop code commented out)"
cecho "YELLOW" "----------------------------------\n"

# Array to store PIDs of simulations
declare -a SIM_PIDS
# declare -a MONITOR_PIDS  # DISABLED: Auto-stop commented out

# Function to monitor and kill simulation when MoE flows complete
monitor_simulation() {
    local pid=$1
    local output_dir=$2
    local name=$3
    local target_moe=16384

    # Wait for FCT file to be created
    local fct_file="${output_dir}/${output_dir##*/}_out_fct.txt"
    while [ ! -f "$fct_file" ]; do
        sleep 0.2
        # Check if process is still running
        if ! kill -0 $pid 2>/dev/null; then
            echo "  [$name] Simulation process ended (FCT file not created)"
            return
        fi
    done

    echo "  [$name] FCT file created: $fct_file"

    # Monitor FCT file completion
    while true; do
        # Check if process is still running
        if ! kill -0 $pid 2>/dev/null; then
            echo "  [$name] Simulation process ended"
            break
        fi

        # Count completed flows (all flows in FCT file)
        local total_flows=$(wc -l < "$fct_file" 2>/dev/null || echo "0")

        # Extract number of background flows from name
        local bg_count=0
        if [[ "$name" == *"64fecmp"* ]] || [[ "$name" == *"64_"* ]]; then
            bg_count=64
        elif [[ "$name" == *"128fecmp"* ]] || [[ "$name" == *"128_"* ]]; then
            bg_count=128
        elif [[ "$name" == *"192fecmp"* ]] || [[ "$name" == *"192_"* ]]; then
            bg_count=192
        fi

        # Expected: MoE flows + background flows
        local expected=$((target_moe + bg_count))

        if [ "$total_flows" -ge "$expected" ]; then
            cecho "GREEN" ">>> [$name] Flows complete ($total_flows/$expected), killing PID=$pid..."
            kill $pid 2>/dev/null
            break
        fi

        # Progress update every 5 seconds
        if [ $((total_flows % 1000)) -eq 0 ] && [ "$total_flows" -gt 0 ]; then
            echo "  [$name] Progress: $total_flows/$expected flows"
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

    # Wait a bit for simulation to create output directory
    sleep 2

    # Find the most recent output directory
    local output_dir=$(ls -td mix/output/*/ 2>/dev/null | head -1)
    if [ -z "$output_dir" ]; then
        echo "  ERROR: Cannot find output directory for $name"
        return
    fi

    # Remove trailing slash and get directory ID
    output_dir="${output_dir%/}"

    echo "  [$name] Output dir: $output_dir, PID: $sim_pid"

    # Start monitoring in background
    # DISABLED: Auto-stop commented out
    # monitor_simulation $sim_pid "$output_dir" "$name" &
    # local mon_pid=$!

    SIM_PIDS+=($sim_pid)
    # MONITOR_PIDS+=($mon_pid)  # DISABLED: Auto-stop commented out
}

# ========== Hybrid mode with different fecmp_bg levels ==========
cecho "GREEN" "\n=========================================="
cecho "GREEN" "Run Hybrid experiments"
cecho "GREEN" "=========================================="

# fecmp_bg = 0 (all drill)
FLOW_FILE="moe_1280group_256to8_8round_8KB.txt"
run_simulation "hybrid" "0" "$FLOW_FILE"

# fecmp_bg = 0
FLOW_FILE="moe_1280group_256to8_8round_8KB.txt"
run_simulation "mixhash" "0" "$FLOW_FILE"

# fecmp_bg = 64
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_64fecmp.txt"
run_simulation "mixhash" "64" "$FLOW_FILE"

# fecmp_bg = 128
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_128fecmp.txt"
run_simulation "mixhash" "128" "$FLOW_FILE"

# fecmp_bg = 192 (all fecmp)
FLOW_FILE="moe_1280group_256to8_8round_8KB_hybrid_192fecmp.txt"
run_simulation "mixhash" "192" "$FLOW_FILE"

# Kill any remaining monitor processes
cleanup() {
    # DISABLED: Auto-stop commented out
    # for mon_pid in "${MONITOR_PIDS[@]}"; do
    #     kill $mon_pid 2>/dev/null
    # done
    :
}
trap cleanup EXIT INT TERM

for pid in "${SIM_PIDS[@]}"; do
    wait $pid
done

# Final cleanup
cleanup

cecho "GREEN" "\n=========================================="
cecho "GREEN" "All experiments completed!"
cecho "GREEN" "=========================================="
