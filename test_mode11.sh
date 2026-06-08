#!/bin/bash

# Test script for lb_mode=11 (ECMP-Conweave)

echo "=================================="
echo "Testing ECMP-Conweave (mode 11)"
echo "=================================="

TOPOLOGY="fat_k16_5pods_256perPod_100G_400G_OS1"
FLOW_FILE="test_mode11_100flows.txt"
NETLOAD="50"
RUNTIME="0.1"

echo ""
echo "Flow file: ${FLOW_FILE}"
echo "Topology: ${TOPOLOGY}"
echo "Network load: ${NETLOAD}%"
echo "Runtime: ${RUNTIME}s"
echo ""

echo "Starting simulation..."
python3 run.py --lb ecmp-conweave --pfc 1 --irn 1 --simul_time ${RUNTIME} \
    --netload ${NETLOAD} --topo ${TOPOLOGY} --flow_file ${FLOW_FILE} 2>&1 | tee mode11_test.log

echo ""
echo "=================================="
echo "Checking results..."
echo "=================================="

# Find the output directory
OUTPUT_DIR=$(ls -td mix/output/*/ | head -1)
echo "Output directory: ${OUTPUT_DIR}"

# Check flow completion
FCT_FILE="${OUTPUT_DIR}*_out_fct.txt"
if [ -f $(echo ${FCT_FILE}) ]; then
    FLOW_COUNT=$(wc -l < $(echo ${FCT_FILE}))
    echo "Flows completed: ${FLOW_COUNT}"
else
    echo "No FCT file found!"
fi

# Check for mode 11 debug output
echo ""
echo "=== Mode 11 Debug Output ==="
grep "\[lb_mode=11\]" mode11_test.log | tail -5

echo ""
echo "=== Checking config.log ==="
cat ${OUTPUT_DIR}/config.log | grep -A 20 "=== INFLEX"

echo ""
echo "=================================="
echo "Test complete!"
echo "=================================="
