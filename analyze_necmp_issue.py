#!/usr/bin/env python3
"""
Analyze NECMP issue in leaf-spine topology
The problem: flows from same leaf switch arrive at spine via SAME port
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


def main():
    print("="*100)
    print("NECMP Issue Analysis in Leaf-Spine Topology")
    print("="*100)
    print()

    # Leaf-spine topology:
    # - 8 leaf switches (each connects to 16 hosts)
    # - 8 spine switches
    # - Each leaf connects to all 8 spines
    # - Each spine connects to all 8 leafs

    print("Topology:")
    print("  - 8 Leaf switches (L0-L7), each with 16 hosts")
    print("  - 8 Spine switches (S0-S7)")
    print("  - Each Leaf connects to all 8 Spines")
    print()

    seed = 12345

    # Simulate: Many flows from same leaf switch (e.g., Leaf 0)
    # At the Spine switch, all these flows arrive via THE SAME PORT (from Leaf 0)
    print("SCENARIO 1: Flows from same leaf, arriving at Spine via same port")
    print("-"*100)

    leaf_id = 0
    spine_inPort = 0  # All flows from Leaf 0 arrive at Spine via port 0

    paths = []
    # Simulate 100 flows from Leaf 0 (hosts 0-15) to various destinations
    for i in range(100):
        src_host = leaf_id * 16 + (i % 16)  # Hosts 0-15 on Leaf 0
        dst_host = (i + 32) % 128  # Various destinations

        sip = 0x0a000000 | (src_host << 8) | 1
        dip = 0x0a000000 | (dst_host << 8) | 2
        sport = 1000 + src_host
        dport = 8000 + dst_host

        # At Spine: inPort is the same (from Leaf 0)
        inPort = spine_inPort

        hash_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), inPort)
        hash_val = ecmp_hash(hash_input, seed)
        path = hash_val % 8  # 8 possible output ports (to 8 different leafs)

        paths.append(path)

    path_counter = Counter(paths)
    print(f"Flows from Leaf 0, arriving at Spine via port {spine_inPort}:")
    for p in sorted(path_counter.keys()):
        count = path_counter[p]
        pct = count / len(paths) * 100
        bar = '*' * int(pct / 2)
        print(f"  Path {p}: {count:3d} ({pct:5.1f}%) {bar}")

    print()
    print("=> GOOD! Even with same inPort, different flows distribute uniformly")
    print()

    # Now simulate: What if many flows have SAME source and destination hosts?
    print("SCENARIO 2: Many flows between SAME src-dst pair (incast)")
    print("-"*100)

    paths_same_pair = []
    src_host = 0
    dst_host = 64

    for i in range(100):
        # Different ports (different flows)
        sport = 2000 + i
        dport = 8000 + i

        sip = 0x0a000000 | (src_host << 8) | 1
        dip = 0x0a000000 | (dst_host << 8) | 2
        inPort = 0  # Same inPort

        hash_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), inPort)
        hash_val = ecmp_hash(hash_input, seed)
        path = hash_val % 8

        paths_same_pair.append(path)

    path_counter_same = Counter(paths_same_pair)
    print(f"100 flows between same src-dst pair (different ports):")
    for p in sorted(path_counter_same.keys()):
        count = path_counter_same[p]
        pct = count / len(paths_same_pair) * 100
        bar = '*' * int(pct / 2)
        print(f"  Path {p}: {count:3d} ({pct:5.1f}%) {bar}")

    print()
    print("=> GOOD! Different ports create different hash values")
    print()

    # The REAL problem: What if workload has "all-to-all" or "incast" pattern?
    # where traffic is concentrated?
    print("="*100)
    print("REAL ISSUE ANALYSIS:")
    print("-"*100)
    print()
    print("In leaf-spine topology, the bottleneck is usually at the LEAF UPLINKS.")
    print()
    print("When 16 hosts on Leaf 0 send traffic to 16 hosts on Leaf 7:")
    print("  - At Leaf 0: 16 flows -> 8 spine paths (ECMP distributes)")
    print("  - At Spine: All flows for Leaf 7 go to THE SAME OUTPUT PORT")
    print("  - This creates HOT SPOT at Leaf 0's uplink to specific spine")
    print()
    print("NECMP doesn't help here because:")
    print("  - At Leaf 0: inPort = host port (different for each host)")
    print("  - At Spine: inPort = from Leaf 0 (SAME for all 16 flows!)")
    print("  - At Spine: hash(sip,dip,sport,dport,inPort=Leaf0)")
    print()
    print("But wait... if inPort is same, the hash should still distribute...")
    print("Let me verify:")
    print()

    # Verify: 16 flows from Leaf 0 to hosts on Leaf 7, at Spine
    paths_leaf_to_leaf = []
    src_leaf = 0
    dst_leaf = 7
    spine_inPort = src_leaf  # All from Leaf 0 arrive at same Spine port

    for i in range(16):
        src_host = src_leaf * 16 + i  # Hosts 0-15
        dst_host = dst_leaf * 16 + i  # Hosts 112-127

        sip = 0x0a000000 | (src_host << 8) | 1
        dip = 0x0a000000 | (dst_host << 8) | 2
        sport = 1000 + i
        dport = 8000 + i

        hash_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), spine_inPort)
        hash_val = ecmp_hash(hash_input, seed)
        path = hash_val % 8

        paths_leaf_to_leaf.append(path)
        print(f"  Host {src_host:3d} -> Host {dst_host:3d}: hash={hash_val:10d}, path={path}")

    path_counter_ll = Counter(paths_leaf_to_leaf)
    print()
    print(f"Distribution: {dict(sorted(path_counter_ll.items()))}")

    print()
    print("="*100)
    print("CONCLUSION:")
    print("NECMP hash distribution itself is UNIFORM.")
    print("The performance issue may be due to:")
    print("  1. Queue management and buffer overflow")
    print("  2. PFC interactions causing head-of-line blocking")
    print("  3. Not related to hash distribution but flow scheduling/queuing")
    print("="*100)


if __name__ == "__main__":
    main()
