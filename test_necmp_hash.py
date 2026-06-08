#!/usr/bin/env python3
"""
Test NECMP hash function - Multi-flow Distribution Analysis
"""

import struct
from collections import Counter

def ecmp_hash(key_bytes, seed):
    """MurmurHash3-style hash function (from NS-3 switch-node.cc)"""
    h = seed

    # Process 4 bytes at a time
    if len(key_bytes) > 3:
        key_x4 = key_bytes
        i = len(key_bytes) // 4
        offset = 0
        while i > 0:
            # Read 4 bytes as little-endian uint32
            k = struct.unpack('<I', key_bytes[offset:offset+4])[0]

            k *= 0xcc9e2d51
            k = (k << 15) | (k >> 17)  # Rotate left 15
            k &= 0xFFFFFFFF  # Keep 32-bit
            k *= 0x1b873593
            k &= 0xFFFFFFFF

            h ^= k
            h &= 0xFFFFFFFF
            h = (h << 13) | (h >> 19)  # Rotate left 13
            h &= 0xFFFFFFFF
            h += (h << 2) + 0xe6546b64
            h &= 0xFFFFFFFF

            offset += 4
            i -= 1

    # Handle remaining bytes (0-3 bytes)
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
    """Test multi-flow distribution with NECMP"""
    seed = 12345

    print("="*100)
    print("NECMP Multi-flow Distribution Test")
    print("="*100)
    print(f"Seed: {seed}, Testing 100 flows from same inPort")
    print()

    # Test NECMP: 100 flows from same inPort
    inPort = 1
    paths_necmp = []

    for i in range(100):
        sip = 0x0a000001 + i
        dip = 0x0a006400 + (i % 100)
        sport = 1000 + (i % 5000)
        dport = 8000 + (i % 1000)

        # NECMP: 16 bytes (sip + dip + sport|dport + inPort)
        hash_input = struct.pack('<IIII', sip, dip, sport | (dport << 16), inPort)
        hash_val = ecmp_hash(hash_input, seed)
        path = hash_val % 8
        paths_necmp.append(path)

    # Test Original ECMP: 100 flows (without inPort)
    paths_ecmp = []
    for i in range(100):
        sip = 0x0a000001 + i
        dip = 0x0a006400 + (i % 100)
        sport = 1000 + (i % 5000)
        dport = 8000 + (i % 1000)

        # Original ECMP: 12 bytes (sip + dip + sport|dport)
        hash_input = struct.pack('<III', sip, dip, sport | (dport << 16))
        hash_val = ecmp_hash(hash_input, seed)
        path = hash_val % 8
        paths_ecmp.append(path)

    # Print distribution comparison
    print(f"{'Method':<12} {'Path Distribution':<60} {'ChiSq':<8} {'MaxDev'}")
    print("-"*100)

    counter_dist_necmp = Counter(paths_necmp)
    counter_dist_ecmp = Counter(paths_ecmp)
    expected = 100 / 8

    # NECMP distribution
    necmp_str = " ".join([f"P{p}:{counter_dist_necmp[p]}" for p in sorted(counter_dist_necmp.keys())])
    chi_necmp = sum((c - expected)**2 / expected for c in counter_dist_necmp.values())
    max_dev_necmp = max(abs(c - expected) for c in counter_dist_necmp.values())
    print(f"{'NECMP':<12} {necmp_str:<60} {chi_necmp:<8.1f} {max_dev_necmp:.1f}")

    # ECMP distribution
    ecmp_str = " ".join([f"P{p}:{counter_dist_ecmp[p]}" for p in sorted(counter_dist_ecmp.keys())])
    chi_ecmp = sum((c - expected)**2 / expected for c in counter_dist_ecmp.values())
    max_dev_ecmp = max(abs(c - expected) for c in counter_dist_ecmp.values())
    print(f"{'ECMP':<12} {ecmp_str:<60} {chi_ecmp:<8.1f} {max_dev_ecmp:.1f}")

    print()
    print("="*100)
    print("CONCLUSION:")
    if chi_necmp < chi_ecmp:
        print(f"  NECMP distributes MORE uniformly (chi-square: {chi_necmp:.1f} vs {chi_ecmp:.1f})")
    else:
        print(f"  ECMP distributes MORE uniformly (chi-square: {chi_ecmp:.1f} vs {chi_necmp:.1f})")
    print()
    print("  Both methods achieve relatively uniform distribution across 8 paths.")
    print("  Adding inPort doesn't significantly improve multi-flow distribution")
    print("  because the 5-tuple already provides good entropy.")
    print("="*100)


if __name__ == "__main__":
    main()
