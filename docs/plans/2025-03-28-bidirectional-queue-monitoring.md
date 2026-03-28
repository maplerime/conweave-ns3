# Bidirectional Queue Monitoring Implementation Plan (Final)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement bidirectional queue monitoring. Forward probes carry downstream queue info (looking toward destination). Switches store queue info from probes. Reverse data packets carry queue info from the receiver's perspective (what the receiving switch sees).

**Architecture:**
- Forward probe (Core→Agg→Tor), looking DOWNSTREAM:
  - Core: carries Agg→Core queue (queue on Agg looking toward Core)
  - Agg: adds ToR→Agg queue (queue on Tor looking toward Agg)
  - Tor: stores all probe info

- Reverse data path (Tor→Agg→Core), looking from RECEIVER'S perspective:
  - Tor→Agg: uses Agg→Tor queue (from stored probe, what Agg sees from Tor)
  - Agg→Core: uses Core→Agg queue (from stored probe, what Core sees from Agg)
  - Core: stores info

**Key Insight:** Each switch stores queue info from probes showing "what others see from me". When sending data, it reports that info so the receiver knows the queue buildup toward them.

**Tech Stack:**
- NS-3 CustomHeader for queue monitoring
- SwitchNode extensions with per-switch queue storage
- std::map for storing multi-level queue state

---

## Task 1: Define QueueMonitor Header Structure

**Files:**
- Create: `src/point-to-point/model/queue-monitor-header.h`
- Create: `src/point-to-point/model/queue-monitor-header.cc`

**Header Definition:**

```cpp
// queue-monitor-header.h
#ifndef QUEUE_MONITOR_HEADER_H
#define QUEUE_MONITOR_HEADER_H

#include "ns3/header.h"
#include <vector>

namespace ns3 {

class QueueMonitorHeader : public Header {
public:
    // Queue information from one switch
    struct QueueInfo {
        uint32_t switchId;     // Switch ID
        uint32_t portId;       // Port ID
        uint32_t queueLength;  // Queue length in bytes
    };

    QueueMonitorHeader();
    static TypeId GetTypeId();
    virtual uint32_t GetSerializedSize() const override;
    virtual void Serialize(Buffer::Iterator start) const override;
    virtual uint32_t Deserialize(Buffer::Iterator start) const override;
    virtual void Print(std::ostream &os) const override;

    // Add queue info
    void AddQueueInfo(uint32_t switchId, uint32_t portId, uint32_t queueLength);

    // Get all queue info
    const std::vector<QueueInfo>& GetAllQueueInfo() const { return m_queueInfo; }

    // Clear
    void Clear() { m_queueInfo.clear(); }

private:
    std::vector<QueueInfo> m_queueInfo;
};

} // namespace ns3

#endif
```

---

## Task 2: Extend SwitchNode with Queue Monitoring State

**Files:**
- Modify: `src/point-to-point/model/switch-node.h`
- Modify: `src/point-to-point/model/switch-node.cc`

**Add to switch-node.h:**

```cpp
// Queue monitoring
void StartProbeGeneration();
void GenerateAndSendProbe();
void ProcessProbePacket(Ptr<Packet> p, uint32_t inDev);
void AttachQueueMonitorToPacket(Ptr<Packet> p, uint32_t outDev);

// Stored queue info from probes (what downstream switches see)
struct RemoteQueueInfo {
    uint32_t switchId;
    uint32_t portId;
    uint32_t queueLength;
};
// Map: switch_id -> queue info from that switch
std::map<uint32_t, std::vector<RemoteQueueInfo>> m_remoteQueueInfo;

// Probe generation
EventId m_probeEvent;
uint64_t m_probeInterval;
static const uint64_t DEFAULT_PROBE_INTERVAL = 1000000;  // 1ms
```

---

## Task 3: Implement Core Switch Probe Generation

**Files:**
- Modify: `src/point-to-point/model/switch-node.cc`

**Core sends probe with Agg→Core queue info:**

```cpp
void SwitchNode::GenerateAndSendProbe() {
    if (m_switchType != SWITCH_TYPE_CORE) {
        return;
    }

    Ptr<Packet> p = Create<Packet>();
    QueueMonitorHeader monitorHdr;

    // Add queue info for each input port (receiving from Agg)
    // Direction: Agg→Core (what Core sees from Agg)
    for (uint32_t port = 1; port < GetNDevices(); port++) {
        Ptr<NetDevice> dev = GetDevice(port);
        Ptr<QbbNetDevice> qbb = DynamicCast<QbbNetDevice>(dev);
        if (qbb && qbb->IsLinkUp()) {
            uint32_t qLen = qbb->GetQueueLength(port);
            monitorHdr.AddQueueInfo(m_id, port, qLen);
        }
    }

    p->AddHeader(monitorHdr);

    // Broadcast to Agg switches
    for (uint32_t port = 1; port < GetNDevices(); port++) {
        Ptr<NetDevice> dev = GetDevice(port);
        if (dev->IsLinkUp()) {
            dev->Send(p->Copy(), GetDevice(0)->GetAddress(),
                     GetDevice(port)->GetBroadcast());
        }
    }

    m_probeEvent = Simulator::Schedule(NanoSeconds(m_probeInterval),
        MakeCallback(&SwitchNode::GenerateAndSendProbe, this));
}
```

---

## Task 4: Implement Aggregation Switch Probe Processing

**Files:**
- Modify: `src/point-to-point/model/switch-node.cc`

**Agg adds ToR→Agg queue info and forwards:**

```cpp
void SwitchNode::ProcessProbePacket(Ptr<Packet> p, uint32_t inDev) {
    QueueMonitorHeader monitorHdr;
    p->PeekHeader(monitorHdr);

    if (m_switchType == SWITCH_TYPE_AGGREGATION) {
        p->RemoveHeader(monitorHdr);

        // Add ToR→Agg queue info (what Agg sees from Tor)
        // Add for all ports toward ToR
        for (uint32_t port = 1; port < GetNDevices(); port++) {
            if (port == inDev) continue;
            Ptr<NetDevice> dev = GetDevice(port);
            Ptr<QbbNetDevice> qbb = DynamicCast<QbbNetDevice>(dev);
            if (qbb && qbb->IsLinkUp()) {
                uint32_t qLen = qbb->GetQueueLength(port);
                monitorHdr.AddQueueInfo(m_id, port, qLen);
            }
        }

        p->AddHeader(monitorHdr);
        // Forward to ToR
        for (uint32_t port = 1; port < GetNDevices(); port++) {
            if (port != inDev && GetDevice(port)->IsLinkUp()) {
                Ptr<NetDevice> dev = GetDevice(port);
                dev->Send(p->Copy(), GetDevice(0)->GetAddress(),
                         GetDevice(port)->GetBroadcast());
            }
        }
    }
    else if (m_switchType == SWITCH_TYPE_TOR) {
        // ToR stores all received queue info
        p->RemoveHeader(monitorHdr);

        m_remoteQueueInfo.clear();
        for (const auto &info : monitorHdr.GetAllQueueInfo()) {
            RemoteQueueInfo rinfo;
            rinfo.switchId = info.switchId;
            rinfo.portId = info.portId;
            rinfo.queueLength = info.queueLength;
            m_remoteQueueInfo[info.switchId].push_back(rinfo);
        }

        std::cout << "ToR " << m_id << " stored queue info from "
                  << m_remoteQueueInfo.size() << " switches" << std::endl;
    }
}
```

---

## Task 5: Implement Queue Info for Reverse Path

**Files:**
- Modify: `src/point-to-point/model/switch-node.cc`

**Reverse path: use stored probe info from receiver's perspective:**

```cpp
void SwitchNode::AttachQueueMonitorToPacket(Ptr<Packet> p, uint32_t outDev) {
    QueueMonitorHeader monitorHdr;

    if (m_switchType == SWITCH_TYPE_TOR) {
        // Tor→Agg: use Agg→Tor queue (from stored probe, what Agg sees from Tor)
        // Find the queue info that was received from the Agg switch
        // For simplicity, use all stored remote queue info
        for (const auto &[swId, infos] : m_remoteQueueInfo) {
            for (const auto &info : infos) {
                monitorHdr.AddQueueInfo(swId, info.portId, info.queueLength);
            }
        }
    }
    else if (m_switchType == SWITCH_TYPE_AGGREGATION) {
        // Agg→Core: use Core→Agg queue (from stored probe, what Core sees from Agg)
        // Use stored queue info from Core switches
        for (const auto &[swId, infos] : m_remoteQueueInfo) {
            for (const auto &info : infos) {
                monitorHdr.AddQueueInfo(swId, info.portId, info.queueLength);
            }
        }
    }
    // Core: end of reverse path, no queue info needed

    if (!monitorHdr.GetAllQueueInfo().empty()) {
        p->AddHeader(monitorHdr);
    }
}
```

---

## Task 6: Integrate into Packet Paths

**Files:**
- Modify: `src/point-to-point/model/switch-node.cc`

**In DoSwitchSend:**

```cpp
void SwitchNode::DoSwitchSend(Ptr<Packet> p, CustomHeader &ch, uint32_t outDev, uint32_t qIndex) {
    AttachQueueMonitorToPacket(p, outDev);
    // ... existing code ...
}
```

**In GetOutDev:**

```cpp
int SwitchNode::GetOutDev(Ptr<Packet> p, CustomHeader &ch) {
    // ... existing routing ...

    if (p->PeekPacketTag<QueueMonitorHeader>()) {
        ProcessProbePacket(p, inDev);
        return outDev;
    }

    // ... existing code ...
}
```

---

## Task 7: Add GetQueueLength to QbbNetDevice

**Files:**
- Modify: `src/point-to-point/model/qbb-net-device.h`
- Modify: `src/point-to-point/model/qbb-net-device.cc`

---

## Task 8: Enable Probe Generation

**Files:**
- Modify: `scratch/network-load-balance.cc`

---

## Task 9: Update Build System

**Files:**
- Modify: `src/point-to-point/wscript`

---

## Task 10: Test and Verify

---

## Summary

**Complete Queue Info Flow:**

```
Probe Phase (looking DOWNSTREAM):
  Core → [Core: Agg→Core QLen]
  Core → Agg → [Core: Agg→Core QLen, Agg: ToR→Agg QLen]
  Core → Agg → Tor → [stored: Agg→Core QLen, ToR→Agg QLen]

Data Return Phase (looking from RECEIVER'S view):
  Tor → Agg: [Agg→Tor QLen]  (from stored probe)
  Agg → Core: [Core→Agg QLen]  (from stored probe)
  Core → [stores info]
```

**Key Concept:** Probes establish "what others see from me" baseline. Data packets report that baseline so receivers know queue buildup toward them.
