#!/usr/bin/env python3
"""
Generate MoE flow file for 1280-node 5-pod topology
- All flows use pg=3
- tag=1 for background flows, tag=2 for expert flows

Each group of 4 nodes acts like a multi-NIC host
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

EXPERT_GROUPS = 256      # Number of sender groups
RECEIVER_GROUPS = 8      # Number of receiver groups
ROUNDS = 8               # Number of MoE rounds
MOE_FLOW_SIZE = 8192     # 8KB per flow
BG_FLOW_SIZE = 8 * 1024 * 1024  # 8MB per background flow

# New parameters
PG_VALUE = 3             # All flows use pg=3
TAG_EXPERT = 2           # Expert flow tag
TAG_BACKGROUND = 1       # Background flow tag

# Timing
BG_START_TIME = 2.0      # Background flows start at 2.0s
ROUND_INTERVAL_US = 100  # 100us between rounds
ROUND_TIME_US = 200      # Time for each round to complete

random.seed(42)

# Helper: get group ID from node ID
def node_to_group(node_id):
    """Convert node ID to group ID"""
    pod = node_id // NODES_PER_POD
    node_in_pod = node_id % NODES_PER_POD
    group_in_pod = node_in_pod // GROUP_SIZE
    return pod * GROUPS_PER_POD + group_in_pod

# Helper: get all nodes in a group
def get_group_nodes(group_id):
    """Return list of 4 node IDs for the given group"""
    pod = group_id // GROUPS_PER_POD
    group_in_pod = group_id % GROUPS_PER_POD
    start_node = pod * NODES_PER_POD + group_in_pod * GROUP_SIZE
    return [start_node + i for i in range(GROUP_SIZE)]

# Select expert groups distributed across pods
all_groups = list(range(NUM_GROUPS))
expert_group_ids = []

# Distribute expert groups evenly across pods
expert_per_pod = EXPERT_GROUPS // NUM_PODS  # 51 per pod
expert_remainder = EXPERT_GROUPS % NUM_PODS  # 1 extra group

for pod in range(NUM_PODS):
    pod_groups = [g for g in all_groups if g // GROUPS_PER_POD == pod]
    # Select expert groups for this pod
    num_expert = expert_per_pod + (1 if pod < expert_remainder else 0)
    pod_expert = random.sample(pod_groups, num_expert)
    expert_group_ids.extend(pod_expert)

expert_group_ids = sorted(expert_group_ids)
remaining_groups = sorted([g for g in all_groups if g not in expert_group_ids])

# Select receiver groups distributed across pods
receiver_group_ids = []
receivers_per_pod = RECEIVER_GROUPS // NUM_PODS  # 1 per pod
receiver_remainder = RECEIVER_GROUPS % NUM_PODS  # 3 extra groups

for pod in range(NUM_PODS):
    pod_remaining = [g for g in remaining_groups if g // GROUPS_PER_POD == pod]
    if not pod_remaining:
        continue
    # Select receiver groups for this pod
    num_receivers = receivers_per_pod + (1 if pod < receiver_remainder else 0)
    num_receivers = min(num_receivers, len(pod_remaining))
    pod_receivers = random.sample(pod_remaining, num_receivers)
    receiver_group_ids.extend(pod_receivers)

receiver_group_ids = sorted(receiver_group_ids)

# Remove receiver groups from remaining groups
remaining_for_bg = sorted([g for g in remaining_groups if g not in receiver_group_ids])

print(f"Topology: {NUM_PODS} pods, {NODES_PER_POD} hosts per pod, {TOTAL_NODES} total hosts")
print(f"Group structure: {NUM_GROUPS} groups total ({GROUPS_PER_POD} groups per pod), {GROUP_SIZE} nodes per group")
print()
print(f"All flows use pg={PG_VALUE}, tag={TAG_EXPERT}(expert) or {TAG_BACKGROUND}(background)")
print()
print(f"Expert Groups: {len(expert_group_ids)} groups")

# Show pod distribution for expert groups
expert_pod_dist = {}
for pod in range(NUM_PODS):
    pod_expert = [g for g in expert_group_ids if g // GROUPS_PER_POD == pod]
    expert_pod_dist[pod] = len(pod_expert)
print(f"  Distribution per pod: {expert_pod_dist}")

print(f"Receiver Groups: {receiver_group_ids}")
receiver_pod_dist = {}
for pod in range(NUM_PODS):
    pod_receivers = [g for g in receiver_group_ids if g // GROUPS_PER_POD == pod]
    receiver_pod_dist[pod] = len(pod_receivers) if pod_receivers else 0
print(f"  Distribution per pod: {receiver_pod_dist}")

print(f"Remaining Groups (for background): {len(remaining_for_bg)} groups")
bg_pod_dist = {}
for pod in range(NUM_PODS):
    pod_bg = [g for g in remaining_for_bg if g // GROUPS_PER_POD == pod]
    bg_pod_dist[pod] = len(pod_bg)
print(f"  Distribution per pod: {bg_pod_dist}")
print()

# Convert groups to node ID lists
expert_groups_nodes = []
for g in expert_group_ids:
    expert_groups_nodes.append(get_group_nodes(g))

receiver_groups_nodes = []
for g in receiver_group_ids:
    receiver_groups_nodes.append(get_group_nodes(g))

# Background nodes: from remaining groups only
bg_nodes = []
for g in remaining_for_bg:
    bg_nodes.extend(get_group_nodes(g))

print(f"Total nodes: {TOTAL_NODES}")
print(f"Expert sender nodes: {len(expert_group_ids) * GROUP_SIZE} (256 groups x 4)")
print(f"Receiver nodes: {len(receiver_group_ids) * GROUP_SIZE} (8 groups x 4)")
print(f"Background-available nodes: {len(bg_nodes)} ({len(remaining_for_bg)} groups x 4)")
print(f"Flows per expert node: 2 (one to each receiver group per round)")
print(f"Total flows per round: {EXPERT_GROUPS * GROUP_SIZE * 2}")
print()

# Generate MoE flows (SAME for all files)
moe_lines = []
moe_start_time = BG_START_TIME + 0.001  # Start 1ms after background

for round_id in range(ROUNDS):
    # Select random 2 receiver groups for this round (each expert node sends 2 flows)
    round_receiver_groups = random.sample(receiver_groups_nodes, 2)

    for expert_group in expert_groups_nodes:
        # 4 nodes in expert group sequentially send to 4 nodes in each receiver group
        for receiver_group in round_receiver_groups:
            # One-to-one: 4 flows
            for i in range(GROUP_SIZE):
                src = expert_group[i]
                dst = receiver_group[i]
                # Ensure src != dst
                if src == dst:
                    continue  # Skip self-flow
                pg = PG_VALUE  # All flows use pg=3
                tag = TAG_EXPERT  # Expert flow tag
                moe_lines.append(f"{src} {dst} {pg} {tag} {MOE_FLOW_SIZE} {moe_start_time:.9f}\n")

total_moe_flows = len(moe_lines)
moe_traffic = total_moe_flows * MOE_FLOW_SIZE

print(f"MoE flows per round: {total_moe_flows // ROUNDS}")
print(f"Total MoE flows: {total_moe_flows} ({moe_traffic / 1024:.1f} KB)")
print()

# Generate background flow pool ONCE (192 flows maximum)
# This ensures cumulative inclusion: 64 -> 128 -> 192
MAX_BG_FLOWS = 192
bg_flow_pool = []

# Generate 192 background flows from remaining nodes
bg_sender_pool = random.sample(bg_nodes, MAX_BG_FLOWS)

for idx, src in enumerate(bg_sender_pool):
    # Select destination from background nodes, different from source
    dst_candidates = [n for n in bg_nodes if n != src]
    if not dst_candidates:
        continue
    dst = random.choice(dst_candidates)
    pg = PG_VALUE  # All flows use pg=3
    tag = TAG_BACKGROUND  # Background flow tag
    bg_flow_pool.append(f"{src} {dst} {pg} {tag} {BG_FLOW_SIZE} {BG_START_TIME:.9f}\n")

print(f"Background flow pool generated: {len(bg_flow_pool)} flows")
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
    bg_tag_1 = sum(1 for line in bg_lines if int(line.split()[3]) == TAG_BACKGROUND)
    moe_pg_3 = sum(1 for line in moe_lines if int(line.split()[2]) == PG_VALUE)
    moe_tag_2 = sum(1 for line in moe_lines if int(line.split()[3]) == TAG_EXPERT)

    print(f"\nFlow statistics:")
    if total_bg_flows > 0:
        print(f"Background flows: {total_bg_flows} ({bg_traffic / 1024 / 1024:.1f} MB)")
        print(f"  - pg={PG_VALUE}: {bg_pg_3}, tag={TAG_BACKGROUND}: {bg_tag_1}")
    print(f"MoE flows per round: {total_moe_flows // ROUNDS}")
    print(f"Total MoE flows: {total_moe_flows} ({moe_traffic / 1024:.1f} KB)")
    print(f"  - pg={PG_VALUE}: {moe_pg_3}, tag={TAG_EXPERT}: {moe_tag_2}")
    print(f"Total traffic: {total_traffic / 1024 / 1024:.1f} MB")
    print(f"MoE ratio: {moe_traffic / total_traffic * 100:.1f}%")
    print(f"Background ratio: {bg_traffic / total_traffic * 100:.1f}%")

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
