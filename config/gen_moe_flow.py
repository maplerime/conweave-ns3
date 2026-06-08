#!/usr/bin/env python3
import random

# 配置
total_nodes = 320
num_senders = 256          # MoE发送节点数
num_receivers = 8          # MoE接收节点数
num_rounds = 30            # MoE流轮数
moe_flow_size = 8192       # 8KB

# 计算理想完成时间：每轮流量 / 带宽
round_duration = 1500000   # 每轮持续时间1.5ms (单位：ns)

bg_flows_per_node = 2
bg_flow_size = 749857      # 约732KB

# 时间设置 (秒)
bg_start_time = 2.0
moe_start_time = bg_start_time + round_duration * 1e-9  # 2.0015s

# 使用相同的种子保证一致性
random.seed(42)
sender_nodes = sorted(random.sample(range(total_nodes), num_senders))
remaining_nodes = [n for n in range(total_nodes) if n not in sender_nodes]
receiver_nodes = sorted(random.sample(remaining_nodes, num_receivers))
bg_available_nodes = [n for n in remaining_nodes if n not in receiver_nodes]

print(f"MoE发送节点: {len(sender_nodes)}")
print(f"MoE接收节点: {receiver_nodes}")
print(f"背景流可用节点: {len(bg_available_nodes)}")

# 生成MoE专家流 (每轮在前一轮结束后开始)
moe_lines = []
for round_num in range(num_rounds):
    round_start_time = moe_start_time + round_num * round_duration * 1e-9
    for sender in sender_nodes:
        for receiver in receiver_nodes:
            # 格式: src dst pg size time_s
            moe_lines.append(f"{sender} {receiver} 3 {moe_flow_size} {round_start_time:.9f}\n")

print(f"Generated {len(moe_lines)} MoE expert flows ({num_rounds} rounds)")
print(f"背景流: {bg_start_time:.9f}s")
print(f"MoE Round 0: {moe_start_time:.9f}s")
print(f"MoE Round {num_rounds-1}: {(moe_start_time + (num_rounds-1) * round_duration * 1e-9):.9f}s")

# 生成背景流：2.0s开始
bg_lines = []
for src in bg_available_nodes:
    possible_dsts = [n for n in bg_available_nodes if n != src]
    selected_dsts = random.sample(possible_dsts, min(bg_flows_per_node, len(possible_dsts)))
    for dst in selected_dsts:
        bg_lines.append(f"{src} {dst} 3 {bg_flow_size} {bg_start_time:.9f}\n")

print(f"Generated {len(bg_lines)} background flows (1 round)")

# 统计
bg_senders = set()
bg_receivers = set()
for line in bg_lines:
    parts = line.split()
    bg_senders.add(int(parts[0]))
    bg_receivers.add(int(parts[1]))

moe_total = len(moe_lines) * moe_flow_size
bg_total = len(bg_lines) * bg_flow_size
total = moe_total + bg_total

print(f"\n===== 流量统计 =====")
print(f"背景流发送节点数: {len(bg_senders)} / {len(bg_available_nodes)}")
print(f"背景流接收节点数: {len(bg_receivers)} / {len(bg_available_nodes)}")
print(f"\n流量占比:")
print(f"  MoE专家流: {len(moe_lines):,} 条 × {moe_flow_size/1024:.0f}KB = {moe_total/1024/1024:.0f}MB ({moe_total/total*100:.2f}%)")
print(f"  背景流: {len(bg_lines):,} 条 × {bg_flow_size/1024:.0f}KB = {bg_total/1024/1024:.0f}MB ({bg_total/total*100:.2f}%)")

# 写入文件
with open("/home/eileen/ns-allinone-3.19/ns-3.19/config/moe_expert_256to8_8round_8KB.txt", "w") as f:
    # 第一行：总流数（与traffic_gen.py生成的格式一致）
    f.write(f"{len(moe_lines) + len(bg_lines)} \n")
    f.writelines(moe_lines)
    f.writelines(bg_lines)

print(f"\nFile saved to: moe_expert_256to8_8round_8KB.txt")

# 显示格式示例
print(f"\n格式示例:")
print("MoE流:")
print(moe_lines[0], end='')
print(moe_lines[2048], end='')
print("背景流:")
print(bg_lines[0], end='')
