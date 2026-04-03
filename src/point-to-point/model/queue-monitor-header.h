#ifndef QUEUE_MONITOR_HEADER_H
#define QUEUE_MONITOR_HEADER_H

#include "ns3/header.h"
#include "ns3/tag.h"
#include <vector>

namespace ns3 {

// Tag to identify queue monitoring probe packets
class QueueProbeTag : public Tag {
public:
    QueueProbeTag();
    static TypeId GetTypeId();
    virtual TypeId GetInstanceTypeId() const override;
    virtual uint32_t GetSerializedSize() const override;
    virtual void Serialize(TagBuffer i) const override;
    virtual void Deserialize(TagBuffer i) override;
    virtual void Print(std::ostream &os) const override;

private:
};

// Path header for Inflex explicit path routing
class InflexPathHeader : public Header {
public:
    // Path hop information
    struct PathHop {
        uint32_t switchId;   // Switch ID (Agg or Core)
        uint32_t portId;     // Port ID to use
        uint32_t queueLen;   // Queue length at selection time
    };

    InflexPathHeader();
    static TypeId GetTypeId();
    virtual TypeId GetInstanceTypeId() const override;
    virtual uint32_t GetSerializedSize() const override;
    virtual void Serialize(Buffer::Iterator start) const override;
    virtual uint32_t Deserialize(Buffer::Iterator start) override;
    virtual void Print(std::ostream &os) const override;

    // Set path direction (0: uplink Tor->Core, 1: downlink Core->Tor)
    void SetDirection(bool uplink) { m_uplink = uplink; }
    bool IsUplink() const { return m_uplink; }

    // Add hop to path
    void AddHop(uint32_t switchId, uint32_t portId, uint32_t queueLen);

    // Get path hops
    const std::vector<PathHop>& GetHops() const { return m_hops; }

    // Get current hop index
    uint32_t GetCurrentHop() const { return m_currentHop; }
    void SetCurrentHop(uint32_t idx) { m_currentHop = idx; }
    void IncrementHop() { m_currentHop++; }

    // Check if path is complete
    bool IsPathComplete() const { return m_currentHop >= m_hops.size(); }

    // Get next hop
    bool GetNextHop(uint32_t& switchId, uint32_t& portId) const;

    // Clear
    void Clear() { m_hops.clear(); m_currentHop = 0; }

private:
    bool m_uplink;                      // true for uplink, false for downlink
    uint32_t m_currentHop;              // Current hop index
    std::vector<PathHop> m_hops;        // Path hops
};

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
    virtual TypeId GetInstanceTypeId() const override;
    virtual uint32_t GetSerializedSize() const override;
    virtual void Serialize(Buffer::Iterator start) const override;
    virtual uint32_t Deserialize(Buffer::Iterator start) override;
    virtual void Print(std::ostream &os) const override;

    // Add queue info
    void AddQueueInfo(uint32_t switchId, uint32_t portId, uint32_t queueLength);

    // Get all queue info
    const std::vector<QueueInfo>& GetAllQueueInfo() const { return m_queueInfo; }

    // Set/Get sender's receiving queue length
    void SetSenderRxQueueLen(uint32_t len) { m_senderRxQueueLen = len; }
    uint32_t GetSenderRxQueueLen() const { return m_senderRxQueueLen; }

    // Clear
    void Clear() { m_queueInfo.clear(); m_senderRxQueueLen = 0; }

private:
    std::vector<QueueInfo> m_queueInfo;
    uint32_t m_senderRxQueueLen;  // Receiving queue length at the sender's port
};

} // namespace ns3

#endif
