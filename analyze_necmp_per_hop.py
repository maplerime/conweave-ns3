#!/usr/bin/env python3
"""
Analyze NECMP hash per-hop in leaf-spine topology
"""

import struct
from collections import Counter, defaultdict

def ecmp_hash(key_bytes, seed):
    """MurmurHash3-style hash function"""
    h = seed
    if len(key_bytes) > 3:
        key_x4 = key_bytes
        i = len(key_bytes) // 4
        offset = 0
        while i > 0:
            k = struct.unpack('<I', key_bytes[offset:offset+4])[0]
            k *= 0xcc9e2d51
            k = (k << 15) | (k >> 17)
            k &= 0xFFFFFFFF
            k *= 0x1b873593
            k &= 0xFFFFFFFF
            h ^= k
            h &= 0xFFFFFFFF
            h = (h << 13) | (h >> 19)
            h &= 0xFFFFFFFF
            h += (h << 2) + 0xe6546b64
            h &= 0xFFFFFFFF
            offset += 4
            i -= 1
    if len(key_bytes) & 3:
        remaining = len(key_bytes) & 3
        k = 0
        for j in range(remaining - 1, -1, -1):
            k <<= 8
            k |= key_bytes[len(key_bytes) - remaining + j]
        k *= 0xcc9e2d51
        k &= 0xFFFFFFFF
        k = (k << 15) | (k >> 17)
        k &= 0xFFFFFFFF
        k *= 0x1b873593
        k &= 0xFFFFFFFF
        h ^= k
        h &= 0xFFFFFFFF
    h ^= len(key_bytes)
    h &= 0xFFFFFFFF
    h ^= h >> 16
    h &= 0xFFFFFFFF
    h *= 0x85ebca6b
    h &= 0xFFFFFFFF
    h ^= h >> 13
    h &= 0xFFFFFFFF
    h *= 0xc2b2ae35
    h &= 0xFFFFFFFF
    h ^= h >> 16
    h &= 0xFFFFFFFF
    return h


def parse_topology_simple(topo_file):
    """Parse leaf-spine topology - fixed structure"""
    print(f"Parsing topology: {topo_file}")
    print()

    # leaf_spine_128_100G_OS2: 128 hosts, 16 switches (8 leaves + 8 spines)
    # Hosts: 0-127
    # Switches: 128-135 (leaves 0-7), 136-143 (spines 0-7)

    # Each leaf connects to 16 hosts (16 hosts per leaf = 8 leaves * 16 = 128)
    # Each leaf connects to all 8 spines
    # So there are 8 uplink ports per leaf

    hosts_per_leaf = 16
    num_leaves = 8
    num_spines = 8

    # Leaf i (switch 128+i) connects to:
    #   - Hosts: [i*16, (i+1)*16)
    #   - Spines: [136, 137, ..., 143] (switch IDs)

    print(f"Topology structure:")
    print(f"  Total hosts: 128")
    print(f"  Leaves: 8 (switch 128-135)")
    print(f"  Spines: 8 (switch 136-143)")
    print(f"  Hosts per leaf: {hosts_per_leaf}")
    print(f"  Uplinks per leaf: {num_spines} (to each spine)")
    print()

    return hosts_per_leaf, num_leaves, num_spines


def analyze_flow_file_with_necmp(flow_file, hosts_per_leaf=16):
    """Analyze NECMP routing per-hop"""

    print("="*100)
    print("NECMP Per-Hop Hash Analysis")
    print("="*100)
    print()

    seed = 12345

    # Read flow file
    with open(flow_file, 'r') as f:
        lines = f.readlines()

    total_flows = int(lines[0].strip())
    print(f"Total flows in file: {total_flows}")
    print()

    # Parse flows (src_host, dst_host, ...)
    flows = []
    for line in lines[1:]:
        parts = line.strip().split()
        if len(parts) >= 3:
            src = int(parts[0])
            dst = int(parts[1])
            flows.append((src, dst))

    # Analyze first 2000 inter-rack flows
    flows_analyzed = 0
    max_flows = 2000

    # Track hash distributions
    # HOP 1: At source leaf - inPort = host port (varies)
    hop1_paths = Counter()  # Which spine is chosen

    # HOP 2: At spine - inPort = source leaf ID (same for flows from same leaf)
    hop2_paths = Counter()  # Which destination leaf is chosen

    # Track flows by source leaf
    flows_by_src_leaf = defaultdict(list)  # src_leaf -> [(src, dst, hop1, hop2), ...]

    for src_host, dst_host in flows:
        if flows_analyzed >= max_flows:
            break

        src_leaf = src_host // hosts_per_leaf
        dst_leaf = dst_host // hosts_per_leaf

        if src_leaf == dst_leaf:
            continue  # Skip intra-leaf traffic

        # Simulate HOP 1: At source leaf
        # inPort = host port (each host has unique port to leaf)
        hop1_inPort = src_host  # Different for each host

        sip = 0x0a000000 | (src_host << 8) | 1
        dip = 0x0a000000 | (dst_host << 8) | 2
        sport = 1000 + src_host
        dport = 8000 + dst_host

        # NECMP hash with inPort
        hash1_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), hop1_inPort)
        hash1_val = ecmp_hash(hash1_input, seed)
        hop1_path = hash1_val % 8  # 8 spines

        # Simulate HOP 2: At spine
        # inPort = source leaf ID (same for all flows from same source leaf)
        hop2_inPort = src_leaf

        hash2_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), hop2_inPort)
        hash2_val = ecmp_hash(hash2_input, seed)
        hop2_path = hash2_val % 8  # 8 destination leaves

        hop1_paths[hop1_path] += 1
        hop2_paths[hop2_path] += 1
        flows_by_src_leaf[src_leaf].append((src_host, dst_host, hop1_path, hop2_path))
        flows_analyzed += 1

    print(f"Analyzed {flows_analyzed} inter-rack flows")
    print()

    # Print HOP 1 results
    print("="*100)
    print("HOP 1: Source Leaf -> Spine (ECMP at source leaf)")
    print("-"*100)
    print(f"  inPort = source host ID (varies: 0-127)")
    print(f"  ECMP choices: 8 spines (output ports)")
    print()

    for path in sorted(hop1_paths.keys()):
        count = hop1_paths[path]
        pct = count / flows_analyzed * 100
        bar = '*' * int(pct / 2)
        print(f"  Spine {path}: {count:5d} ({pct:5.1f}%) {bar}")

    expected = flows_analyzed / 8
    chi1 = sum((c - expected)**2 / expected for c in hop1_paths.values())
    max_dev1 = max(abs(c - expected) for c in hop1_paths.values())

    print(f"  Expected: {expected:.1f}, Max deviation: {max_dev1:.1f} ({max_dev1/expected*100:.1f}%)")
    print(f"  Chi-square: {chi1:.2f}")
    print()

    # Print HOP 2 results
    print("="*100)
    print("HOP 2: Spine -> Destination Leaf (ECMP at spine)")
    print("-"*100)
    print(f"  inPort = source leaf ID (same for flows from same source leaf)")
    print(f"  ECMP choices: 8 destination leaves (output ports)")
    print()

    for path in sorted(hop2_paths.keys()):
        count = hop2_paths[path]
        pct = count / flows_analyzed * 100
        bar = '*' * int(pct / 2)
        print(f"  DestLeaf {path}: {count:5d} ({pct:5.1f}%) {bar}")

    chi2 = sum((c - expected) ** 2 / expected for c in hop2_paths.values())
    max_dev2 = max(abs(c - expected) for c in hop2_paths.values())

    print(f"  Expected: {expected:.1f}, Max deviation: {max_dev2:.1f} ({max_dev2/expected*100:.1f}%)")
    print(f"  Chi-square: {chi2:.2f}")
    print()

    # Analysis by source leaf
    print("="*100)
    print("Analysis by Source Leaf")
    print("-"*100)

    for src_leaf in sorted(flows_by_src_leaf.keys()):
        flows_from_leaf = flows_by_src_leaf[src_leaf]
        print(f"\nLeaf {src_leaf} (hosts {src_leaf*16}-{(src_leaf+1)*16-1}): {len(flows_from_leaf)} flows")

        # For flows from this leaf, what spine do they take at HOP 1?
        hop1_this_leaf = Counter()
        for _, _, hop1, _ in flows_from_leaf:
            hop1_this_leaf[hop1] += 1

        print(f"  HOP 1 (from Leaf {src_leaf}):")
        for spine in sorted(hop1_this_leaf.keys()):
            count = hop1_this_leaf[spine]
            pct = count / len(flows_from_leaf) * 100
            print(f"    Spine {spine}: {count:4d} ({pct:5.1f}%)")

    print()
    print("="*100)
    print("KEY FINDING")
    print("-"*100)
    print("At HOP 2 (Spine): All flows FROM THE SAME source leaf have THE SAME inPort!")
    print()
    print("Example: All flows from Leaf 0 have inPort=0 at the spine")
    print("         This means hash(sip,dip,sport,dport,0) for these flows")
    print()
    print("However, since sip/dip/sport/dport are different, they still distribute well.")
    print()
    print("The inPort helps redistribute flows from different source leaves,")
    print("but flows from the SAME source leaf don't benefit from inPort at HOP 2.")
    print("="*100)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        flow_file = sys.argv[1]
    else:
        flow_file = "/home/eileen/ns-allinone-3.19/ns-3.19/config/L_15.00_CDF_AliStorage2019_N_128_T_10ms_B_100_flow.txt"

    analyze_flow_file_with_necmp(flow_file)
