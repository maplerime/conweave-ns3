#!/usr/bin/env python3
"""
Generate MoE flow file for 1280-node 5-pod topology
- Only use nodes where node_id % 4 == 0 (total 320 nodes)
- Expert flows: randomly select 256 nodes from 320
  - Each source sends to 64 random destinations (from remaining 255)
  - 1 flow per destination
- Background flows: from remaining 64 nodes, incrementally add flows
"""

import random
import argparse

parser = argparse.ArgumentParser(description='Generate MoE flow file')
parser.add_argument('--fecmp_bg', type=int, default=None, help='Number of 8MB background flows')
parser.add_argument('--conflict', action='store_true', help='Create hash conflicts in background flows')
parser.add_argument('--conflict_ratio', type=float, default=0.25,
                    help='Ratio of unique SD pairs for background flows (default: 0.25 = 25%%)')
args = parser.parse_args()

FECMP_BG_VALUES = [0, 64, 128, 192] if args.fecmp_bg is None else [args.fecmp_bg]

TOTAL_NODES = 1280
NUM_PODS = 5
NODES_PER_POD = TOTAL_NODES // NUM_PODS

# Only use nodes where node_id % 4 == 0
EXPERT_NODE_COUNT = 256
RECEIVERS_PER_SOURCE = 64
MOE_FLOW_SIZE = 8192
BG_FLOW_SIZE = 8 * 1024 * 1024

PG_VALUE = 3
TAG_EXPERT = 2
TAG_BACKGROUND = 1
BG_START_TIME = 2.0

random.seed(2026)

# Get all nodes where node_id % 4 == 0
aligned_nodes = [i for i in range(TOTAL_NODES) if i % 4 == 0]
print(f"Total aligned nodes (node_id %% 4 == 0): {len(aligned_nodes)}")

# Randomly select 256 nodes for expert flows
expert_nodes = sorted(random.sample(aligned_nodes, EXPERT_NODE_COUNT))
# Remaining 64 nodes for background flows
bg_nodes = sorted([n for n in aligned_nodes if n not in expert_nodes])

print(f"Expert nodes: {len(expert_nodes)}")
print(f"Background nodes: {len(bg_nodes)}")
print()

if args.conflict:
    print(f"*** BG CONFLICT MODE: ratio={args.conflict_ratio} ***")
    print(f"    Background flows concentrated on fewer SD pairs for hash collisions")
print()

# Generate background flows incrementally
# First generate up to 192 unique SD pairs, then use incrementally
bg_flows_all = []
used_pairs = set()
attempts = 0
max_attempts = 192 * 10

while len(bg_flows_all) < 192 and attempts < max_attempts:
    src = random.choice(bg_nodes)
    dst = random.choice(bg_nodes)
    if src != dst and (src, dst) not in used_pairs:
        used_pairs.add((src, dst))
        flow_entry = f"{src} {dst} {PG_VALUE} {BG_FLOW_SIZE} {BG_START_TIME:.9f} {TAG_BACKGROUND}\n"
        bg_flows_all.append(flow_entry)
    attempts += 1

print(f"Generated {len(bg_flows_all)} unique background flow pairs")

# Incremental background flows: 64, 128 (keep first 64), 192 (keep first 128)
bg_flow_pools = {
    0: [],
    64: bg_flows_all[:64],
    128: bg_flows_all[:128],
    192: bg_flows_all[:192]
}

print(f"Background flow pools: 0/64/128/192 flows")
print()

# Generate MoE flows
# Each source selects 64 random destinations (from remaining expert nodes)
# 1 flow per destination
source_to_receivers = {}
for src in expert_nodes:
    available = [d for d in expert_nodes if d != src]
    receivers = sorted(random.sample(available, RECEIVERS_PER_SOURCE))
    source_to_receivers[src] = receivers

moe_lines = []
for src in expert_nodes:
    receivers = source_to_receivers[src]
    for dst in receivers:
        # 1 flow per source-destination pair
        moe_lines.append(f"{src} {dst} {PG_VALUE} {MOE_FLOW_SIZE} {BG_START_TIME:.9f} {TAG_EXPERT}\n")

total_moe_flows = len(moe_lines)
print(f"Total MoE flows: {total_moe_flows} ({total_moe_flows * MOE_FLOW_SIZE / 1024:.1f} KB)")
print(f"Pattern: {EXPERT_NODE_COUNT} sources × {RECEIVERS_PER_SOURCE} receivers = {total_moe_flows} flows")
print()

# Generate files
for FECMP_BG_COUNT in FECMP_BG_VALUES:
    bg_lines = bg_flow_pools[FECMP_BG_COUNT]
    total_bg_flows = len(bg_lines)

    if args.conflict:
        if FECMP_BG_COUNT > 0:
            output_file = f"moe_1280group_256to8_8round_8KB_bg_conflict{int(args.conflict_ratio*100)}_{FECMP_BG_COUNT}fecmp.txt"
        else:
            output_file = "moe_1280group_256to8_8round_8KB.txt"
    elif FECMP_BG_COUNT > 0:
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
