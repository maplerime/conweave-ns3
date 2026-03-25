#!/usr/bin/env python3
"""
Generate MoE flow file for 1280-node topology (320 groups x 4 nodes)
Each group of 4 nodes acts like a multi-NIC host
One-to-one mapping: 4 nodes in expert group map to 4 nodes in receiver group
"""

import random
import argparse

# Parse arguments
parser = argparse.ArgumentParser(description='Generate MoE flow file with hybrid pg')
parser.add_argument('--fecmp_bg', type=int, default=0,
                    help='Number of 8MB background flows using pg=0 (fecmp), rest use pg=2 (drill)')
args = parser.parse_args()

FECMP_BG_COUNT = args.fecmp_bg  # Number of 8MB flows with pg=0

# Parameters
TOTAL_NODES = 1280
GROUP_SIZE = 4
NUM_GROUPS = TOTAL_NODES // GROUP_SIZE  # 320 groups

EXPERT_GROUPS = 256      # Number of sender groups
RECEIVER_GROUPS = 8      # Number of receiver groups
ROUNDS = 8               # Number of MoE rounds
MOE_FLOW_SIZE = 8192     # 8KB per flow
BG_FLOW_SIZE = 8 * 1024 * 1024  # 8MB per background flow

BG_SENDERS = 192         # Number of background flow senders

# Network parameters for timing
LINK_RATE_Gbps = 100
NIC_BANDWIDTH_Gbps = LINK_RATE_Gbps  # 100Gbps per node
# Each group has 4 NICs, so 400Gbps aggregate bandwidth

# Timing
BG_START_TIME = 2.0      # Background flows start at 2.0s
ROUND_INTERVAL_US = 100  # 100us between rounds

# Calculate round completion time
# Each expert group sends 4 flows to each receiver group (one-to-one)
# Each receiver group gets: 256 expert groups x 4 flows = 1024 flows = 8MB
# At 400Gbps per group (4x100Gbps), receiving 8MB takes: 8MB / 50GB/s = 0.16ms = 160us
# Add margin: round_time = 200us
ROUND_TIME_US = 200      # Time for each round to complete

random.seed(42)

# Generate group IDs
all_groups = list(range(NUM_GROUPS))

# Select expert sender groups and receiver groups
expert_group_ids = random.sample(all_groups, EXPERT_GROUPS)
remaining_groups = [g for g in all_groups if g not in expert_group_ids]
receiver_group_ids = random.sample(remaining_groups, RECEIVER_GROUPS)

# Convert groups to node ID ranges
expert_groups_nodes = []
for g in sorted(expert_group_ids):
    nodes = [g * GROUP_SIZE + i for i in range(GROUP_SIZE)]
    expert_groups_nodes.append(nodes)

receiver_groups_nodes = []
for g in sorted(receiver_group_ids):
    nodes = [g * GROUP_SIZE + i for i in range(GROUP_SIZE)]
    receiver_groups_nodes.append(nodes)

print(f"Total nodes: {TOTAL_NODES}")
print(f"Groups: {NUM_GROUPS} (4 nodes per group)")
print(f"Expert sender groups: {EXPERT_GROUPS}")
print(f"Receiver groups: {RECEIVER_GROUPS}")
print(f"Flows per expert group per receiver group: {GROUP_SIZE} (one-to-one)")
print(f"Total flows per round: {EXPERT_GROUPS * RECEIVER_GROUPS * GROUP_SIZE}")
print(f"Fecmp background flows: {FECMP_BG_COUNT}")

# Background: remaining hosts (256 nodes from 64 groups)
remaining_nodes = []
for g in remaining_groups:
    if g not in receiver_group_ids:
        remaining_nodes.extend([g * GROUP_SIZE + i for i in range(GROUP_SIZE)])

bg_sender_node_ids = random.sample(remaining_nodes, BG_SENDERS)
# Background receivers: any node not in expert senders
all_expert_nodes = []
for g in expert_group_ids:
    all_expert_nodes.extend([g * GROUP_SIZE + i for i in range(GROUP_SIZE)])
bg_receiver_candidates = [n for n in range(TOTAL_NODES) if n not in all_expert_nodes]

print(f"Background senders: {len(bg_sender_node_ids)}")

moe_lines = []
bg_lines = []

# Generate background flows (placed at beginning of file)
# First FECMP_BG_COUNT flows use pg=0 (fecmp), rest use pg=2 (drill)
for idx, src in enumerate(bg_sender_node_ids):
    dst = random.choice(bg_receiver_candidates)
    if idx < FECMP_BG_COUNT:
        pg = 0  # fecmp
    else:
        pg = 2  # drill
    bg_lines.append(f"{src} {dst} {pg} {BG_FLOW_SIZE} {BG_START_TIME:.9f}\n")

# Generate MoE flows with proper timing
# One-to-one mapping: node i in expert group -> node i in receiver group
first_round_start = BG_START_TIME + 0.001  # Start 1ms after background

for round_id in range(ROUNDS):
    round_start_time = first_round_start + (round_id * (ROUND_TIME_US + ROUND_INTERVAL_US)) / 1e6

    for expert_group in expert_groups_nodes:
        for receiver_group in receiver_groups_nodes:
            # One-to-one: 4 flows (matching positions)
            for i in range(GROUP_SIZE):
                src = expert_group[i]
                dst = receiver_group[i]
                pg = 2  # drill
                moe_lines.append(f"{src} {dst} {pg} {MOE_FLOW_SIZE} {round_start_time:.9f}\n")

# Print timing summary
print(f"\nTiming summary:")
print(f"Background flows start: {BG_START_TIME:.6f}s")
for i in range(min(3, ROUNDS)):
    t = first_round_start + (i * (ROUND_TIME_US + ROUND_INTERVAL_US)) / 1e6
    print(f"Round {i} starts: {t:.6f}s (ends approx {t + ROUND_TIME_US/1e6:.6f}s)")
print(f"...")

# Statistics
total_moe_flows = len(moe_lines)
total_bg_flows = len(bg_lines)
moe_traffic = total_moe_flows * MOE_FLOW_SIZE
bg_traffic = total_bg_flows * BG_FLOW_SIZE
total_traffic = moe_traffic + bg_traffic

# Count pg values
bg_pg_0 = sum(1 for line in bg_lines if line.split()[2] == "0")
bg_pg_2 = sum(1 for line in bg_lines if line.split()[2] == "2")
moe_pg_2 = sum(1 for line in moe_lines if line.split()[2] == "2")

print(f"\nFlow statistics:")
print(f"Background flows: {total_bg_flows} ({bg_traffic / 1024 / 1024:.1f} MB)")
print(f"  - pg=0 (fecmp): {bg_pg_0}")
print(f"  - pg=2 (drill): {bg_pg_2}")
print(f"MoE flows per round: {total_moe_flows // ROUNDS}")
print(f"Total MoE flows: {total_moe_flows} ({moe_traffic / 1024:.1f} KB)")
print(f"  - pg=2 (drill): {moe_pg_2}")
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
