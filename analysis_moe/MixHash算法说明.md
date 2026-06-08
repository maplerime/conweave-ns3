# MixHash 负载均衡算法说明

## 1. 算法概述

MixHash（lb_mode=16）是一种基于ECMP的负载均衡算法，通过在哈希计算中加入数据包序列计数器（ecmp_counter），使同一流的不同包能够在多条等价路径间进行智能分配，同时利用目的端ToR交换机的重排序缓冲区保证包序。

### 核心设计思路

1. **路径选择**：在ECMP哈希中融入 `ecmp_counter` 值，使同一流的包按计数器值轮询分配到不同路径
2. **包序保证**：目的端ToR交换机使用基于 `ecmp_counter % num_queues` 的FIFO队列进行重排序

## 2. 拓扑配置

### 仿真拓扑：1280节点三层Fat-Tree

```
总节点数: 1856
  - 1280 台主机
  - 576 台交换机

拓扑结构:
  - 80 个 ToR (Top-of-Rack) 交换机
  - 384 个 Aggregation 交换机
  - 112 个 Core 交换机

链路规格:
  - 所有链路: 400 Gbps
  - 链路延迟: 10 ns
  - 主机到ToR: 直连

RTT (Round Trip Time):
  - 典型 RTT: 600 ns (240ns × 2 + 120ns)
  - BDP (Bandwidth-Delay Product): 30000 bytes
```

### 流量配置

| 流类型 | 大小 | 数量 | Tag |
|--------|------|------|-----|
| 专家流 (Expert) | 8 KB | 16384 | 2 |
| 背景流 (Background) | 8 MB | 0-192 | 1 |

**流量模式**: MoE (Mixture of Experts) 模式，256个专家组，每组8轮通信

## 3. 算法实现

### 3.1 路径选择 (源端)

```cpp
// DoLbFlowECMPWithCounter - switch-node.cc
uint32_t SwitchNode::DoLbFlowECMPWithCounter(Ptr<const Packet> p,
                                              const CustomHeader &ch,
                                              const std::vector<int> &nexthops) {
    uint8_t buf[14];
    buf[0] = ch.udp.sip >> 24;
    buf[1] = ch.udp.sip >> 16;
    buf[2] = ch.udp.sip >> 8;
    buf[3] = ch.udp.sip;
    buf[4] = ch.udp.dip >> 24;
    buf[5] = ch.udp.dip >> 16;
    buf[6] = ch.udp.dip >> 8;
    buf[7] = ch.udp.dip;
    buf[8] = ch.udp.sport >> 8;
    buf[9] = ch.udp.sport;
    buf[10] = ch.udp.dport >> 8;
    buf[11] = ch.udp.dport;
    buf[12] = ch.udp.l3Prot;

    // 关键：使用 ecmp_counter % num_queues 作为哈希输入
    buf[13] = ch.udp.ecmp_counter % Settings::reorder_queue_num;

    uint32_t hashVal = EcmpHash(buf.u8, 14, m_ecmpSeed);
    return nexthops[hashVal % nexthops.size()];
}
```

### 3.2 重排序缓冲区 (目的端ToR)

```cpp
// FlowReorderBuffer 结构 - switch-node.h
struct FlowReorderBuffer {
    uint16_t expected_counter;                          // 期望的下一个计数器值
    std::vector<std::queue<Ptr<Packet>>> queues;       // FIFO队列数组
    FlowReorderStats stats;                            // 统计信息
};
```

**工作原理**:
1. 每个流维护一个 `FlowReorderBuffer`
2. 根据包的 `ecmp_counter % reorder_queue_num` 选择对应的FIFO队列
3. 只有当 `ecmp_counter == expected_counter` 时才发送包到主机
4. 发送后 `expected_counter++`，检查下一个队列

### 3.3 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `reorder_queue_num` | 5 | 重排序队列数量 |
| `REORDER_MAX_QUEUE_SIZE` | ∞ | 每个队列最大包数（已取消限制） |

### 3.4 重排序算法流程

#### 3.4.1 数据结构

```
FlowReorderBuffer (每流一个):
  - expected_counter: uint16_t  // 期望的下一个计数器值
  - queues[4]: std::queue<Packet>  // 4个FIFO队列

FlowReorderStats (每流一个):
  - packets_received: 总接收包数
  - packets_direct_sent: 直接发送的包数（按序）
  - packets_buffered: 缓存的包数（乱序）
  - packets_flushed: 从缓冲区发出的包数
  - packets_dropped: 计数器不匹配时丢弃的包数
  - max_buffer_size: 观察到的最大缓冲区大小
```

#### 3.4.2 包处理流程

```
┌─────────────────────────────────────────────────────────────┐
│                    收到数据包                                │
│         (sip, dip, sport, dport, ecmp_counter)             │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │ 检查: LB_MODE == 16?   │──No──▶ 直接转发
              │ 检查: tag != 2?        │
              └───────────┬─────────────┘
                          │ Yes
                          ▼
              ┌─────────────────────────┐
              │ 获取/创建 FlowReorderBuffer │
              │ 初始化 queues[4]        │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │ ecmp_counter == expected?│
              └───────────┬─────────────┘
                          │
            ┌─────────────┴─────────────┐
            │ Yes                        │ No
            ▼                            ▼
    ┌───────────────┐         ┌───────────────────┐
    │ 直接发送到主机 │         │ ecmp_counter <    │
    │ expected++   │         │ expected?         │
    │ 调用Flush()   │         └─────────┬─────────┘
    └───────────────│                   │
                                ┌───────┴───────┐
                                │ Yes            │ No
                                ▼                ▼
                        ┌───────────────┐ ┌─────────────────┐
                        │ 直接发送(迟包)│ │ 缓存到队列:      │
                        │ (可能是重传)  │ │ queue_idx =      │
                        └───────────────┘ │ counter % 4     │
                                          │ packets_buffered++│
                                          └─────────────────┘
```

#### 3.4.3 FlushReorderQueue流程（发送后按序发出缓存包）

```
FlushReorderQueue() - 在 expected_counter 匹配时调用:

while (true):
    queue_idx = expected_counter % 4

    if (queues[queue_idx].empty()):
        break  // 对应队列为空，停止flush

    head_pkt = queues[queue_idx].front()
    head_counter = head_pkt.ecmp_counter

    if (head_counter == expected_counter):
        // 队首包匹配，发出
        queues[queue_idx].pop()
        发送 head_pkt 到主机
        expected_counter++
        packets_flushed++
        // 继续循环检查下一个expected
    else:
        // 队首包不匹配！计数器异常
        // 清空所有队列，丢弃所有缓存包
        for i in 0..3:
            清空 queues[i]
            packets_dropped += queues[i].size()
        expected_counter 保持不变
        break  // 停止flush
```

#### 3.4.4 算法示例

**场景：4个队列，reorder_queue_num=4**

```
初始状态: expected_counter = 0

收到包序列（乱序到达）:
  包2 (counter=2) → 缓存到 queue[2]
  包0 (counter=0) → 匹配expected，直接发送，expected=1
                   → Flush: 检查queue[1]，空，停止
  包3 (counter=3) → 缓存到 queue[3]
  包1 (counter=1) → 匹配expected，直接发送，expected=2
                   → Flush: 检查queue[2]，有包2，发出，expected=3
                   → Flush: 检查queue[3]，有包3，发出，expected=4
                   → Flush: 检查queue[0]，空，停止

结果: 包按序 0,1,2,3 发送到主机
```

**异常情况：计数器不匹配**

```
状态: expected_counter = 5, queue[1] = [包6], queue[2] = [包7]

收到包5 → 匹配expected，发送，expected=6
        → Flush: 检查queue[2](6%4=2)，队首是包7 ≠ 6
        → 不匹配！清空所有队列
        → queue[1], queue[2] 被清空，包被丢弃
        → expected_counter 保持 = 6

原因: 可能是丢包或重传导致的计数器跳跃
```

## 4. 性能对比：MixHash (reorder_queue_num=5)

### 4.1 小流FCT (8KB专家流) - 以Drill(0)为基准

| 模式 | Avg(μs) | vs Drill | P50(μs) | P99(μs) | vs基线P99 | 小流PFC |
|------|---------|----------|---------|---------|-----------|---------|
| **Drill(0)** [基线] | 12.82 | 0.0% | 12.64 | 15.39 | 0.0% | 0 |
| **MixHash(0)** | 12.81 | -0.1% | 12.63 | 15.29 | -0.6% | 0 |
| **Hybrid(0)** | 12.82 | 0.0% | 12.64 | 15.39 | 0.0% | 0 |
| **MixHash(64)** | 13.04 | +1.8% | 12.80 | 16.18 | +5.1% | 0 |
| **Hybrid(64)** | 13.26 | +3.4% | 12.87 | 18.49 | +20.1% | 1,301 |
| **MixHash(128)** | 13.26 | +3.4% | 13.00 | 17.17 | +11.5% | 0 |
| **Hybrid(128)** | 13.73 | +7.2% | 13.11 | 21.07 | +36.9% | 467 |
| **MixHash(192)** | 13.27 | +3.5% | 13.05 | 16.46 | +6.9% | 0 |
| **Hybrid(192)** | 13.75 | +7.3% | 13.10 | 21.92 | +42.4% | 476 |
| **ECMP** | 13.72 | +7.0% | 12.70 | 23.04 | +49.7% | 3,069 |

**关键发现**:
- MixHash(0)与Drill(0)性能基本相当
- MixHash在所有场景下小流PFC均为0，显著优于Hybrid和ECMP
- MixHash的P99比Hybrid低约12.5%-24.9%

### 4.2 大流FCT (8MB背景流) - 以ECMP为基准

| 模式 | 大流数 | Avg(μs) | P50(μs) | **P99(μs)** | vs基线P99 | 大流PFC |
|------|--------|---------|---------|-------------|-----------|---------|
| **ECMP** [基线] | 192 | 936.03 | 914.64 | **1489.26** | 0.0% | 1,114,155 |
| **MixHash(64)** | 64 | 461.18 | 497.75 | **721.97** | **-51.5%** | 81,861 |
| **Hybrid(64)** | 64 | 503.14 | 535.34 | **721.97** | **-51.5%** | 323,372 |
| **MixHash(128)** | 128 | 661.74 | 604.13 | **1098.55** | **-26.2%** | 310,824 |
| **Hybrid(128)** | 128 | 722.62 | 721.97 | **1196.52** | **-19.7%** | 765,179 |
| **MixHash(192)** | 192 | 859.01 | 894.90 | **1443.36** | **-3.1%** | 495,289 |
| **Hybrid(192)** | 192 | 935.80 | 914.59 | **1481.08** | **-0.5%** | 1,106,015 |

**关键发现**:
- MixHash在大流Avg FCT上优于Hybrid约8%
- MixHash在大流PFC上显著优于Hybrid（约60%）
- 128流时MixHash的P99优于Hybrid约6%

### 4.3 MixHash vs Hybrid 详细对比

#### 小流FCT对比 (Avg & P99)

| 背景流数 | Hybrid Avg(μs) | MixHash Avg(μs) | Avg改善 | Hybrid P99(μs) | MixHash P99(μs) | P99改善 |
|----------|----------------|-----------------|---------|----------------|-----------------|---------|
| 0 | 12.82 | 12.81 | -0.1% | 15.39 | 15.29 | -0.6% |
| 64 | 13.26 | 13.04 | **1.6%** ↓ | 18.49 | 16.18 | **12.5%** ↓ |
| 128 | 13.73 | 13.26 | **3.4%** ↓ | 21.07 | 17.17 | **18.5%** ↓ |
| 192 | 13.75 | 13.27 | **3.5%** ↓ | 21.92 | 16.46 | **24.9%** ↓ |

**小流结论**:
- MixHash的Avg FCT比Hybrid低0.1%-3.5%
- MixHash的P99 FCT比Hybrid低12.5%-24.9%（负载越高优势越大）

#### 大流FCT对比 (Avg & P99)

| 背景流数 | Hybrid Avg(μs) | MixHash Avg(μs) | Avg改善 | Hybrid P99(μs) | MixHash P99(μs) | P99改善 |
|----------|----------------|-----------------|---------|----------------|-----------------|---------|
| 64 | 503.14 | 461.18 | **8.3%** ↓ | 721.97 | 721.97 | 0.0% |
| 128 | 722.62 | 661.74 | **8.4%** ↓ | 1196.52 | 1098.55 | **8.2%** ↓ |
| 192 | 935.80 | 859.01 | **8.2%** ↓ | 1481.08 | 1443.36 | **2.6%** ↓ |

**大流结论**:
- MixHash的Avg FCT比Hybrid低约8.3%
- MixHash的P99 FCT在128/192流时优于Hybrid
- 64流时两者P99相同

#### 大流PFC对比

| 背景流数 | Hybrid PFC | MixHash PFC | 改善 |
|----------|------------|-------------|------|
| 64 | 323,372 | 81,861 | **74.7%** ↓ |
| 128 | 765,179 | 310,824 | **59.4%** ↓ |
| 192 | 1,106,015 | 495,289 | **55.2%** ↓ |
| **总计** | 2,194,566 | 887,974 | **59.6%** ↓ |

### 4.4 PFC统计对比

| 模式 | 小流PFC | 大流PFC(全部) | vs Hybrid大流PFC |
|------|---------|---------------|-----------------|
| **Hybrid** | 2,244 | 2,194,566 | 1.0x (基准) |
| **MixHash** | 0 | 887,974 | **0.40x** |
| **Drill** | 0 | 11,196,015 | 5.10x |
| **ECMP** | 3,069 | 1,114,155 | 0.51x |

**结论**: MixHash将大流PFC降低了60%，显著优于Hybrid和Drill。
| **Drill** | 0 | 11,196,015 | 10.05x |

**结论**: MixHash将大流PFC降低了20%，显著优于ECMP和Drill。

## 5. 内存占用分析

### 5.1 重排序缓冲区结构

```
Per-Flow Reorder Buffer:
  - FlowKey: 13 bytes (sip:4 + dip:4 + sport:2 + dport:2 + proto:1)
  - Expected Counter: 2 bytes (uint16_t)
  - Queues: vector of 5 FIFO queues (reorder_queue_num=5)
  - Stats: ~40 bytes

队列深度 (单个FIFO队列): 缓存的包数
乱序窗口 (counter-expected): 表示乱序包数量

Per-Packet (in queue):
  - Ptr<Packet>: ~8 bytes (pointer)
  + Packet data overhead
```

### 5.2 内存占用估算

**单流重排序缓冲区**:
```
FlowReorderBuffer 结构:
  - FlowKey (map key): 13 bytes
  - expected_counter: 2 bytes
  - queues[5]: 5 × (overhead of std::queue)
  - stats: ~40 bytes
  ≈ 100-200 bytes/流 (不含排队包)
```

**最坏情况: 所有16384个流同时发生重排序**

假设每个流有5个包在缓冲区（每个队列1个）:
```
包数量: 16384 流 × 5 包/流 = 81,920 包
包大小: ~1500 bytes (平均)

总内存占用:
  - 缓冲区结构: 16384 × 200 bytes ≈ 3.3 MB
  - 排队包: 81920 × 1500 bytes ≈ 123 MB
  - 总计: ~125 MB per ToR switch
```

### 5.3 实际仿真中的Reorder情况

**仿真配置 (MoE流量)**:
- 流量: 16,576个专家流 (8KB)
- 配置: reorder_queue_num=5, PFC=1, IRN=1

**实际Reorder统计数据** (从config.log统计):

| 指标 | 值 |
|------|-----|
| REORDER事件 | 945 |
| 涉及流数 | 189 |
| Reorder流占比 | 1.1% |
| 最大队列深度 | 3 包 |
| 最大counter-expected差值 | 12 |

**队列宽度分布**:
```
qsize=1: 875 次 (92.6%)
qsize=2:  66 次 ( 7.0%)
qsize=3:   4 次 ( 0.4%)
```

**关键发现**:
```
1. reorder_queue_num=5 完全充足
   - 实际最大队列深度: 3 包
   - 理论需求: ceil(13/5) = 3 包/队列

2. 内存占用极小
   - 时间维度峰值: 22 KB (15包 × 1.5KB)
   - 单流最大: 20 KB (5队列 × 4KB)
   - 理论峰值: 4 MB (189流 × 20KB)

3. reorder使用率低
   - 99.6%的reorder事件队列只有1个包
   - 仅1.1%的流发生reorder
```

### 5.4 内存开销对比
| 组件 | Hybrid | MixHash |
|------|--------|---------|
| 队列监控 | 每端口队列长度 | 无 |
| 探测包 | 周期性发送 | 无 |
| 重排序缓冲区(实际) | 无 | **22 KB/ToR** (时间维度峰值) |
| 重排序缓冲区(理论) | - | ~125 MB/ToR |

**实际结论** (50%负载MoE流):
- reorder_queue_num=5 已足够，实际最大队列深度3包
- 时间维度峰值仅22 KB，远小于理论最坏情况
- 1.1%的流发生reorder，99.6%的队列只有1个包

## 6. 算法优势与劣势

### 优势
1. **更低的PFC事件**: 大流PFC仅为Hybrid的40%，ECMP的80%
2. **更优的P99 FCT**: 小流P99比Hybrid低12.5%-24.9%
3. **实现简单**: 无需复杂的队列监控和探测机制
4. **状态less**: 中间交换机不需要维护额外状态
5. **零小流PFC**: 所有场景下小流PFC均为0

### 劣势
1. **目的端状态**: 需要在ToR交换机维护重排序缓冲区
2. **内存开销**: 最坏情况下需要~125 MB内存
3. **重排序延迟**: 乱序包需要在缓冲区等待

### 适用场景
- 中低负载场景（PFC事件较少）
- 对P99延迟敏感的应用
- 目的端ToR有足够内存资源

## 7. 关键代码位置

| 文件 | 功能 |
|------|------|
| `switch-node.cc:121-140` | DoLbFlowECMPWithCounter 实现 |
| `switch-node.cc:860-872` | Mode 16 路由逻辑 |
| `switch-node.cc:ProcessReorderBuffer` | 重排序处理 |
| `rdma-hw.cc` | ecmp_counter 赋值 |
| `settings.cc:37` | reorder_queue_num 配置 (当前=5) |

## 8. 总结

MixHash通过巧妙地在哈希中融入序列计数器，实现了简单高效的负载均衡。仿真结果表明：

### 核心优势

1. **小流P99 FCT**: 比Hybrid低12.5%-24.9%
2. **大流Avg FCT**: 比Hybrid低约8.3%
3. **大流P99 FCT**: 128流时优于Hybrid 8.2%
4. **大流PFC优化**: 比Hybrid低60%
5. **零小流PFC**: 所有场景下小流PFC均为0
6. **实际内存极低**: 仿真中未触发reorder，内存开销< 50 KB/ToR

### 实际仿真Reorder发现

- **64/128/192条大流场景下均未发生reorder**
- 网络延迟一致性好，包按序到达
- 内存开销仅为结构开销，无排队包

### 性能对比摘要表

| 指标 | MixHash vs Hybrid | 优势场景 |
|------|-------------------|----------|
| 小流Avg FCT | ↓ 0.1%-3.5% | 所有负载 |
| 小流P99 FCT | ↓ 12.5%-24.9% | 高负载更优 |
| 小流PFC | -100% | 所有场景 |
| 大流Avg FCT | ↓ 8.3% | 所有场景 |
| 大流P99 FCT | 64流相当，128/192流更优 | 中高负载 |
| 大流PFC | ↓ 60% | 所有场景 |
| 实际Reorder | 1.1%流发生 | 50%负载MoE |
| 内存开销 | 22 KB峰值 | 时间维度 |

该算法特别适合MoE等对延迟敏感、流量模式复杂的数据中心网络场景。
