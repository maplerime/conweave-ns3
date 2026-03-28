#include "queue-monitor-header.h"
#include "ns3/log.h"
#include "ns3/tag-buffer.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("QueueMonitorHeader");

NS_OBJECT_ENSURE_REGISTERED(QueueProbeTag);
NS_OBJECT_ENSURE_REGISTERED(InflexPathHeader);
NS_OBJECT_ENSURE_REGISTERED(QueueMonitorHeader);

/******************** QueueProbeTag ********************/

QueueProbeTag::QueueProbeTag() {
}

TypeId
QueueProbeTag::GetTypeId() {
    static TypeId tid = TypeId("ns3::QueueProbeTag")
        .SetParent<Tag>()
        .SetGroupName("Network")
        .AddConstructor<QueueProbeTag>();
    return tid;
}

TypeId
QueueProbeTag::GetInstanceTypeId() const {
    return GetTypeId();
}

uint32_t
QueueProbeTag::GetSerializedSize() const {
    return 0;  // Empty tag, just for identification
}

void
QueueProbeTag::Serialize(TagBuffer i) const {
    // No data to serialize
}

void
QueueProbeTag::Deserialize(TagBuffer i) {
    // No data to deserialize
}

void
QueueProbeTag::Print(std::ostream &os) const {
    os << "QueueProbeTag";
}

/******************** InflexPathHeader ********************/

InflexPathHeader::InflexPathHeader() : m_uplink(true), m_currentHop(0) {
}

TypeId
InflexPathHeader::GetTypeId() {
    static TypeId tid = TypeId("ns3::InflexPathHeader")
        .SetParent<Header>()
        .SetGroupName("Network")
        .AddConstructor<InflexPathHeader>();
    return tid;
}

TypeId
InflexPathHeader::GetInstanceTypeId() const {
    return GetTypeId();
}

uint32_t
InflexPathHeader::GetSerializedSize() const {
    // uplink(1) + currentHop(4) + hopCount(4) + hops * (switchId + portId + queueLen)
    return 1 + 4 + 4 + m_hops.size() * 12;
}

void
InflexPathHeader::Serialize(Buffer::Iterator start) const {
    Buffer::Iterator i = start;

    i.WriteU8(m_uplink ? 1 : 0);
    i.WriteU32(m_currentHop);
    i.WriteU32(m_hops.size());

    for (const auto &hop : m_hops) {
        i.WriteU32(hop.switchId);
        i.WriteU32(hop.portId);
        i.WriteU32(hop.queueLen);
    }
}

uint32_t
InflexPathHeader::Deserialize(Buffer::Iterator start) {
    Buffer::Iterator i = start;

    m_uplink = (i.ReadU8() != 0);
    m_currentHop = i.ReadU32();
    uint32_t hopCount = i.ReadU32();

    m_hops.clear();
    for (uint32_t j = 0; j < hopCount; j++) {
        PathHop hop;
        hop.switchId = i.ReadU32();
        hop.portId = i.ReadU32();
        hop.queueLen = i.ReadU32();
        m_hops.push_back(hop);
    }

    return GetSerializedSize();
}

void
InflexPathHeader::Print(std::ostream &os) const {
    os << "InflexPathHeader[" << (m_uplink ? "uplink" : "downlink")
       << ", currentHop=" << m_currentHop << ", hops=[";
    for (size_t i = 0; i < m_hops.size(); i++) {
        const auto &hop = m_hops[i];
        os << "SW" << hop.switchId << ":P" << hop.portId << "(Q=" << hop.queueLen << ")";
        if (i < m_hops.size() - 1) {
            os << "->";
        }
    }
    os << "]]";
}

void
InflexPathHeader::AddHop(uint32_t switchId, uint32_t portId, uint32_t queueLen) {
    PathHop hop;
    hop.switchId = switchId;
    hop.portId = portId;
    hop.queueLen = queueLen;
    m_hops.push_back(hop);
}

bool
InflexPathHeader::GetNextHop(uint32_t& switchId, uint32_t& portId) const {
    if (m_currentHop >= m_hops.size()) {
        return false;
    }
    switchId = m_hops[m_currentHop].switchId;
    portId = m_hops[m_currentHop].portId;
    return true;
}

/******************** QueueMonitorHeader ********************/

QueueMonitorHeader::QueueMonitorHeader() {
}

TypeId
QueueMonitorHeader::GetTypeId() {
    static TypeId tid = TypeId("ns3::QueueMonitorHeader")
        .SetParent<Header>()
        .SetGroupName("Network")
        .AddConstructor<QueueMonitorHeader>();
    return tid;
}

TypeId
QueueMonitorHeader::GetInstanceTypeId() const {
    return GetTypeId();
}

uint32_t
QueueMonitorHeader::GetSerializedSize() const {
    // Each QueueInfo: switchId(4) + portId(4) + queueLength(4) = 12 bytes
    // Plus count of entries (4 bytes)
    return 4 + m_queueInfo.size() * 12;
}

void
QueueMonitorHeader::Serialize(Buffer::Iterator start) const {
    Buffer::Iterator i = start;

    // Write number of entries
    uint32_t count = m_queueInfo.size();
    i.WriteU32(count);

    // Write each QueueInfo
    for (const auto &info : m_queueInfo) {
        i.WriteU32(info.switchId);
        i.WriteU32(info.portId);
        i.WriteU32(info.queueLength);
    }
}

uint32_t
QueueMonitorHeader::Deserialize(Buffer::Iterator start) {
    Buffer::Iterator i = start;

    // Read number of entries
    uint32_t count = i.ReadU32();
    m_queueInfo.clear();

    // Read each QueueInfo
    for (uint32_t j = 0; j < count; j++) {
        QueueInfo info;
        info.switchId = i.ReadU32();
        info.portId = i.ReadU32();
        info.queueLength = i.ReadU32();
        m_queueInfo.push_back(info);
    }

    return GetSerializedSize();
}

void
QueueMonitorHeader::Print(std::ostream &os) const {
    os << "QueueMonitorHeader[";
    for (size_t i = 0; i < m_queueInfo.size(); i++) {
        const auto &info = m_queueInfo[i];
        os << "SW" << info.switchId << ":P" << info.portId << "=" << info.queueLength;
        if (i < m_queueInfo.size() - 1) {
            os << ", ";
        }
    }
    os << "]";
}

void
QueueMonitorHeader::AddQueueInfo(uint32_t switchId, uint32_t portId, uint32_t queueLength) {
    QueueInfo info;
    info.switchId = switchId;
    info.portId = portId;
    info.queueLength = queueLength;
    m_queueInfo.push_back(info);
}

} // namespace ns3
