#!/usr/bin/env python3
"""
Generate MoE flow file for 1280-node 5-pod topology
- All flows use pg=3
- tag=1 for background flows (large flows, 8MB) → ECMP
- tag=2 for expert flows (small flows, 8KB) → other mode

Each group of 4 nodes acts like a multi-NIC host
Expert groups send to receiver groups with one-to-one node mapping by index
"""

import random
import argparse

# Parse arguments
parser = argparse.ArgumentParser(description='Generate MoE flow file with hybrid pg')
parser.add_argument('--fecmp_bg', type=int, default=None,
                    help='Number of 8MB background flows (0, 64, 128, 192). If not specified, generates all 4 files.')
args = parser.parse_args()

FECMP_BG_VALUES = [0, 64, 128, 192] if args.fecmp_bg is None else [args.fecmp_bg]

# Parameters
TOTAL_NODES = 1280
NUM_PODS = 5
NODES_PER_POD = TOTAL_NODES // NUM_PODS  # 256 hosts per pod

GROUP_SIZE = 4  # Each group has 4 nodes (within same pod)
GROUPS_PER_POD = NODES_PER_POD // GROUP_SIZE  # 64 groups per pod
NUM_GROUPS = GROUPS_PER_POD * NUM_PODS  # 320 groups total

EXPERT_GROUPS = 256      # Number of sender groups (1024 nodes)
RECEIVER_GROUPS = 8      # Number of receiver groups (32 nodes)
ROUNDS = 8               # Number of MoE rounds
MOE_FLOW_SIZE = 8192     # 8KB per flow
BG_FLOW_SIZE = 8 * 1024 * 1024  # 8MB per background flow

# New parameters
PG_VALUE = 3             # All flows use pg=3
TAG_EXPERT = 2           # Expert flow tag = other mode (small flows)
TAG_BACKGROUND = 1       # Background flow tag = ECMP (large flows)

# Timing
BG_START_TIME = 2.0      # Background flows start at 2.0s
ROUND_INTERVAL_US = 100  # 100us between rounds
ROUND_TIME_US = 200      # Time for each round to complete

random.seed(42)

# Helper: get all nodes in a group
def get_group_nodes(group_id):
    """Return list of 4 node IDs for the given group"""
    pod = group_id // GROUPS_PER_POD
    group_in_pod = group_id % GROUPS_PER_POD
    start_node = pod * NODES_PER_POD + group_in_pod * GROUP_SIZE
    return [start_node + i for i in range(GROUP_SIZE)]

# Select expert groups randomly from all groups
all_groups = list(range(NUM_GROUPS))
expert_group_ids = sorted(random.sample(all_groups, EXPERT_GROUPS))

remaining_groups = sorted([g for g in all_groups if g not in expert_group_ids])

# Select receiver groups randomly from remaining groups
receiver_group_ids = sorted(random.sample(remaining_groups, RECEIVER_GROUPS))

# Remove receiver groups from remaining groups (for background flows)
remaining_for_bg = sorted([g for g in remaining_groups if g not in receiver_group_ids])

print(f"Topology: {NUM_PODS} pods, {NODES_PER_POD} hosts per pod, {TOTAL_NODES} total hosts")
print(f"Group structure: {NUM_GROUPS} groups total ({GROUPS_PER_POD} groups per pod), {GROUP_SIZE} nodes per group")
print()
print(f"All flows use pg={PG_VALUE}, tag={TAG_EXPERT}(expert) or {TAG_BACKGROUND}(background)")
print(f"Expert Groups: {len(expert_group_ids)} groups")
print(f"Receiver Groups: {len(receiver_group_ids)} groups")
print(f"Background Groups: {len(remaining_for_bg)} groups")
print()

# Convert groups to node ID lists
expert_groups_nodes = []
for g in expert_group_ids:
    expert_groups_nodes.append(get_group_nodes(g))

receiver_groups_nodes = []
for g in receiver_group_ids:
    receiver_groups_nodes.append(get_group_nodes(g))

# Background nodes: from remaining groups only (not expert or receiver)
bg_nodes = []
for g in remaining_for_bg:
    bg_nodes.extend(get_group_nodes(g))

# ALL remaining nodes (for background flow src/dst selection)
# This includes expert nodes, receiver nodes, and background-only nodes
all_nodes = list(range(TOTAL_NODES))

print(f"Total nodes: {TOTAL_NODES}")
print(f"Expert sender nodes: {len(expert_group_ids) * GROUP_SIZE} (256 groups x 4)")
print(f"Receiver nodes: {len(receiver_group_ids) * GROUP_SIZE} (8 groups x 4)")
print(f"Background-only nodes: {len(bg_nodes)} ({len(remaining_for_bg)} groups x 4)")
print()

# Generate MoE flows: 8 rounds, each round experts send to ONE receiver group
# Use one-to-one mapping: expert_group[i][j] -> receiver_group[k][j]
moe_lines = []
moe_start_time = BG_START_TIME + 0.001  # Start 1ms after background

for round_id in range(ROUNDS):
    # Each round, each expert group sends to ALL receiver groups
    # For each pair, only 1 flow: expert_group[j] -> receiver_group[j]
    # j varies per round to distribute load across nodes
    node_idx = round_id % GROUP_SIZE  # Round 0 uses node 0, round 1 uses node 1, etc.

    for expert_group in expert_groups_nodes:
        for receiver_group in receiver_groups_nodes:
            # One-to-one mapping by index: expert_group[node_idx] -> receiver_group[node_idx]
            src = expert_group[node_idx]
            dst = receiver_group[node_idx]

            # Skip if src == dst (shouldn't happen with different groups)
            if src == dst:
                continue

            pg = PG_VALUE
            tag = TAG_EXPERT
            moe_lines.append(f"{src} {dst} {pg} {MOE_FLOW_SIZE} {moe_start_time:.9f} {tag}\n")

total_moe_flows = len(moe_lines)
moe_traffic = total_moe_flows * MOE_FLOW_SIZE

print(f"MoE flows per round: {total_moe_flows // ROUNDS}")
print(f"Total MoE flows: {total_moe_flows} ({moe_traffic / 1024:.1f} KB)")
print(f"Pattern: Each expert group -> all {RECEIVER_GROUPS} receiver groups, 1 flow per pair, {GROUP_SIZE} nodes rotate across rounds")
print()

# Generate background flow pool
# Source and destination are selected from remaining nodes (not expert or receiver groups)
# Only requirement: src != dst
MAX_BG_FLOWS = 192
bg_flow_pool = []

# Generate all possible (src, dst) pairs from bg_nodes where src != dst
# Then randomly select from them
all_node_pairs = []
for src in bg_nodes:
    for dst in bg_nodes:
        if src != dst:
            all_node_pairs.append((src, dst))

# Randomly select 192 pairs
selected_pairs = random.sample(all_node_pairs, MAX_BG_FLOWS)

for src, dst in selected_pairs:
    pg = PG_VALUE
    tag = TAG_BACKGROUND
    bg_flow_pool.append(f"{src} {dst} {pg} {BG_FLOW_SIZE} {BG_START_TIME:.9f} {tag}\n")

print(f"Background flow pool generated: {len(bg_flow_pool)} flows")
print(f"  - Source and destination selected from remaining {len(bg_nodes)} nodes")
print(f"  - (excluding {len(expert_group_ids) * GROUP_SIZE} expert nodes and {len(receiver_group_ids) * GROUP_SIZE} receiver nodes)")
print(f"  - Only requirement: src != dst")
print(f"  - First 64 flows will be used for fecmp=64")
print(f"  - First 128 flows will be used for fecmp=128")
print(f"  - All 192 flows will be used for fecmp=192")
print()

# Generate files for each fecmp_bg value
for FECMP_BG_COUNT in FECMP_BG_VALUES:
    print(f"{'='*60}")
    print(f"Generating file with {FECMP_BG_COUNT} background flows")
    print(f"{'='*60}")

    # Use cumulative background flows from pool
    bg_lines = bg_flow_pool[:FECMP_BG_COUNT]

    # Statistics
    total_bg_flows = len(bg_lines)
    bg_traffic = total_bg_flows * BG_FLOW_SIZE
    total_traffic = moe_traffic + bg_traffic

    # Count pg values and tag values
    bg_pg_3 = sum(1 for line in bg_lines if int(line.split()[2]) == PG_VALUE)
    bg_tag_1 = sum(1 for line in bg_lines if int(line.split()[5]) == TAG_BACKGROUND)
    moe_pg_3 = sum(1 for line in moe_lines if int(line.split()[2]) == PG_VALUE)
    moe_tag_2 = sum(1 for line in moe_lines if int(line.split()[5]) == TAG_EXPERT)

    print(f"\nFlow statistics:")
    if total_bg_flows > 0:
        print(f"Background flows: {total_bg_flows} ({bg_traffic / 1024 / 1024:.1f} MB)")
        print(f"  - pg={PG_VALUE}: {bg_pg_3}, tag={TAG_BACKGROUND}(ECMP): {bg_tag_1}")
    print(f"MoE flows per round: {total_moe_flows // ROUNDS}")
    print(f"Total MoE flows: {total_moe_flows} ({moe_traffic / 1024:.1f} KB)")
    print(f"  - pg={PG_VALUE}: {moe_pg_3}, tag={TAG_EXPERT}(other): {moe_tag_2}")
    print(f"Total traffic: {total_traffic / 1024 / 1024:.1f} MB")
    if total_traffic > 0:
        print(f"MoE ratio: {moe_traffic / total_traffic * 100:.1f}%")
        if moe_traffic > 0:
            print(f"Background vs MoE: {bg_traffic / moe_traffic * 100:.1f}%")

    # Write flow file - background flows first, then MoE flows
    if FECMP_BG_COUNT > 0:
        output_file = f"moe_1280group_256to8_8round_8KB_hybrid_{FECMP_BG_COUNT}fecmp.txt"
    else:
        output_file = "moe_1280group_256to8_8round_8KB.txt"

    with open(output_file, "w") as f:
        f.write(f"{total_moe_flows + total_bg_flows}\n")
        # Background flows first
        for line in bg_lines:
            f.write(line)
        # MoE flows after
        for line in moe_lines:
            f.write(line)

    print(f"\nFlow file written to: {output_file}")
    print(f"Order: Background flows (first {total_bg_flows} lines), then MoE flows ({total_moe_flows} lines)")
    print()
