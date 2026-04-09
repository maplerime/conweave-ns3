#!/usr/bin/env python3
"""
Generate MoE flow file for 1280-node 5-pod topology
- Expert groups: 256 groups (randomly selected)
- Each source group ONCE selects 8 receiver groups (from expert groups, excluding self)
- 8 rounds: round 0 uses receiver[0], round 1 uses receiver[1], etc.
- Each round: all 256 sources send to their designated receiver for that round
"""

import random
import argparse

parser = argparse.ArgumentParser(description='Generate MoE flow file')
parser.add_argument('--fecmp_bg', type=int, default=None, help='Number of 8MB background flows')
args = parser.parse_args()

FECMP_BG_VALUES = [0, 64, 128, 192] if args.fecmp_bg is None else [args.fecmp_bg]

TOTAL_NODES = 1280
NUM_PODS = 5
NODES_PER_POD = TOTAL_NODES // NUM_PODS
GROUP_SIZE = 4
GROUPS_PER_POD = NODES_PER_POD // GROUP_SIZE
NUM_GROUPS = GROUPS_PER_POD * NUM_PODS

EXPERT_GROUPS = 256
RECEIVER_GROUPS_PER_SOURCE = 8
ROUNDS = 8
MOE_FLOW_SIZE = 8192
BG_FLOW_SIZE = 8 * 1024 * 1024

PG_VALUE = 3
TAG_EXPERT = 2
TAG_BACKGROUND = 1
BG_START_TIME = 2.0

random.seed(42)

def get_group_nodes(group_id):
    """Return list of 4 node IDs for the given group"""
    pod = group_id // GROUPS_PER_POD
    group_in_pod = group_id % GROUPS_PER_POD
    start_node = pod * NODES_PER_POD + group_in_pod * GROUP_SIZE
    return [start_node + i for i in range(GROUP_SIZE)]

# Select expert groups
all_groups = list(range(NUM_GROUPS))
expert_group_ids = sorted(random.sample(all_groups, EXPERT_GROUPS))
remaining_groups = sorted([g for g in all_groups if g not in expert_group_ids])

print(f"Expert Groups: {len(expert_group_ids)} groups")
print(f"Background Groups: {len(remaining_groups)} groups")
print()

expert_groups_nodes = []
expert_groups_ids_list = []
for g in expert_group_ids:
    expert_groups_nodes.append(get_group_nodes(g))
    expert_groups_ids_list.append(g)

# Background flows
# From remaining 64 groups, select nodes where src%4 == dst%4
# Try to use as many different groups as possible
bg_flow_pools = {0: [], 64: [], 128: [], 192: []}

bg_groups_nodes = [(g, get_group_nodes(g)) for g in remaining_groups]
num_bg_groups = len(bg_groups_nodes)

# Generate all possible background flows (src%4 == dst%4)
all_bg_flows = []
for src_group_id, src_group in bg_groups_nodes:
    for dst_group_id, dst_group in bg_groups_nodes:
        if src_group_id == dst_group_id:
            continue
        for node_idx in range(GROUP_SIZE):
            src = src_group[node_idx]
            dst = dst_group[node_idx]
            all_bg_flows.append((src_group_id, dst_group_id, src, dst))

# Shuffle to get diverse selection
random.shuffle(all_bg_flows)

# Take first 192 flows
for i in range(min(192, len(all_bg_flows))):
    src_group_id, dst_group_id, src, dst = all_bg_flows[i]
    flow_entry = f"{src} {dst} {PG_VALUE} {BG_FLOW_SIZE} {BG_START_TIME:.9f} {TAG_BACKGROUND}\n"
    bg_flow_pools[192].append(flow_entry)

# Create smaller pools
bg_flow_pools[128] = bg_flow_pools[192][:128]
bg_flow_pools[64] = bg_flow_pools[192][:64]

print(f"Background flow pools: 0/64/128/192 flows")
print(f"BG groups used: {len(set(f[0] for f in all_bg_flows[:192]))} source groups")
print()

# Each source group selects 8 receiver groups (one-time selection)
source_to_receiver_groups = {}
for src_group_id in expert_group_ids:
    available = [g for g in expert_group_ids if g != src_group_id]
    assigned = sorted(random.sample(available, RECEIVER_GROUPS_PER_SOURCE))
    source_to_receiver_groups[src_group_id] = assigned

# Generate MoE flows
# 4 nodes take turns to send, 8 rounds total
# Each source-target pair: node0 rounds 0,1; node1 rounds 2,3; node2 rounds 4,5; node3 rounds 6,7
# Total: 256 sources × 8 targets × 8 rounds = 16384 flows
moe_lines = []

for src_idx, src_group_id in enumerate(expert_group_ids):
    src_group_nodes = expert_groups_nodes[src_idx]

    # Get the 8 target groups for this source
    target_group_ids = source_to_receiver_groups[src_group_id]

    for target_group_id in target_group_ids:
        target_group_nodes = get_group_nodes(target_group_id)

        # 8 rounds: 4 nodes take turns (each node does 2 rounds)
        for round_id in range(ROUNDS):
            node_idx = (round_id // 2) % GROUP_SIZE  # rounds 0,1->node0; 2,3->node1; 4,5->node2; 6,7->node3
            src = src_group_nodes[node_idx]
            dst = target_group_nodes[node_idx]
            moe_lines.append(f"{src} {dst} {PG_VALUE} {MOE_FLOW_SIZE} {BG_START_TIME:.9f} {TAG_EXPERT}\n")

total_moe_flows = len(moe_lines)
print(f"Total MoE flows: {total_moe_flows} ({total_moe_flows * MOE_FLOW_SIZE / 1024:.1f} KB)")
print(f"Pattern: 256 sources × 8 targets × 4 nodes × 8 rounds = {total_moe_flows} flows")
print()

# Generate files
for FECMP_BG_COUNT in FECMP_BG_VALUES:
    bg_lines = bg_flow_pools[FECMP_BG_COUNT]
    total_bg_flows = len(bg_lines)

    if FECMP_BG_COUNT > 0:
        output_file = f"moe_1280group_256to8_8round_8KB_hybrid_{FECMP_BG_COUNT}fecmp.txt"
    else:
        output_file = "moe_1280group_256to8_8round_8KB.txt"

    with open(output_file, "w") as f:
        f.write(f"{total_moe_flows + total_bg_flows}\n")
        for line in bg_lines:
            f.write(line)
        for line in moe_lines:
            f.write(line)

    print(f"Generated: {output_file} ({total_moe_flows} MoE + {total_bg_flows} BG flows)")
