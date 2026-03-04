# FlowSlice 机制总结

## 概述

FlowSlice 是一种基于RTT差异的动态流量切片负载均衡机制，在NS-3网络模拟器中实现。其核心思想是将长流动态切分为多个切片（slice），每个切片独立选择路径，通过测量多条路径的RTT差异来动态调整切片大小。

---

## 核心设计原理

### 1. 动态切片策略

FlowSlice根据路径RTT差异动态调整切片大小：

| RTT差异 | 切片大小 | 策略 |
|---------|---------|------|
| < 0.5μs | 1包 | 频繁切换，探索路径 |
| 0.5-1μs | 2-16包 | 中等切换频率 |
| 1-2μs | 16-24包 | 较少切换 |
| 2-5μs | 24-32包 | 保持路径 |
| > 5μs | 32包 | 固定在最优路径 |

**关键设计思想**：
- **RTT差异小** → 切片小 → 频繁切换路径 → 探索所有路径
- **RTT差异大** → 切片大 → 保持路径 → 利用已知最优路径

### 2. RTT测量机制

```
源端ToR                    中间交换机                    目的端ToR
   |                          |                            |
   |--[DATA包, slice_id]----->|----[DATA包]-------------->|
   |   (含timestamp_tx)       |   (slice_id映射到端口)    |
   |                          |                            |
   |--[TAIL包]----------------|----[TAIL包]-------------->|
   |   (触发RTT计算)          |                            |
   |                          |                            |
   |<--[RTT Feedback]---------|<----------------------------|
   |   (rtt_diff = max_rtt - min_rtt)                    |
```

**RTT计算过程**：
1. 源端ToR为每个包打上发送时间戳 `timestamp_tx`
2. 目的端ToR记录每个slice的首包到达时间 `phase0_rx_time`
3. 收到TAIL包时，计算当前slice的RTT
4. 计算所有活跃路径的 max_rtt 和 min_rtt
5. RTT差异 = max_rtt - min_rtt
6. 将RTT差异反馈给源端ToR

---

## 架构组件

### 1. FlowSliceTag (数据包头)

位于 `flowslice-sender.cc`，携带切片信息：

```cpp
class FlowSliceTag {
    uint64_t m_slice_id;        // 切片ID: (flow_key << 16) | slice_num
    uint64_t m_flow_key;        // 流标识: 五元组哈希
    uint32_t m_sequence_number; // 流内序列号
    uint64_t m_timestamp_tx;    // 发送时间戳
    uint64_t m_timestamp_tail;  // 上一个TAIL包时间戳
    uint32_t m_flag;            // DATA(0) 或 TAIL(1)
};
```

**标签大小**: 36字节

### 2. FlowSliceSender (源端ToR)

位于 `flowslice-sender.cc:114-383`，负责：

| 功能 | 描述 |
|------|------|
| 包标记 | 为每个包添加FlowSliceTag |
| 切片切换 | 达到切片大小后切换路径 |
| RTT反馈处理 | 接收并处理目的端的RTT反馈 |
| 路径选择 | 基于本地队列选择最短队列路径 |

**关键参数**：
```cpp
uint32_t m_maxPaths = 4;           // 最大路径数
uint32_t m_minSliceTime = 1000ns;  // 最小切片间隔
uint32_t m_minSlicePackets = 1;    // 最小切片大小
uint32_t m_maxSlicePackets = 32;   // 最大切片大小
double m_safetyFactor = 0.8;       // 安全系数
```

**RTT平滑**：使用EWMA (Exponentially Weighted Moving Average)
```cpp
measured_rtt_diff = (measured_rtt_diff * 7 + rtt_diff_ns) / 8;
```

### 3. SwitchNode (中间交换机)

位于 `switch-node.cc:223-390`，实现 `DoLbFlowSlice()`：

**核心功能**：
1. **Per-Slice端口映射**: 同一切片的所有包使用相同端口
   ```cpp
   std::map<uint64_t, uint32_t> m_sliceIdToPort;  // <slice_id, out_port>
   ```

2. **路径排除**: 切片切换时避免使用上一条路径
   ```cpp
   std::map<uint64_t, uint32_t> m_flowPrevSlicePort; // <flow_key, prev_port>
   ```

3. **RTT测量** (目的端ToR): 记录每个路径的RTT
   ```cpp
   std::map<uint64_t, std::map<uint32_t, PathRttInfo>> m_flowPathRtt;
   ```

---

## 工作流程

### 流程图

```
流开始
   |
   v
[创建FlowSliceSenderState]
   |
   v
[发送DATA包，打上slice_tag]
   |
   v
[达到切片大小?] --> 否 --> 继续发送
   |
   是
   v
[发送TAIL包]
   |
   v
[切换路径，更新slice_id]
   |
   v
目的端ToR接收TAIL包
   |
   v
[计算各路径RTT，得到max-min差异]
   |
   v
[发送RTT Feedback给源端]
   |
   v
源端更新measured_rtt_diff (EWMA)
   |
   v
[重新计算切片大小]
   |
   v
继续发送...
```

### 代码执行路径

**发送端 (`flowslice-sender.cc:291-340`)**:
```cpp
ProcessPacket():
    1. 获取或创建流状态
    2. 生成slice_id = (flow_key << 16) | current_slice
    3. 检查是否需要切换切片
    4. 为包添加FlowSliceTag
    5. 更新状态
```

**交换机 (`switch-node.cc:223-390`)**:
```cpp
DoLbFlowSlice():
    1. 检查是否有FlowSliceTag
    2. 如果是目的端ToR，进行RTT测量
    3. 检查slice_id是否已有端口映射
    4. 如果没有，选择最短队列端口（排除上一条路径）
    5. 缓存端口映射
```

---

## 关键数据结构

### 发送端状态

```cpp
struct FlowSliceSenderState {
    uint64_t flow_key;              // 流标识
    uint32_t current_slice;         // 当前切片号
    uint32_t packets_in_slice;      // 当前切片已发包数
    uint32_t current_slice_size;    // 当前切片大小
    uint32_t current_path;          // 当前路径
    uint64_t measured_rtt_diff;     // 测量的RTT差异(ns)
    Time last_switch_time;          // 上次切换时间
    uint64_t last_tail_time;        // 上次TAIL包时间
    Time phase0_tx_time;            // 当前phase首包发送时间
    Time phase0_rx_time;            // 当前phase首包接收时间
};
```

### 路径RTT信息

```cpp
struct PathRttInfo {
    uint64_t phase0_tx_time;  // 首包发送时间戳
    Time phase0_rx_time;      // 首包接收时间
    uint32_t path_id;         // 路径标识
    bool active;              // 是否活跃
};
```

---

## 使用方式

### 启用FlowSlice

在模拟参数中设置：
```bash
--lb_mode 11
```

### 配合其他参数

```bash
--pfc 1      # 启用优先级流控
--irn 0      # 禁用IRN
--flow_mode  # 流量模式
```

---

## 与其他负载均衡算法对比

| 算法 | lb_mode | 策略 |
|------|---------|------|
| ECMP | 0 | 基于流的哈希 |
| DRILL | 2 | 动态选择最短队列 |
| Letflow | 6 | 基于路径拥塞 |
| Hybrid | 10 | 小流ECMP + 大流DRILL |
| **FlowSlice** | **11** | **基于RTT差异的动态切片** |

---

## 优势特点

1. **自适应**: 根据实时RTT差异动态调整切片大小
2. **路径探索**: RTT差异小时频繁切换，探索所有路径
3. **路径利用**: RTT差异大时保持路径，利用已知最优路径
4. **低开销**: 只有TAIL包触发RTT计算，开销小
5. **避免路径振荡**: 切片切换时排除上一条路径

---

## 文件位置

| 文件 | 描述 |
|------|------|
| `src/point-to-point/model/flowslice-sender.h` | 发送端头文件 |
| `src/point-to-point/model/flowslice-sender.cc` | 发送端实现 |
| `src/point-to-point/model/switch-node.h` | 交换机头文件 |
| `src/point-to-point/model/switch-node.cc` | 交换机实现 (lb_mode=11) |

---

## 版本历史

- **v1.0**: 初始实现，固定切片大小
- **v2.0**: 添加RTT差异测量和动态切片大小计算
- **v3.0**: 添加路径排除逻辑，避免切片切换时使用相同路径

---

## 参考代码位置

- **动态切片计算**: `flowslice-sender.cc:252-289`
- **RTT反馈处理**: `flowslice-sender.cc:342-370`
- **交换机负载均衡**: `switch-node.cc:223-390`
- **RTT测量**: `switch-node.cc:243-314`
- **路径选择**: `switch-node.cc:353-389`
