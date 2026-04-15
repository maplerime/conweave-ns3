#!/usr/bin/env python3
"""
Analyze flow file to understand 5-tuple distribution for NECMP
"""

import struct
from collections import Counter

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


def analyze_flow_file(flow_file, seed=12345):
    """Analyze flow file for hash distribution"""
    print(f"Analyzing: {flow_file}")
    print("="*100)

    flows = []
    with open(flow_file, 'r') as f:
        lines = f.readlines()
        total_flows = int(lines[0].strip())
        print(f"Total flows: {total_flows}")
        print()

        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 3:
                src_host = int(parts[0])
                dst_host = int(parts[1])
                pg = int(parts[2])
                flows.append((src_host, dst_host, pg))

    # Map host IDs to IP addresses (assuming 10.0.{pod}.{host} format)
    # For leaf_spine_128: 128 hosts
    flows_analyzed = min(2000, len(flows))  # Analyze first 2000 flows

    print(f"Analyzing first {flows_analyzed} flows...")
    print()

    # Path distribution for NECMP
    path_counter = Counter()

    # Track unique (src, dst) pairs
    src_dst_pairs = set()
    # Track unique src hosts
    src_hosts = set()
    # Track unique dst hosts
    dst_hosts = set()

    for i, (src_host, dst_host, pg) in enumerate(flows[:flows_analyzed]):
        # Simulate IP addressing
        # Assuming leaf_spine topology with specific IP mapping
        sip = 0x0a000000 | (src_host << 8) | 1  # 10.x.x.1
        dip = 0x0a000000 | (dst_host << 8) | 2  # 10.x.x.2
        sport = 1000 + (src_host % 5000)
        dport = 8000 + (dst_host % 1000)

        # In leaf_spine, all flows from hosts arrive at same ToR ports initially
        # But at aggregation/core switches, they come from different spine ports
        # Let's simulate assuming inPort = src_host (for analysis)
        inPort = src_host % 8 + 1  # Simulate different inPorts

        # NECMP hash: sip + dip + sport|dport + inPort
        hash_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), inPort)
        hash_val = ecmp_hash(hash_input, seed)
        path = hash_val % 8

        path_counter[path] += 1
        src_dst_pairs.add((src_host, dst_host))
        src_hosts.add(src_host)
        dst_hosts.add(dst_host)

    print("Statistics:")
    print(f"  Unique (src, dst) pairs: {len(src_dst_pairs)}")
    print(f"  Unique src hosts: {len(src_hosts)}")
    print(f"  Unique dst hosts: {len(dst_hosts)}")
    print()

    print("NECMP Path Distribution:")
    print("-"*60)
    for p in sorted(path_counter.keys()):
        count = path_counter[p]
        pct = count / flows_analyzed * 100
        bar = '*' * int(pct / 2)
        print(f"  Path {p}: {count:5d} ({pct:5.1f}%) {bar}")

    expected = flows_analyzed / 8
    chi_square = sum((c - expected)**2 / expected for c in path_counter.values())
    max_dev = max(abs(c - expected) for c in path_counter.values())

    print()
    print(f"Expected per path: {expected:.1f}")
    print(f"Max deviation: {max_dev:.1f} ({max_dev/expected*100:.1f}%)")
    print(f"Chi-square: {chi_square:.2f}")
    print()

    # Check if there's "hot spot" - many flows with same src or dst
    print("Flow Pattern Analysis:")
    print("-"*60)

    # Count flows per src
    src_counter = Counter()
    for src_host, dst_host, pg in flows[:flows_analyzed]:
        src_counter[src_host] += 1

    print(f"Top 10 sources by flow count:")
    for src, count in src_counter.most_common(10):
        print(f"  Host {src}: {count} flows")

    print()
    print("="*100)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        flow_file = sys.argv[1]
    else:
        flow_file = "/home/eileen/ns-allinone-3.19/ns-3.19/config/L_25.00_CDF_AliStorage2019_N_128_T_10ms_B_100_flow.txt"

    analyze_flow_file(flow_file)
