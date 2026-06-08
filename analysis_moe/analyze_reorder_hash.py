#!/usr/bin/env python3
"""
分析MixHash模式下流的四元组hash值和reorder队列分布

计算:
1. 流的hash值 (Murmur3 x86_32, seed=0x8BADF00D)
2. ecmp_counter % REORDER_QUEUE_NUM 的值 (reorder队列索引)
3. hash % REORDER_QUEUE_NUM 的值
"""

# Reorder queue配置 (与ns-3一致)
REORDER_QUEUE_NUM = 64  # reorder队列数量
HASH_MODULO = 8        # hash取模的固定值

import struct
import sys
from pathlib import Path

# Murmur3 x86_32 hash implementation (same as ns-3)
def murmur3_x86_32(data, seed=0x8BADF00D):
    """
    Murmur3 x86_32 hash function

    Args:
        data: bytes to hash
        seed: hash seed (default 0x8BADF00D, same as ns-3)

    Returns:
        32-bit hash value
    """
    def rotl32(x, r):
        return (x << r) | (x >> (32 - r))

    h1 = seed
    c1 = 0xcc9e2d51
    c2 = 0x1b873593

    length = len(data)
    nblocks = length // 4

    # Process blocks
    blocks = struct.unpack('<' + 'I' * nblocks, data[:nblocks * 4])
    for k1 in blocks:
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = rotl32(k1, 15)
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 = (h1 ^ k1) & 0xFFFFFFFF
        h1 = rotl32(h1, 13)
        h1 = ((h1 * 5) + 0xe6546b64) & 0xFFFFFFFF

    # Process tail
    tail = data[nblocks * 4:]
    k1 = 0
    tail_size = length & 3
    if tail_size == 3:
        k1 ^= tail[2] << 16
    if tail_size >= 2:
        k1 ^= tail[1] << 8
    if tail_size >= 1:
        k1 ^= tail[0]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = rotl32(k1, 15)
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 = (h1 ^ k1) & 0xFFFFFFFF

    # Finalization
    h1 = (h1 ^ length) & 0xFFFFFFFF

    # fmix
    h1 ^= h1 >> 16
    h1 = ((h1 * 0x85ebca6b) & 0xFFFFFFFF)
    h1 ^= h1 >> 13
    h1 = ((h1 * 0xc2b2ae35) & 0xFFFFFFFF)
    h1 ^= h1 >> 16

    return h1


def calculate_flow_hash(sip, dip, sport, dport):
    """
    计算流的hash值 (与ns-3 RdmaQueuePair::GetHash相同)

    Args:
        sip: source IP (uint32, host byte order)
        dip: destination IP (uint32, host byte order)
        sport: source port (uint16)
        dport: destination port (uint16)

    Returns:
        32-bit hash value
    """
    # Pack into 12 bytes: sip(4) + dip(4) + sport(2) + dport(2)
    data = struct.pack('<IIHH', sip, dip, sport, dport)
    return murmur3_x86_32(data)


def calculate_flow_hash_with_queue(sip, dip, sport, dport, queue_idx):
    """
    计算流+队列的hash值 (四元组 + ecmp_counter%8)

    Args:
        sip: source IP (uint32)
        dip: destination IP (uint32)
        sport: source port (uint16)
        dport: destination port (uint16)
        queue_idx: queue index (ecmp_counter % REORDER_QUEUE_NUM)

    Returns:
        32-bit hash value
    """
    # Pack into 13 bytes: sip(4) + dip(4) + sport(2) + dport(2) + queue(1)
    data = struct.pack('<IIHHB', sip, dip, sport, dport, queue_idx)
    return murmur3_x86_32(data)


def ip_to_uint32(ip_str):
    """Convert IP string '10.0.0.1' to uint32"""
    parts = ip_str.split('.')
    return (int(parts[0]) << 24) | (int(parts[1]) << 16) | \
           (int(parts[2]) << 8) | int(parts[3])


def uint32_to_ip(ip_uint):
    """Convert uint32 to IP string"""
    return f"{(ip_uint >> 24) & 0xFF}.{(ip_uint >> 16) & 0xFF}.{(ip_uint >> 8) & 0xFF}.{ip_uint & 0xFF}"


def print_separator():
    print("=" * 140)


def analyze_flow_from_line(line):
    """
    分析一行流数据

    输入格式: sip dip sport dport [ecmp_counter_start]
    或:     10.0.0.1 10.0.1.1 5000 6000
    """
    parts = line.strip().split()
    if len(parts) < 4:
        return None

    sip_str = parts[0]
    dip_str = parts[1]
    sport = int(parts[2])
    dport = int(parts[3])
    ecmp_start = int(parts[4]) if len(parts) > 4 else 0
    num_packets = int(parts[5]) if len(parts) > 5 else 10

    sip = ip_to_uint32(sip_str)
    dip = ip_to_uint32(dip_str)

    flow_hash = calculate_flow_hash(sip, dip, sport, dport)
    hash_mod_8 = flow_hash % REORDER_QUEUE_NUM

    results = []
    for i in range(num_packets):
        ecmp_counter = ecmp_start + i
        ecmp_mod_8 = ecmp_counter % REORDER_QUEUE_NUM
        results.append({
            'sip': sip_str,
            'dip': dip_str,
            'sport': sport,
            'dport': dport,
            'flow_hash': flow_hash,
            'hash_mod_8': hash_mod_8,
            'ecmp_counter': ecmp_counter,
            'ecmp_mod_8': ecmp_mod_8,
        })

    return results


def analyze_reorder_distribution(sip, dip, sport, dport, ecmp_start=0, num_pkts=16):
    """
    分析单个流的reorder队列分布

    Args:
        sip: source IP string (e.g., '10.0.0.1')
        dip: dest IP string
        sport: source port
        dport: dest port
        ecmp_start: starting ecmp_counter value
        num_pkts: number of packets to analyze
    """
    sip_uint = ip_to_uint32(sip)
    dip_uint = ip_to_uint32(dip)

    print_separator()
    print(f"Flow: {sip}:{sport} -> {dip}:{dport}")
    print(f"SIP(uint32): {sip_uint:#010x}  DIP(uint32): {dip_uint:#010x}")
    print(f"Reorder Queue Num: 8")
    print(f"Hash Input: 4-tuple + ecmp_counter%8")
    print()

    # Header
    print(f"{'Pkt':>4} {'ECMP_Ctr':>10} {'Queue':>6} {'Hash(4+Q)':>12} {'Hash%8':>8}")
    print("-" * 50)

    queue_distribution = {}
    hash_distribution = {}
    for i in range(num_pkts):
        ecmp_counter = ecmp_start + i
        queue_idx = ecmp_counter % REORDER_QUEUE_NUM
        queue_distribution[queue_idx] = queue_distribution.get(queue_idx, 0) + 1

        # Calculate hash with queue included
        hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
        hash_mod_8 = hash_with_queue % HASH_MODULO
        hash_distribution[hash_mod_8] = hash_distribution.get(hash_mod_8, 0) + 1

        print(f"{i+1:>4} {ecmp_counter:>10} {queue_idx:>6} {hash_with_queue:#010x} {hash_mod_8:>8}")

    print()
    print("Hash Distribution (Hash(4+Q) % REORDER_QUEUE_NUM):")
    for h in sorted(hash_distribution.keys()):
        bar = '█' * (hash_distribution[h] * 2)
        print(f"  Hash%8 {h}: {hash_distribution[h]:>2} packets {bar}")


def analyze_random_ports(sip, dip, num_flows, num_pkts_per_flow=16, seed=42):
    """
    分析随机端口的多流hash分布

    Args:
        sip: source IP string
        dip: dest IP string
        num_flows: number of flows to analyze
        num_pkts_per_flow: packets per flow (default 16)
        seed: random seed
    """
    import random
    random.seed(seed)

    sip_uint = ip_to_uint32(sip)
    dip_uint = ip_to_uint32(dip)

    print_separator()
    print(f"Multi-Flow Hash Analysis (Random Ports)")
    print(f"Source: {sip}  Dest: {dip}")
    print(f"Number of Flows: {num_flows}")
    print(f"Packets per Flow: {num_pkts_per_flow}")
    print(f"Hash Input: 4-tuple + ecmp_counter%8")
    print_separator()

    # 统计所有hash分布
    total_hash_distribution = {i: 0 for i in range(HASH_MODULO)}

    for flow_idx in range(num_flows):
        sport = random.randint(1024, 65535)
        dport = random.randint(1024, 65535)

        flow_hash_distribution = {i: 0 for i in range(HASH_MODULO)}

        for pkt_idx in range(num_pkts_per_flow):
            ecmp_counter = pkt_idx
            queue_idx = ecmp_counter % REORDER_QUEUE_NUM
            hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
            hash_mod_8 = hash_with_queue % HASH_MODULO
            flow_hash_distribution[hash_mod_8] += 1
            total_hash_distribution[hash_mod_8] += 1

    print("\nPer-Flow Hash Distribution (first 10 flows):")
    hash_headers = "  ".join([f"H{i:>2}" for i in range(HASH_MODULO)])
    print(f"{'Flow':>4} {'Sport':>6} {'Dport':>6}  {hash_headers}")
    print("-" * 60)

    random.seed(seed)
    for flow_idx in range(min(10, num_flows)):
        sport = random.randint(1024, 65535)
        dport = random.randint(1024, 65535)
        flow_hash_distribution = {i: 0 for i in range(HASH_MODULO)}

        for pkt_idx in range(num_pkts_per_flow):
            ecmp_counter = pkt_idx
            queue_idx = ecmp_counter % REORDER_QUEUE_NUM
            hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
            hash_mod_8 = hash_with_queue % HASH_MODULO
            flow_hash_distribution[hash_mod_8] += 1

        counts = [flow_hash_distribution[i] for i in range(HASH_MODULO)]
        print(f"{flow_idx+1:>4} {sport:>6} {dport:>6}  " + " ".join([f"{c:>3}" for c in counts]))

    print("\nTotal Hash Distribution (all {} flows):".format(num_flows))
    for h in range(HASH_MODULO):
        pct = total_hash_distribution[h] / (num_flows * num_pkts_per_flow) * 100
        bar = '█' * int(pct / 2)
        print(f"  Hash%8 {h}: {total_hash_distribution[h]:>6} packets ({pct:>5.1f}%) {bar}")


def analyze_ns3_style(cidr, num_flows, num_pkts_per_flow=16, seed=42, show_detail=False):
    """
    分析ns3风格的多流hash分布

    ns3端口规则:
    - 源端口: 从10000开始递增
    - 目的端口: 从100开始递增
    - 源/目的IP: 从指定CIDR范围随机选择

    Args:
        cidr: CIDR格式的IP范围 (e.g., "10.0.0.0/24")
        num_flows: number of flows to analyze
        num_pkts_per_flow: packets per flow (default 16)
        seed: random seed
    """
    import random
    import ipaddress
    random.seed(seed)

    # 解析CIDR并生成IP列表
    network = ipaddress.IPv4Network(cidr)
    hosts = list(network.hosts())

    print_separator()
    print(f"Multi-Flow Hash Analysis (NS3 Style)")
    print(f"IP Range: {cidr} (usable hosts: {len(hosts)})")
    print(f"Number of Flows: {num_flows}")
    print(f"Packets per Flow: {num_pkts_per_flow}")
    print(f"Source Port: starts at 10000, increments per flow")
    print(f"Dest Port: starts at 100, increments per flow")
    print(f"Hash Input: 4-tuple + ecmp_counter%8")
    print_separator()

    # 初始化端口计数器
    sport_start = 10000
    dport_start = 100

    # 存储每流的端口信息用于重新生成
    flow_ports = []

    # 第一遍：生成流信息并统计hash
    total_hash_distribution = {i: 0 for i in range(HASH_MODULO)}      # 4-tuple + queue
    total_base_hash_distribution = {i: 0 for i in range(HASH_MODULO)} # 仅 4-tuple

    for flow_idx in range(num_flows):
        # 随机选择源和目的IP
        sip_str = str(random.choice(hosts))
        dip_str = str(random.choice(hosts))

        # 端口按ns3规则递增
        sport = sport_start + flow_idx
        dport = dport_start + flow_idx

        flow_ports.append((sip_str, dip_str, sport, dport))

        sip_uint = ip_to_uint32(sip_str)
        dip_uint = ip_to_uint32(dip_str)

        # 计算原4元组hash (不含queue_idx) - 同一流内所有包hash值相同
        base_hash = calculate_flow_hash(sip_uint, dip_uint, sport, dport)
        base_hash_mod_8 = base_hash % HASH_MODULO
        total_base_hash_distribution[base_hash_mod_8] += num_pkts_per_flow

        # 计算带queue的hash
        for pkt_idx in range(num_pkts_per_flow):
            ecmp_counter = pkt_idx
            queue_idx = ecmp_counter % REORDER_QUEUE_NUM
            hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
            hash_mod_8 = hash_with_queue % HASH_MODULO
            total_hash_distribution[hash_mod_8] += 1

    # 显示前10个流的详细hash分布
    print("\nPer-Flow Hash Distribution (first 10 flows):")
    hash_headers = "  ".join([f"H{i:>2}" for i in range(HASH_MODULO)])
    print(f"{'Flow':>4} {'Src IP':>15} {'Dst IP':>15} {'Sport':>6} {'Dport':>6}  {hash_headers}")
    print("-" * 100)

    for flow_idx in range(min(10, num_flows)):
        sip_str, dip_str, sport, dport = flow_ports[flow_idx]
        sip_uint = ip_to_uint32(sip_str)
        dip_uint = ip_to_uint32(dip_str)

        flow_hash_distribution = {i: 0 for i in range(HASH_MODULO)}

        for pkt_idx in range(num_pkts_per_flow):
            ecmp_counter = pkt_idx
            queue_idx = ecmp_counter % REORDER_QUEUE_NUM
            hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
            hash_mod_8 = hash_with_queue % HASH_MODULO
            flow_hash_distribution[hash_mod_8] += 1

        counts = [flow_hash_distribution[i] for i in range(HASH_MODULO)]
        print(f"{flow_idx+1:>4} {sip_str:>15} {dip_str:>15} {sport:>6} {dport:>6}  " + " ".join([f"{c:>3}" for c in counts]))

    # 显示总hash分布对比
    print("\n" + "=" * 80)
    print("Hash Distribution Comparison:")
    print("=" * 80)

    print("\n[1] Hash(4-tuple + queue_idx) % 8 - Current MixHash Mode:")
    total_packets = num_flows * num_pkts_per_flow
    for h in range(HASH_MODULO):
        pct = total_hash_distribution[h] / total_packets * 100 if total_packets > 0 else 0
        bar_len = int(pct / 2)
        bar = '█' * bar_len if bar_len > 0 else ''
        print(f"  Hash%8 {h}: {total_hash_distribution[h]:>6} packets ({pct:>5.1f}%) {bar}")

    # 计算负载均衡效果 (4-tuple + queue)
    expected_per_bucket = total_packets / HASH_MODULO
    max_deviation = max(abs(total_hash_distribution[h] - expected_per_bucket) for h in range(HASH_MODULO))
    max_deviation_pct = (max_deviation / expected_per_bucket * 100) if expected_per_bucket > 0 else 0

    print(f"\nLoad Balance Quality (4-tuple + queue):")
    print(f"  Expected per bucket: {expected_per_bucket:.1f}")
    print(f"  Max deviation: {max_deviation:.1f} packets ({max_deviation_pct:.2f}%)")
    if max_deviation_pct < 5:
        quality_mixhash = "Excellent (deviation < 5%)"
    elif max_deviation_pct < 10:
        quality_mixhash = "Good (deviation < 10%)"
    elif max_deviation_pct < 20:
        quality_mixhash = "Fair (deviation < 20%)"
    else:
        quality_mixhash = "Poor (deviation >= 20%)"
    print(f"  Quality: {quality_mixhash}")

    # 原始4元组hash分布
    print("\n" + "-" * 80)
    print("[2] Hash(4-tuple only) % 8 - Original ECMP Mode (without queue_idx):")
    for h in range(HASH_MODULO):
        pct = total_base_hash_distribution[h] / total_packets * 100 if total_packets > 0 else 0
        bar_len = int(pct / 2)
        bar = '█' * bar_len if bar_len > 0 else ''
        print(f"  Hash%8 {h}: {total_base_hash_distribution[h]:>6} packets ({pct:>5.1f}%) {bar}")

    # 计算负载均衡效果 (仅4-tuple)
    max_deviation_base = max(abs(total_base_hash_distribution[h] - expected_per_bucket) for h in range(HASH_MODULO))
    max_deviation_base_pct = (max_deviation_base / expected_per_bucket * 100) if expected_per_bucket > 0 else 0

    print(f"\nLoad Balance Quality (4-tuple only):")
    print(f"  Expected per bucket: {expected_per_bucket:.1f}")
    print(f"  Max deviation: {max_deviation_base:.1f} packets ({max_deviation_base_pct:.2f}%)")
    if max_deviation_base_pct < 5:
        quality_base = "Excellent (deviation < 5%)"
    elif max_deviation_base_pct < 10:
        quality_base = "Good (deviation < 10%)"
    elif max_deviation_base_pct < 20:
        quality_base = "Fair (deviation < 20%)"
    else:
        quality_base = "Poor (deviation >= 20%)"
    print(f"  Quality: {quality_base}")

    # 总结对比
    print("\n" + "=" * 80)
    print("Summary:")
    print(f"  MixHash (4+Q):  Max deviation {max_deviation_pct:.2f}% - {quality_mixhash}")
    print(f"  ECMP   (4):     Max deviation {max_deviation_base_pct:.2f}% - {quality_base}")
    improvement = max_deviation_base_pct - max_deviation_pct
    if improvement > 0:
        print(f"  Improvement: {improvement:.2f}% (better)")
    elif improvement < 0:
        print(f"  Regression: {-improvement:.2f}% (worse)")
    else:
        print(f"  No change")
    print("=" * 80)

    # 显示每个包的详细信息（如果请求）
    if show_detail and num_flows <= 10:
        print("\nDetailed Packet Hash (first 2 flows, first 4 packets):")
        print(f"{'Flow':>4} {'Pkt':>4} {'Src IP':>15} {'Dst IP':>15} {'Sport':>6} {'Dport':>6} {'ECMP_Ctr':>10} {'Queue':>6} {'Hash(4+Q)':>12} {'Hash%8':>8}")
        print("-" * 115)

        for flow_idx in range(min(2, num_flows)):
            sip_str, dip_str, sport, dport = flow_ports[flow_idx]
            sip_uint = ip_to_uint32(sip_str)
            dip_uint = ip_to_uint32(dip_str)

            for pkt_idx in range(min(4, num_pkts_per_flow)):
                ecmp_counter = pkt_idx
                queue_idx = ecmp_counter % REORDER_QUEUE_NUM
                hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
                hash_mod_8 = hash_with_queue % HASH_MODULO
                print(f"{flow_idx+1:>4} {pkt_idx+1:>4} {sip_str:>15} {dip_str:>15} {sport:>6} {dport:>6} {ecmp_counter:>10} {queue_idx:>6} {hash_with_queue:#010x} {hash_mod_8:>8}")




def main():
    import random
    import ipaddress

    # Parse command line arguments
    args = sys.argv[1:]
    show_detail = '--detail' in args
    if show_detail:
        args = [a for a in args if a != '--detail']

    if len(args) < 1:
        print("Usage:")
        print(f"  {sys.argv[0]} <sip> <dip> <sport> <dport> [ecmp_start] [num_pkts]")
        print(f"  {sys.argv[0]} --ns3 <cidr> <num_flows> [num_pkts_per_flow] [--detail]")
        print(f"  {sys.argv[0]} --ecmp <flows>/<pkts> [--moe <flows>/<pkts>] [--detail]")
        print(f"  {sys.argv[0]} --random <sip> <dip> <num_flows> [num_pkts_per_flow]")
        print(f"  {sys.argv[0]} --file <flow_file>")
        print()
        print("Options:")
        print(f"  --detail    Show per-packet hash details")
        print()
        print("Examples:")
        print(f"  {sys.argv[0]} 10.0.0.1 10.0.1.1 5000 6000 0 16")
        print(f"  {sys.argv[0]} --ns3 10.0.0.0/24 1000 16")
        print(f"  {sys.argv[0]} --ecmp 64/1000")
        print(f"  {sys.argv[0]} --ecmp 64/1000 --moe 16384/10")
        print(f"  {sys.argv[0]} --random 10.0.0.1 10.0.1.1 1000 16")
        print(f"  {sys.argv[0]} --file flows.txt")
        sys.exit(1)

    if args[0] == '--ns3':
        # ns3风格多流分析 (CIDR + 递增端口)
        cidr = args[1]
        num_flows = int(args[2])
        num_pkts = int(args[3]) if len(args) > 3 else 16

        print_separator()
        print("Reorder Hash Analysis (MixHash Mode 16)")
        print(f"Reorder Queue Num: {REORDER_QUEUE_NUM}")
        print(f"Hash Algorithm: Murmur3 x86_32, seed=0x8BADF00D")
        print_separator()

        analyze_ns3_style(cidr, num_flows, num_pkts, show_detail=show_detail)

    elif args[0] == '--ecmp' or (len(args) >= 2 and args[0] == '--ecmp' and args[2] == '--moe'):
        # 解析 --ecmp 和可选的 --moe
        ecmp_parts = args[1].split('/')
        ecmp_flows = int(ecmp_parts[0])
        ecmp_pkts = int(ecmp_parts[1]) if len(ecmp_parts) > 1 else 16

        moe_flows = 0
        moe_pkts = 16

        # 查找 --moe 参数
        moe_idx = -1
        for i, arg in enumerate(args):
            if arg == '--moe' and i + 1 < len(args):
                moe_idx = i
                break

        if moe_idx > 0:
            moe_parts = args[moe_idx + 1].split('/')
            moe_flows = int(moe_parts[0])
            moe_pkts = int(moe_parts[1]) if len(moe_parts) > 1 else 16

        total_flows = ecmp_flows + moe_flows
        cidr = "10.0.0.0/24"
        seed = 42

        print_separator()
        if moe_flows > 0:
            print(f"Reorder Hash Analysis (ECMP: {ecmp_flows} flows x {ecmp_pkts} pkts, MoE: {moe_flows} flows x {moe_pkts} pkts)")
        else:
            print(f"Reorder Hash Analysis (ECMP: {ecmp_flows} flows x {ecmp_pkts} pkts)")
        print(f"Reorder Queue Num: {REORDER_QUEUE_NUM}")
        print(f"Hash Algorithm: Murmur3 x86_32, seed=0x8BADF00D")
        print_separator()

        # 解析CIDR并生成IP列表
        network = ipaddress.IPv4Network(cidr)
        hosts = list(network.hosts())
        random.seed(seed)

        # 初始化端口计数器和统计
        sport_start = 10000
        dport_start = 100
        total_hash_distribution = {i: 0 for i in range(HASH_MODULO)}
        total_base_hash_distribution = {i: 0 for i in range(HASH_MODULO)}

        # 处理ECMP流
        for flow_idx in range(ecmp_flows):
            sip_str = str(random.choice(hosts))
            dip_str = str(random.choice(hosts))
            sport = sport_start + flow_idx
            dport = dport_start + flow_idx

            sip_uint = ip_to_uint32(sip_str)
            dip_uint = ip_to_uint32(dip_str)

            # 原始4元组hash
            base_hash = calculate_flow_hash(sip_uint, dip_uint, sport, dport)
            base_hash_mod_8 = base_hash % HASH_MODULO
            total_base_hash_distribution[base_hash_mod_8] += ecmp_pkts

            # 带queue的hash
            for pkt_idx in range(ecmp_pkts):
                ecmp_counter = pkt_idx
                queue_idx = ecmp_counter % REORDER_QUEUE_NUM
                hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
                hash_mod_8 = hash_with_queue % HASH_MODULO
                total_hash_distribution[hash_mod_8] += 1

        # 处理MoE流 (端口从ECMP之后继续)
        for flow_idx in range(ecmp_flows, ecmp_flows + moe_flows):
            sip_str = str(random.choice(hosts))
            dip_str = str(random.choice(hosts))
            sport = sport_start + flow_idx
            dport = dport_start + flow_idx

            sip_uint = ip_to_uint32(sip_str)
            dip_uint = ip_to_uint32(dip_str)

            # 原始4元组hash
            base_hash = calculate_flow_hash(sip_uint, dip_uint, sport, dport)
            base_hash_mod_8 = base_hash % HASH_MODULO
            total_base_hash_distribution[base_hash_mod_8] += moe_pkts

            # 带queue的hash
            for pkt_idx in range(moe_pkts):
                ecmp_counter = pkt_idx
                queue_idx = ecmp_counter % REORDER_QUEUE_NUM
                hash_with_queue = calculate_flow_hash_with_queue(sip_uint, dip_uint, sport, dport, queue_idx)
                hash_mod_8 = hash_with_queue % HASH_MODULO
                total_hash_distribution[hash_mod_8] += 1

        # 计算总数
        total_packets = ecmp_flows * ecmp_pkts + moe_flows * moe_pkts

        # 显示总hash分布对比
        print("\n" + "=" * 80)
        print("Hash Distribution Comparison:")
        print("=" * 80)

        print(f"\n[1] Hash(4-tuple + queue_idx) % 8 - Current MixHash Mode:")
        for h in range(HASH_MODULO):
            pct = total_hash_distribution[h] / total_packets * 100 if total_packets > 0 else 0
            bar_len = int(pct / 2)
            bar = '█' * bar_len if bar_len > 0 else ''
            print(f"  Hash%8 {h}: {total_hash_distribution[h]:>6} packets ({pct:>5.1f}%) {bar}")

        expected_per_bucket = total_packets / HASH_MODULO
        max_deviation = max(abs(total_hash_distribution[h] - expected_per_bucket) for h in range(HASH_MODULO))
        max_deviation_pct = (max_deviation / expected_per_bucket * 100) if expected_per_bucket > 0 else 0

        print(f"\nLoad Balance Quality (4-tuple + queue):")
        print(f"  Total packets: {total_packets}")
        print(f"  Expected per bucket: {expected_per_bucket:.1f}")
        print(f"  Max deviation: {max_deviation:.1f} packets ({max_deviation_pct:.2f}%)")
        if max_deviation_pct < 5:
            quality_mixhash = "Excellent (deviation < 5%)"
        elif max_deviation_pct < 10:
            quality_mixhash = "Good (deviation < 10%)"
        elif max_deviation_pct < 20:
            quality_mixhash = "Fair (deviation < 20%)"
        else:
            quality_mixhash = "Poor (deviation >= 20%)"
        print(f"  Quality: {quality_mixhash}")

        # 原始4元组hash分布
        print("\n" + "-" * 80)
        print("[2] Hash(4-tuple only) % 8 - Original ECMP Mode (without queue_idx):")
        for h in range(HASH_MODULO):
            pct = total_base_hash_distribution[h] / total_packets * 100 if total_packets > 0 else 0
            bar_len = int(pct / 2)
            bar = '█' * bar_len if bar_len > 0 else ''
            print(f"  Hash%8 {h}: {total_base_hash_distribution[h]:>6} packets ({pct:>5.1f}%) {bar}")

        max_deviation_base = max(abs(total_base_hash_distribution[h] - expected_per_bucket) for h in range(HASH_MODULO))
        max_deviation_base_pct = (max_deviation_base / expected_per_bucket * 100) if expected_per_bucket > 0 else 0

        print(f"\nLoad Balance Quality (4-tuple only):")
        print(f"  Total packets: {total_packets}")
        print(f"  Expected per bucket: {expected_per_bucket:.1f}")
        print(f"  Max deviation: {max_deviation_base:.1f} packets ({max_deviation_base_pct:.2f}%)")
        if max_deviation_base_pct < 5:
            quality_base = "Excellent (deviation < 5%)"
        elif max_deviation_base_pct < 10:
            quality_base = "Good (deviation < 10%)"
        elif max_deviation_base_pct < 20:
            quality_base = "Fair (deviation < 20%)"
        else:
            quality_base = "Poor (deviation >= 20%)"
        print(f"  Quality: {quality_base}")

        # 总结对比
        print("\n" + "=" * 80)
        print("Summary:")
        print(f"  MixHash (4+Q):  Max deviation {max_deviation_pct:.2f}% - {quality_mixhash}")
        print(f"  ECMP   (4):     Max deviation {max_deviation_base_pct:.2f}% - {quality_base}")
        improvement = max_deviation_base_pct - max_deviation_pct
        if improvement > 0:
            print(f"  Improvement: {improvement:.2f}% (better)")
        elif improvement < 0:
            print(f"  Regression: {-improvement:.2f}% (worse)")
        else:
            print(f"  No change")
        print("=" * 80)

    elif args[0] == '--random':
        # 随机端口多流分析
        sip = args[1]
        dip = args[2]
        num_flows = int(args[3])
        num_pkts = int(args[4]) if len(args) > 4 else 16

        print_separator()
        print("Reorder Hash Analysis (MixHash Mode 16)")
        print(f"Reorder Queue Num: {REORDER_QUEUE_NUM}")
        print(f"Hash Algorithm: Murmur3 x86_32, seed=0x8BADF00D")
        print_separator()

        analyze_random_ports(sip, dip, num_flows, num_pkts)

    elif args[0] == '--file':
        # 从文件读取流信息
        flow_file = args[1]
        print_separator()
        print(f"Reorder Hash Analysis - File: {flow_file}")
        print_separator()

        with open(flow_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                results = analyze_flow_from_line(line)
                if results:
                    analyze_reorder_distribution(
                        results[0]['sip'], results[0]['dip'],
                        results[0]['sport'], results[0]['dport'],
                        results[0]['ecmp_counter'],
                        len(results)
                    )
    else:
        # 命令行参数
        sip = args[0]
        dip = args[1]
        sport = int(args[2])
        dport = int(args[3])
        ecmp_start = int(args[4]) if len(args) > 4 else 0
        num_pkts = int(args[5]) if len(args) > 5 else 16

        print_separator()
        print("Reorder Hash Analysis (MixHash Mode 16)")
        print(f"Reorder Queue Num: {REORDER_QUEUE_NUM}")
        print(f"Hash Algorithm: Murmur3 x86_32, seed=0x8BADF00D")
        print_separator()

        analyze_reorder_distribution(sip, dip, sport, dport, ecmp_start, num_pkts)

    print_separator()
    print("\nNotes:")
    print("  - Hash Input: 4-tuple (sip,dip,sport,dport) + ecmp_counter%8")
    print("  - Queue Index: ecmp_counter % REORDER_QUEUE_NUM")
    print("  - Each flow maintains its own ecmp_counter (starts at 0)")
    print("  - NS3 style: sport starts at 10000, dport starts at 100, both increment per flow")


if __name__ == '__main__':
    main()
