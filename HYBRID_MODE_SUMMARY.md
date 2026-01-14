# ECMP + DRILL Hybrid Load Balancing Mode

## Implementation Summary

### Files Modified:

1. **src/point-to-point/model/settings.h**
   - Added `lb_hybrid_enabled`, `lb_hybrid_threshold`, `lb_hybrid_ratio`

2. **src/point-to-point/model/settings.cc**
   - Initialized hybrid mode parameters

3. **src/point-to-point/model/switch-node.h**
   - Added `DoLbHybrid()` function
   - Added `m_flowUseDrill` and `m_flowByteCount` maps

4. **src/point-to-point/model/switch-node.cc**
   - Implemented `DoLbHybrid()` function (lines 149-206)
   - Added `case 10` in load balancer switch

5. **scratch/network-load-balance.cc**
   - Added hybrid mode config file reading

6. **run.py**
   - Added "hybrid" to `lb_modes` (mode 10)
   - Added `--hybrid_threshold` and `--hybrid_ratio` parameters

---

## Hybrid Mode Algorithm

```
DoLbHybrid(p, ch, nexthops):
  1. Calculate flow hash (5-tuple: sip, dip, sport, dport)
  2. Check if flow seen before:
     - NEW FLOW:
       * If threshold > 0: Start with ECMP
       * If threshold = 0: Randomly decide based on ratio
     - EXISTING FLOW: Use previous decision
  3. For threshold mode: Track bytes sent
     - Switch to DRILL when flow >= threshold
  4. Route: ECMP or DRILL based on decision
```

---

## Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--lb hybrid` | Enable hybrid mode (LB_MODE = 10) | - |
| `--hybrid_threshold N` | Flow size threshold in bytes (flows >= N use DRILL) | 100000 (100KB) |
| `--hybrid_ratio R` | Ratio of flows using DRILL (0.0-1.0, only if threshold=0) | 0.5 |

---

## Usage Examples

### Threshold-based mode (recommended)
```bash
# 100KB threshold
./run.py --lb hybrid --hybrid_threshold 100000 --netload 40

# 50KB threshold
./run.py --lb hybrid --hybrid_threshold 50000 --netload 40

# 1MB threshold
./run.py --lb hybrid --hybrid_threshold 1000000 --netload 40
```

### Ratio-based mode
```bash
# 50% of flows use DRILL
./run.py --lb hybrid --hybrid_threshold 0 --hybrid_ratio 0.5 --netload 40

# 30% of flows use DRILL
./run.py --lb hybrid --hybrid_threshold 0 --hybrid_ratio 0.3 --netload 40
```

---

## How It Works

```
Small Flows (< threshold)          Large Flows (>= threshold)
┌─────────────┐                    ┌─────────────┐
│     ECMP    │                    │    DRILL    │
│   (Fast)    │                    │  (Balanced) │
│             │                    │             │
│ Fixed path  │                    │ Queue-aware │
│  per flow   │                    │ per packet  │
└─────────────┘                    └─────────────┘
```

---

## Algorithm Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Packet Arrival                          │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                   ┌─────────────────────┐
                   │ Calculate Flow Hash │
                   │  (5-tuple)          │
                   └──────────┬──────────┘
                              │
                              ▼
                   ┌─────────────────────┐
                   │ Is flow in cache?   │
                   └──────────┬──────────┘
                         ┌────┴────┐
                         │         │
                        NO        YES
                         │         │
                         ▼         ▼
              ┌──────────────┐  ┌────────────────┐
              │ threshold>0? │  │ Use cached     │
              └────┬────┬───┘  │ decision       │
                   │    │       └────────┬───────┘
                  YES   NO                │
                   │    │                 │
                   ▼    ▼                 ▼
          ┌─────────┐ ┌─────────┐  ┌──────────────┐
          │ Use     │ │ Random  │  │ Update bytes  │
          │ ECMP    │ │ based   │  │ >= threshold? │
          │ Track   │ │ on ratio│  └──────┬───────┘
          │ bytes   │ └────┬────┘         │
          └────┬────┘      │              │
               │           │         ┌────┴────┐
               │           │        NO        YES
               │           │         │          │
               │           │         ▼          ▼
               │           │    ┌─────────┐ ┌─────────┐
               │           │    │ Use     │ │ Switch  │
               │           │    │ ECMP    │ │ to DRILL│
               │           │    └────┬────┘ └────┬────┘
               │           │         │          │
               └───────────┴─────────┴──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Route using ECMP or │
                    │ DRILL based on      │
                    │ decision            │
                    └─────────────────────┘
```

---

## Benefits

1. **Small flows** get low latency through ECMP (fast, fixed path)
2. **Large flows** get better load balancing through DRILL (queue-aware)
3. **Reduced tail latency** for mice flows
4. **Better network utilization** for elephant flows
5. **Adaptive**: Switches to DRILL only when flow becomes large

---

## Configuration File Format

When using hybrid mode, the generated config file will include:

```ini
LB_MODE 10
LB_HYBRID_ENABLED 1
LB_HYBRID_THRESHOLD 100000      # bytes
LB_HYBRID_RATIO 0.5             # 0.0 - 1.0
```

---

## Troubleshooting

If compilation fails due to unrelated errors in other modules, you can:
1. Try building specific targets: `./waf build --targets=ns3-point-to-point`
2. Use a clean build: `./waf clean && ./waf configure`
3. The hybrid mode changes are isolated to the point-to-point module and network-load-balance.cc
