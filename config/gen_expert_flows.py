#!/usr/bin/env python3
import random
import sys

# 配置
NUM_NODES = 320
NUM_EXPERT_NODES = 256
SMALL_FLOW_SIZE = 8 * 1024  # 8KB
LARGE_FLOW_SIZE = 16 * 1024 * 1024  # 16MB
NUM_ROUNDS = 8
TARGETS_PER_ROUND = 8
START_TIME_NS = 1000000000  # 1秒

# 大流数量配置 - 三个文件
LARGE_FLOW_COUNTS = [64, 128, 192]

# 设置随机种子以便复现
random.seed(42)

# 生成节点列表
all_nodes = list(range(NUM_NODES))
expert_nodes = sorted(random.sample(all_nodes, NUM_EXPERT_NODES))
remaining_nodes = sorted([n for n in all_nodes if n not in expert_nodes])

print(f"专家节点: {len(expert_nodes)}")
print(f"剩余节点: {len(remaining_nodes)}")

# 端口分配
small_flow_pg = 0
large_flow_pg = 1

# 为每个大流数量生成一个文件
for large_count in LARGE_FLOW_COUNTS:
    flows = []
    sport_counter = 1

    # 1. 生成专家节点的小流 (8KB) - 每个文件相同
    print(f"\n生成专家小流...")
    for expert in expert_nodes:
        # 每轮选择8个不同的目标节点
        for round in range(NUM_ROUNDS):
            targets = random.sample(all_nodes, TARGETS_PER_ROUND)
            for target in targets:
                if expert != target:  # 不发送给自己
                    flows.append({
                        'src': expert,
                        'dst': target,
                        'sport': sport_counter,
                        'dport': 5000,
                        'size': SMALL_FLOW_SIZE,
                        'pg': small_flow_pg,
                        'start_time': START_TIME_NS
                    })
                    sport_counter += 1

    small_flow_count = len([f for f in flows if f['size'] == SMALL_FLOW_SIZE])
    print(f"生成小流数量: {small_flow_count}")

    # 2. 生成大流 (16MB)
    print(f"生成{large_count}条大流...")
    for _ in range(large_count):
        src = random.choice(remaining_nodes)
        dst = random.choice([n for n in remaining_nodes if n != src])
        flows.append({
            'src': src,
            'dst': dst,
            'sport': sport_counter,
            'dport': 5000,
            'size': LARGE_FLOW_SIZE,
            'pg': large_flow_pg,
            'start_time': START_TIME_NS
        })
        sport_counter += 1

    # 打乱流顺序（模拟同时发送）
    random.shuffle(flows)

    # 输出流文件
    output_file = f"config/expert_flows_{large_count}.txt"
    with open(output_file, 'w') as f:
        for flow in flows:
            f.write(f"{flow['src']} {flow['dst']} {flow['sport']} {flow['dport']} "
                    f"{flow['size']} {flow['pg']} {flow['start_time']}\n")

    print(f"文件已生成: {output_file}")
    print(f"  小流(PG={small_flow_pg}): {small_flow_count}条")
    print(f"  大流(PG={large_flow_pg}): {large_count}条")
    print(f"  总流数量: {len(flows)}")

print("\n所有流文件生成完成！")
