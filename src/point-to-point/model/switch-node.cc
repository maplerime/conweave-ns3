#include "switch-node.h"

#include "assert.h"
#include "ns3/boolean.h"
#include "ns3/conweave-routing.h"
#include "ns3/double.h"
#include "ns3/flow-id-tag.h"
#include "ns3/int-header.h"
#include "ns3/ipv4-header.h"
#include "ns3/ipv4.h"
#include "ns3/letflow-routing.h"
#include "ns3/packet.h"
#include "ns3/pause-header.h"
#include "ns3/settings.h"
#include "ns3/uinteger.h"
#include "ppp-header.h"
#include "qbb-net-device.h"

namespace ns3 {

TypeId SwitchNode::GetTypeId(void) {
    static TypeId tid =
        TypeId("ns3::SwitchNode")
            .SetParent<Node>()
            .AddConstructor<SwitchNode>()
            .AddAttribute("EcnEnabled", "Enable ECN marking.", BooleanValue(false),
                          MakeBooleanAccessor(&SwitchNode::m_ecnEnabled), MakeBooleanChecker())
            .AddAttribute("CcMode", "CC mode.", UintegerValue(0),
                          MakeUintegerAccessor(&SwitchNode::m_ccMode),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("AckHighPrio", "Set high priority for ACK/NACK or not", UintegerValue(0),
                          MakeUintegerAccessor(&SwitchNode::m_ackHighPrio),
                          MakeUintegerChecker<uint32_t>());
    return tid;
}

SwitchNode::SwitchNode() {
    m_ecmpSeed = m_id;
    m_isToR = false;
    m_switchType = SWITCH_TYPE_UNKNOWN;
    m_node_type = 1;
    m_isToR = false;
    m_drill_candidate = 2;
    m_probeInterval = DEFAULT_PROBE_INTERVAL;
    m_mmu = CreateObject<SwitchMmu>();
    // Conga's Callback for switch functions
    m_mmu->m_congaRouting.SetSwitchSendCallback(MakeCallback(&SwitchNode::DoSwitchSend, this));
    m_mmu->m_congaRouting.SetSwitchSendToDevCallback(
        MakeCallback(&SwitchNode::SendToDevContinue, this));
    // ConWeave's Callback for switch functions
    m_mmu->m_conweaveRouting.SetSwitchSendCallback(MakeCallback(&SwitchNode::DoSwitchSend, this));
    m_mmu->m_conweaveRouting.SetSwitchSendToDevCallback(
        MakeCallback(&SwitchNode::SendToDevContinue, this));

    for (uint32_t i = 0; i < pCnt; i++) {
        m_txBytes[i] = 0;
    }
}

/**
 * @brief Load Balancing
 */
uint32_t SwitchNode::DoLbFlowECMP(Ptr<const Packet> p, const CustomHeader &ch,
                                  const std::vector<int> &nexthops) {
    // pick one next hop based on hash
    union {
        uint8_t u8[4 + 4 + 2 + 2];
        uint32_t u32[3];
    } buf;
    buf.u32[0] = ch.sip;
    buf.u32[1] = ch.dip;
    if (ch.l3Prot == 0x6)
        buf.u32[2] = ch.tcp.sport | ((uint32_t)ch.tcp.dport << 16);
    else if (ch.l3Prot == 0x11)  // XXX RDMA traffic on UDP
        buf.u32[2] = ch.udp.sport | ((uint32_t)ch.udp.dport << 16);
    else if (ch.l3Prot == 0xFC || ch.l3Prot == 0xFD)  // ACK or NACK
        buf.u32[2] = ch.ack.sport | ((uint32_t)ch.ack.dport << 16);
    else {
        std::cout << "[ERROR] Sw(" << m_id << ")," << PARSE_FIVE_TUPLE(ch)
                  << "Cannot support other protoocls than TCP/UDP (l3Prot:" << ch.l3Prot << ")"
                  << std::endl;
        assert(false && "Cannot support other protoocls than TCP/UDP");
    }

    uint32_t hashVal = EcmpHash(buf.u8, 12, m_ecmpSeed);
    uint32_t idx = hashVal % nexthops.size();
    return nexthops[idx];
}

/*-----------------CONGA-----------------*/
uint32_t SwitchNode::DoLbConga(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    return DoLbFlowECMP(p, ch, nexthops);  // flow ECMP (dummy)
}

/*-----------------Letflow-----------------*/
uint32_t SwitchNode::DoLbLetflow(Ptr<Packet> p, CustomHeader &ch,
                                 const std::vector<int> &nexthops) {
    if (m_isToR && nexthops.size() == 1) {
        if (m_isToR_hostIP.find(ch.sip) != m_isToR_hostIP.end() &&
            m_isToR_hostIP.find(ch.dip) != m_isToR_hostIP.end()) {
            return nexthops[0];  // intra-pod traffic
        }
    }

    /* ONLY called for inter-Pod traffic */
    uint32_t outPort = m_mmu->m_letflowRouting.RouteInput(p, ch);
    if (outPort == LETFLOW_NULL) {
        assert(nexthops.size() == 1);  // Receiver's TOR has only one interface to receiver-server
        outPort = nexthops[0];         // has only one option
    }
    assert(std::find(nexthops.begin(), nexthops.end(), outPort) !=
           nexthops.end());  // Result of Letflow cannot be found in nexthops
    return outPort;
}

/*-----------------DRILL-----------------*/
uint32_t SwitchNode::CalculateInterfaceLoad(uint32_t interface) {
    Ptr<QbbNetDevice> device = DynamicCast<QbbNetDevice>(m_devices[interface]);
    NS_ASSERT_MSG(!!device && !!device->GetQueue(),
                  "Error of getting a egress queue for calculating interface load");
    return device->GetQueue()->GetNBytesTotal();  // also used in HPCC
}

uint32_t SwitchNode::DoLbDrill(Ptr<const Packet> p, const CustomHeader &ch,
                               const std::vector<int> &nexthops) {
    // find the Egress (output) link with the smallest local Egress Queue length
    uint32_t leastLoadInterface = 0;
    uint32_t leastLoad = std::numeric_limits<uint32_t>::max();
    auto rand_nexthops = nexthops;
    std::random_shuffle(rand_nexthops.begin(), rand_nexthops.end());

    std::map<uint32_t, uint32_t>::iterator itr = m_previousBestInterfaceMap.find(ch.dip);
    if (itr != m_previousBestInterfaceMap.end()) {
        leastLoadInterface = itr->second;
        leastLoad = CalculateInterfaceLoad(itr->second);
    }

    uint32_t sampleNum =
        m_drill_candidate < rand_nexthops.size() ? m_drill_candidate : rand_nexthops.size();
    for (uint32_t samplePort = 0; samplePort < sampleNum; samplePort++) {
        uint32_t sampleLoad = CalculateInterfaceLoad(rand_nexthops[samplePort]);
        if (sampleLoad < leastLoad) {
            leastLoad = sampleLoad;
            leastLoadInterface = rand_nexthops[samplePort];
        }
    }
    m_previousBestInterfaceMap[ch.dip] = leastLoadInterface;
    return leastLoadInterface;
}

/*------------------ConWeave Dummy ----------------*/
uint32_t SwitchNode::DoLbConWeave(Ptr<const Packet> p, const CustomHeader &ch,
                                  const std::vector<int> &nexthops) {
    return DoLbFlowECMP(p, ch, nexthops);  // flow ECMP (dummy)
}
/*----------------------------------*/

void SwitchNode::CheckAndSendPfc(uint32_t inDev, uint32_t qIndex) {
    Ptr<QbbNetDevice> device = DynamicCast<QbbNetDevice>(m_devices[inDev]);
    bool pClasses[qCnt] = {0};
    m_mmu->GetPauseClasses(inDev, qIndex, pClasses);
    for (int j = 0; j < qCnt; j++) {
        if (pClasses[j]) {
            uint32_t paused_time = device->SendPfc(j, 0);
            m_mmu->SetPause(inDev, j, paused_time);
            m_mmu->m_pause_remote[inDev][j] = true;
            /** PAUSE SEND COUNT ++ */
        }
    }

    for (int j = 0; j < qCnt; j++) {
        if (!m_mmu->m_pause_remote[inDev][j]) continue;

        if (m_mmu->GetResumeClasses(inDev, j)) {
            device->SendPfc(j, 1);
            m_mmu->SetResume(inDev, j);
            m_mmu->m_pause_remote[inDev][j] = false;
        }
    }
}
void SwitchNode::CheckAndSendResume(uint32_t inDev, uint32_t qIndex) {
    Ptr<QbbNetDevice> device = DynamicCast<QbbNetDevice>(m_devices[inDev]);
    if (m_mmu->GetResumeClasses(inDev, qIndex)) {
        device->SendPfc(qIndex, 1);
        m_mmu->SetResume(inDev, qIndex);
    }
}

/********************************************
 *              MAIN LOGICS                 *
 *******************************************/

// This function can only be called in switch mode
bool SwitchNode::SwitchReceiveFromDevice(Ptr<NetDevice> device, Ptr<Packet> packet,
                                         CustomHeader &ch) {
    SendToDev(packet, ch);
    return true;
}

void SwitchNode::SendToDev(Ptr<Packet> p, CustomHeader &ch) {
    /** HIJACK: hijack the packet and run DoSwitchSend internally for Conga and ConWeave.
     * Note that DoLbConWeave() and DoLbConga() are flow-ECMP function for control packets
     * or intra-ToR traffic.
     */

    // Conga
    if (Settings::lb_mode == 3) {
        m_mmu->m_congaRouting.RouteInput(p, ch);
        return;
    }

    // ConWeave
    if (Settings::lb_mode == 9) {
        m_mmu->m_conweaveRouting.RouteInput(p, ch);
        return;
    }

    // Others
    SendToDevContinue(p, ch);
}

void SwitchNode::SendToDevContinue(Ptr<Packet> p, CustomHeader &ch) {
    int idx = GetOutDev(p, ch);
    if (idx >= 0) {
        NS_ASSERT_MSG(m_devices[idx]->IsLinkUp(),
                      "The routing table look up should return link that is up");

        // determine the qIndex
        uint32_t qIndex;
        if (ch.l3Prot == 0xFF || ch.l3Prot == 0xFE ||
            (m_ackHighPrio &&
             (ch.l3Prot == 0xFD ||
              ch.l3Prot == 0xFC))) {  // QCN or PFC or ACK/NACK, go highest priority
            qIndex = 0;               // high priority
        } else {
            qIndex = (ch.l3Prot == 0x06 ? 1 : ch.udp.pg);  // if TCP, put to queue 1. Otherwise, it
                                                           // would be 3 (refer to trafficgen)
        }

        DoSwitchSend(p, ch, idx, qIndex);  // m_devices[idx]->SwitchSend(qIndex, p, ch);
        return;
    }
    std::cout << "WARNING - Drop occurs in SendToDevContinue()" << std::endl;
    return;  // Drop otherwise
}

int SwitchNode::GetOutDev(Ptr<Packet> p, CustomHeader &ch) {
    // Check if this is a queue monitoring probe packet
    QueueProbeTag probeTag;
    if (p->PeekPacketTag(probeTag)) {
        // Process probe packet - broadcast to all ports for Agg, store for ToR
        uint32_t inDev = 0;
        FlowIdTag t;
        if (p->PeekPacketTag(t)) {
            inDev = t.GetFlowId();
        }
        ProcessProbePacket(p, inDev);
        return -1;  // Special return: probe handled, don't forward normally
    }

    // look up entries
    auto entry = m_rtTable.find(ch.dip);

    // no matching entry
    if (entry == m_rtTable.end()) {
        std::cout << "[ERROR] Sw(" << m_id << ")," << PARSE_FIVE_TUPLE(ch)
                  << "No matching entry, so drop this packet at SwitchNode (l3Prot:" << ch.l3Prot
                  << ")" << std::endl;
        assert(false);
    }

    // entry found
    const auto &nexthops = entry->second;
    bool control_pkt =
        (ch.l3Prot == 0xFF || ch.l3Prot == 0xFE || ch.l3Prot == 0xFD || ch.l3Prot == 0xFC);

    // Check if this packet has InflexPathHeader (explicit path routing)
    InflexPathHeader pathHdr;
    bool hasPathHdr = false;
    try {
        // Peek without removing to check if header exists
        p->PeekHeader(pathHdr);
        hasPathHdr = true;
    } catch (...) {
        hasPathHdr = false;
    }

    if (hasPathHdr && Settings::lb_mode == 12) {
        // Follow the explicit path in the header
        if (m_switchType == SWITCH_TYPE_AGGREGATION) {
            // Agg switch follows the path
            uint32_t nextSwitchId, nextPortId;
            if (pathHdr.GetNextHop(nextSwitchId, nextPortId)) {
                // Remove header to update hop index
                p->RemoveHeader(pathHdr);
                pathHdr.IncrementHop();
                p->AddHeader(pathHdr);

                // Use the specified port
                for (int port : nexthops) {
                    if ((uint32_t)port == nextPortId) {
                        return port;
                    }
                }
            }
        }
        // Tor/Core handle path selection in their respective functions
    }

    // Hybrid mode (lb_mode=10): use pg field to determine per-flow load balancing
    if (Settings::lb_mode == 10) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // pg == 2 -> DRILL, otherwise -> FlowECMP
        if (ch.udp.pg == 2) {
            return DoLbDrill(p, ch, nexthops);
        }
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // ECMP-Conweave mode (lb_mode=11): use pg field to determine per-flow load balancing
    if (Settings::lb_mode == 11) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // pg == 2 -> Conweave, otherwise -> FlowECMP
        if (ch.udp.pg == 2) {
            return DoLbConWeave(p, ch, nexthops);
        }
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // Inflex mode (lb_mode=12): explicit path selection based on queue monitoring
    if (Settings::lb_mode == 12) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // pg == 2 -> Inflex with queue monitoring, otherwise -> FlowECMP
        if (ch.udp.pg == 2) {
            // Use Inflex path selection
            if (m_switchType == SWITCH_TYPE_TOR) {
                return SelectInflexUplink(p, ch, nexthops);
            } else if (m_switchType == SWITCH_TYPE_CORE) {
                return SelectInflexDownlink(p, ch, nexthops);
            } else if (m_switchType == SWITCH_TYPE_AGGREGATION) {
                return SelectInflexAggForward(p, ch, nexthops);
            }
            return DoLbFlowECMP(p, ch, nexthops);
        }
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // Original modes
    if (Settings::lb_mode == 0 || control_pkt) {  // control packet (ACK, NACK, PFC, QCN)
        return DoLbFlowECMP(p, ch, nexthops);     // ECMP routing path decision (4-tuple)
    }

    switch (Settings::lb_mode) {
        case 2:
            return DoLbDrill(p, ch, nexthops);
        case 3:
            return DoLbConga(p, ch, nexthops); /** DUMMY: Do ECMP */
        case 6:
            return DoLbLetflow(p, ch, nexthops);
        case 9:
            return DoLbConWeave(p, ch, nexthops); /** DUMMY: Do ECMP */
        default:
            std::cout << "Unknown lb_mode(" << Settings::lb_mode << ")" << std::endl;
            assert(false);
    }
}

/*
 * The (possible) callback point when conweave dequeues packets from buffer
 */
void SwitchNode::DoSwitchSend(Ptr<Packet> p, CustomHeader &ch, uint32_t outDev, uint32_t qIndex) {
    // admission control
    FlowIdTag t;
    p->PeekPacketTag(t);
    uint32_t inDev = t.GetFlowId();

    /** NOTE:
     * ConWeave control packets have the high priority as ACK/NACK/PFC/etc with qIndex = 0.
     */
    if (inDev == Settings::CONWEAVE_CTRL_DUMMY_INDEV) { // sanity check
        // ConWeave reply is on ACK protocol with high priority, so qIndex should be 0
        assert(qIndex == 0 && m_ackHighPrio == 1 && "ConWeave's reply packet follows ACK, so its qIndex should be 0");
    }

    if (qIndex != 0) {  // not highest priority
        if (m_mmu->CheckEgressAdmission(outDev, qIndex,
                                        p->GetSize())) {  // Egress Admission control
            if (m_mmu->CheckIngressAdmission(inDev, qIndex,
                                             p->GetSize())) {  // Ingress Admission control
                m_mmu->UpdateIngressAdmission(inDev, qIndex, p->GetSize());
                m_mmu->UpdateEgressAdmission(outDev, qIndex, p->GetSize());
            } else { /** DROP: At Ingress */
#if (0)
                // /** NOTE: logging dropped pkts */
                // std::cout << "LostPkt ingress - Sw(" << m_id << ")," << PARSE_FIVE_TUPLE(ch)
                //           << "L3Prot:" << ch.l3Prot
                //           << ",Size:" << p->GetSize()
                //           << ",At " << Simulator::Now() << std::endl;
#endif
                Settings::dropped_pkt_sw_ingress++;
                return;  // drop
            }
        } else { /** DROP: At Egress */
#if (0)
            // /** NOTE: logging dropped pkts */
            // std::cout << "LostPkt egress - Sw(" << m_id << ")," << PARSE_FIVE_TUPLE(ch)
            //           << "L3Prot:" << ch.l3Prot << ",Size:" << p->GetSize() << ",At "
            //           << Simulator::Now() << std::endl;
#endif
            Settings::dropped_pkt_sw_egress++;
            return;  // drop
        }

        CheckAndSendPfc(inDev, qIndex);
    }

    // Attach queue monitoring info for reverse path data packets (only in mode 12: Inflex)
    if (Settings::lb_mode == 12) {
        AttachQueueMonitorToPacket(p, outDev);
    }

    m_devices[outDev]->SwitchSend(qIndex, p, ch);
}

void SwitchNode::SwitchNotifyDequeue(uint32_t ifIndex, uint32_t qIndex, Ptr<Packet> p) {
    FlowIdTag t;
    p->PeekPacketTag(t);
    if (qIndex != 0) {
        uint32_t inDev = t.GetFlowId();
        if (inDev != Settings::CONWEAVE_CTRL_DUMMY_INDEV) {
            // NOTE: ConWeave's probe/reply does not need to pass inDev interface,
            // so skip for conweave's queued packets
            m_mmu->RemoveFromIngressAdmission(inDev, qIndex, p->GetSize());
        }
        m_mmu->RemoveFromEgressAdmission(ifIndex, qIndex, p->GetSize());
        if (m_ecnEnabled) {
            bool egressCongested = m_mmu->ShouldSendCN(ifIndex, qIndex);
            if (egressCongested) {
                PppHeader ppp;
                Ipv4Header h;
                p->RemoveHeader(ppp);
                p->RemoveHeader(h);
                h.SetEcn((Ipv4Header::EcnType)0x03);
                p->AddHeader(h);
                p->AddHeader(ppp);
            }
        }
        // NOTE: ConWeave's probe/reply does not need to pass inDev interface
        if (inDev != Settings::CONWEAVE_CTRL_DUMMY_INDEV) {
            CheckAndSendResume(inDev, qIndex);
        }
    }

    // HPCC's INT
    if (1) {
        uint8_t *buf = p->GetBuffer();
        if (buf[PppHeader::GetStaticSize() + 9] == 0x11) {  // udp packet
            IntHeader *ih = (IntHeader *)&buf[PppHeader::GetStaticSize() + 20 + 8 +
                                              6];  // ppp, ip, udp, SeqTs, INT
            Ptr<QbbNetDevice> dev = DynamicCast<QbbNetDevice>(m_devices[ifIndex]);
            if (m_ccMode == 3) {  // HPCC
                ih->PushHop(Simulator::Now().GetTimeStep(), m_txBytes[ifIndex],
                            dev->GetQueue()->GetNBytesTotal(), dev->GetDataRate().GetBitRate());
            }
        }
    }
    m_txBytes[ifIndex] += p->GetSize();
}

uint32_t SwitchNode::EcmpHash(const uint8_t *key, size_t len, uint32_t seed) {
    uint32_t h = seed;
    if (len > 3) {
        const uint32_t *key_x4 = (const uint32_t *)key;
        size_t i = len >> 2;
        do {
            uint32_t k = *key_x4++;
            k *= 0xcc9e2d51;
            k = (k << 15) | (k >> 17);
            k *= 0x1b873593;
            h ^= k;
            h = (h << 13) | (h >> 19);
            h += (h << 2) + 0xe6546b64;
        } while (--i);
        key = (const uint8_t *)key_x4;
    }
    if (len & 3) {
        size_t i = len & 3;
        uint32_t k = 0;
        key = &key[i - 1];
        do {
            k <<= 8;
            k |= *key--;
        } while (--i);
        k *= 0xcc9e2d51;
        k = (k << 15) | (k >> 17);
        k *= 0x1b873593;
        h ^= k;
    }
    h ^= len;
    h ^= h >> 16;
    h *= 0x85ebca6b;
    h ^= h >> 13;
    h *= 0xc2b2ae35;
    h ^= h >> 16;
    return h;
}

void SwitchNode::SetEcmpSeed(uint32_t seed) { m_ecmpSeed = seed; }

void SwitchNode::SetSwitchType(SwitchType type) { m_switchType = type; }

SwitchType SwitchNode::GetSwitchType() const { return m_switchType; }

void SwitchNode::AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx) {
    uint32_t dip = dstAddr.Get();
    m_rtTable[dip].push_back(intf_idx);
}

void SwitchNode::ClearTable() { m_rtTable.clear(); }

uint64_t SwitchNode::GetTxBytesOutDev(uint32_t outdev) {
    assert(outdev < pCnt);
    return m_txBytes[outdev];
}

/********************************************
 *     Queue Monitoring Implementation      *
 *******************************************/

void SwitchNode::StartProbeGeneration() {
    if (m_switchType == SWITCH_TYPE_CORE) {
        // Start periodic probe generation from Core switches
        m_probeEvent = Simulator::Schedule(NanoSeconds(m_probeInterval),
            &SwitchNode::GenerateAndSendProbe, this);
        std::cout << "Core switch " << m_id << " started probe generation (interval="
                  << m_probeInterval << "ns)" << std::endl;
    }
}

void SwitchNode::GenerateAndSendProbe() {
    if (m_switchType != SWITCH_TYPE_CORE) {
        return;
    }

    Ptr<Packet> p = Create<Packet>(0);  // Empty probe packet
    QueueProbeTag probeTag;  // Tag to identify this as a probe packet
    p->AddPacketTag(probeTag);

    QueueMonitorHeader monitorHdr;
    // Core sends empty header - Agg will detect empty = from Core

    p->AddHeader(monitorHdr);

    // Broadcast to Agg switches (all ports except possibly CPU port)
    // Use 0x0800 as protocol number (IPv4)
    for (uint32_t port = 1; port < GetNDevices(); port++) {
        Ptr<NetDevice> dev = GetDevice(port);
        if (dev->IsLinkUp()) {
            dev->Send(p->Copy(), GetDevice(port)->GetBroadcast(), 0x0800);
        }
    }

    // Schedule next probe
    m_probeEvent = Simulator::Schedule(NanoSeconds(m_probeInterval),
        &SwitchNode::GenerateAndSendProbe, this);
}

void SwitchNode::ProcessProbePacket(Ptr<Packet> p, uint32_t inDev) {
    QueueMonitorHeader monitorHdr;
    p->PeekHeader(monitorHdr);

    if (m_switchType == SWITCH_TYPE_AGGREGATION) {
        p->RemoveHeader(monitorHdr);

        // Detect direction: empty header = from Core (forward path)
        // Non-empty header = from Tor (reverse path)
        bool isFromCore = monitorHdr.GetAllQueueInfo().empty();

        if (isFromCore) {
            // Forward path: add Agg→Core queue info, broadcast to all Tor
            Ptr<NetDevice> inDevDevice = GetDevice(inDev);
            Ptr<QbbNetDevice> qbbIn = DynamicCast<QbbNetDevice>(inDevDevice);
            if (qbbIn) {
                uint32_t qLen = 0;
                for (uint32_t q = 0; q < 8; q++) {
                    qLen += qbbIn->GetQueue()->GetNBytes(q);
                }
                monitorHdr.AddQueueInfo(m_id, inDev, qLen);
            }

            p->AddHeader(monitorHdr);
            // Broadcast to all Tor (all ports except inDev)
            for (uint32_t port = 1; port < GetNDevices(); port++) {
                if (port != inDev && GetDevice(port)->IsLinkUp()) {
                    Ptr<NetDevice> dev = GetDevice(port);
                    dev->Send(p->Copy(), dev->GetBroadcast(), 0x0800);
                }
            }
        } else {
            // Reverse path: from Tor, update Tor entry's queue, send back to Core
            uint32_t aggToCorePort = 0;
            uint32_t torSwitchId = 0;
            uint32_t torPort = 0;

            // Find Agg's port toward Core and Tor's info
            for (const auto &info : monitorHdr.GetAllQueueInfo()) {
                if (info.switchId == m_id) {
                    aggToCorePort = info.portId;
                } else {
                    // This is Tor's entry
                    torSwitchId = info.switchId;
                    torPort = info.portId;
                }
            }

            if (aggToCorePort > 0 && torSwitchId > 0) {
                // Get Agg→Tor queue length (on inDev port, the port from which we received Tor's probe)
                uint32_t aggToTorQueue = 0;
                Ptr<NetDevice> inDevDevice = GetDevice(inDev);
                Ptr<QbbNetDevice> qbbIn = DynamicCast<QbbNetDevice>(inDevDevice);
                if (qbbIn) {
                    for (uint32_t q = 0; q < 8; q++) {
                        aggToTorQueue += qbbIn->GetQueue()->GetNBytes(q);
                    }
                }

                // Rebuild header:
                monitorHdr.Clear();
                monitorHdr.AddQueueInfo(m_id, aggToCorePort, 0);           // Agg: keep port for routing, qLen=0
                monitorHdr.AddQueueInfo(torSwitchId, torPort, aggToTorQueue); // Tor: update queue to Agg→Tor queue

                p->AddHeader(monitorHdr);
                // Send to Core via aggToCorePort
                Ptr<NetDevice> outDev = GetDevice(aggToCorePort);
                if (outDev->IsLinkUp()) {
                    outDev->Send(p, outDev->GetBroadcast(), 0x0800);
                }
            }
        }
    }
    else if (m_switchType == SWITCH_TYPE_TOR) {
        p->RemoveHeader(monitorHdr);

        // ToR stores all received queue info (from Agg)
        m_remoteQueueInfo.clear();
        for (const auto &info : monitorHdr.GetAllQueueInfo()) {
            RemoteQueueInfo rinfo;
            rinfo.switchId = info.switchId;
            rinfo.portId = info.portId;
            rinfo.queueLength = info.queueLength;
            m_remoteQueueInfo[info.switchId].push_back(rinfo);
        }

        // Also store Tor's own info (Tor→Agg port, queue=0)
        RemoteQueueInfo torInfo;
        torInfo.switchId = m_id;
        torInfo.portId = inDev;
        torInfo.queueLength = 0;
        m_remoteQueueInfo[m_id].push_back(torInfo);

        std::cout << "ToR " << m_id << " stored queue info from "
                  << m_remoteQueueInfo.size() << " switches" << std::endl;

        // Add Tor's own info to probe header (keep Agg's info for reverse path routing)
        monitorHdr.AddQueueInfo(m_id, inDev, 0);

        // Send reverse probe back to the Agg (inDev port)
        // The header now contains: (Agg_id, agg_to_core_port, agg_queue) + (Tor_id, tor_port, 0)
        p->AddHeader(monitorHdr);
        Ptr<NetDevice> inDevDevice = GetDevice(inDev);
        if (inDevDevice->IsLinkUp()) {
            inDevDevice->Send(p, inDevDevice->GetBroadcast(), 0x0800);
        }
    }
    else if (m_switchType == SWITCH_TYPE_CORE) {
        // Core stores queue info from reverse path probes
        p->RemoveHeader(monitorHdr);

        m_remoteQueueInfo.clear();
        for (const auto &info : monitorHdr.GetAllQueueInfo()) {
            RemoteQueueInfo rinfo;
            rinfo.switchId = info.switchId;
            rinfo.portId = info.portId;
            rinfo.queueLength = info.queueLength;
            m_remoteQueueInfo[info.switchId].push_back(rinfo);
        }

        std::cout << "Core " << m_id << " stored queue info from "
                  << m_remoteQueueInfo.size() << " switches" << std::endl;
    }
}

void SwitchNode::AttachQueueMonitorToPacket(Ptr<Packet> p, uint32_t outDev) {
    QueueMonitorHeader monitorHdr;

    if (m_switchType == SWITCH_TYPE_TOR) {
        // Tor→Agg: use Agg→Tor queue (from stored probe, what Agg sees from Tor)
        // Find the queue info that was received from the Agg switch
        for (const auto &entry : m_remoteQueueInfo) {
            uint32_t swId = entry.first;
            for (const auto &info : entry.second) {
                monitorHdr.AddQueueInfo(swId, info.portId, info.queueLength);
            }
        }
    }
    else if (m_switchType == SWITCH_TYPE_AGGREGATION) {
        // Agg→Core: use Core→Agg queue (from stored probe, what Core sees from Agg)
        for (const auto &entry : m_remoteQueueInfo) {
            uint32_t swId = entry.first;
            for (const auto &info : entry.second) {
                monitorHdr.AddQueueInfo(swId, info.portId, info.queueLength);
            }
        }
    }
    // Core: end of reverse path, no queue info needed

    if (!monitorHdr.GetAllQueueInfo().empty()) {
        p->AddHeader(monitorHdr);
    }
}

/********************************************
 *     Inflex Path Selection Implementation *
 *******************************************/

int SwitchNode::SelectInflexUplink(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    // ToR selects path: ToR -> Agg -> Core
    // Calculate total queue length for each path (2-hop sum)
    // Select path with minimum total queue length

    if (m_remoteQueueInfo.empty()) {
        // No queue info yet, fall back to ECMP
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // Build path options: for each Agg reachable from this ToR,
    // calculate queue length: ToR->Agg (local) + Agg->Core (from stored probe)
    struct PathOption {
        int port;
        uint32_t aggSwitchId;
        uint32_t totalQueue;
    };
    std::vector<PathOption> pathOptions;

    // For each output port (to Agg), calculate total queue length
    for (int port : nexthops) {
        // Get local ToR→Agg queue length for this port
        uint32_t torToAggQueue = 0;
        Ptr<NetDevice> dev = GetDevice(port);
        Ptr<QbbNetDevice> qbb = DynamicCast<QbbNetDevice>(dev);
        if (qbb) {
            for (uint32_t q = 0; q < 8; q++) {
                torToAggQueue += qbb->GetQueue()->GetNBytes(q);
            }
        }

        // Find Agg→Core queue length from stored probe info
        // Look for entry where portId matches this port
        uint32_t aggToCoreQueue = 0;
        uint32_t aggSwId = 0;

        for (const auto &entry : m_remoteQueueInfo) {
            for (const auto &info : entry.second) {
                if (info.portId == (uint32_t)port) {
                    aggSwId = entry.first;
                    aggToCoreQueue = info.queueLength;
                    break;
                }
            }
            if (aggSwId > 0) break;
        }

        // Only consider this path if we found Agg info
        if (aggSwId > 0) {
            PathOption opt;
            opt.port = port;
            opt.aggSwitchId = aggSwId;
            // Formula: localQueue * 1.2 + remoteQueue
            opt.totalQueue = (uint32_t)(torToAggQueue * 1.2) + aggToCoreQueue;
            pathOptions.push_back(opt);
        }
    }

    if (pathOptions.empty()) {
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // Select path with minimum total queue
    auto best = std::min_element(pathOptions.begin(), pathOptions.end(),
        [](const PathOption& a, const PathOption& b) {
            return a.totalQueue < b.totalQueue;
        });

    // Attach path header to packet with selected path info
    InflexPathHeader pathHdr;
    pathHdr.SetDirection(true);  // Uplink
    pathHdr.AddHop(best->aggSwitchId, best->port, best->totalQueue);
    // Add Core hop info if available (from probe)
    // For now, we let Agg decide the next hop
    p->AddHeader(pathHdr);

    return best->port;
}

int SwitchNode::SelectInflexDownlink(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    // Core selects path: Core -> Agg -> Tor
    // Calculate total queue: Core->Agg (local) + Agg->Tor (from stored probe)

    if (m_remoteQueueInfo.empty()) {
        return DoLbFlowECMP(p, ch, nexthops);
    }

    struct PathOption {
        int port;
        uint32_t aggSwitchId;
        uint32_t totalQueue;
    };
    std::vector<PathOption> pathOptions;

    // For each output port (to Agg), calculate total queue length
    for (int port : nexthops) {
        // Get local Core→Agg queue length for this port
        uint32_t coreToAggQueue = 0;
        Ptr<NetDevice> dev = GetDevice(port);
        Ptr<QbbNetDevice> qbb = DynamicCast<QbbNetDevice>(dev);
        if (qbb) {
            for (uint32_t q = 0; q < 8; q++) {
                coreToAggQueue += qbb->GetQueue()->GetNBytes(q);
            }
        }

        // Find Agg→Tor queue length from stored probe info
        // Look for entry where portId matches this port
        uint32_t aggToTorQueue = 0;
        uint32_t aggSwId = 0;

        for (const auto &entry : m_remoteQueueInfo) {
            for (const auto &info : entry.second) {
                if (info.portId == (uint32_t)port) {
                    aggSwId = entry.first;
                    aggToTorQueue = info.queueLength;
                    break;
                }
            }
            if (aggSwId > 0) break;
        }

        // Only consider this path if we found Agg info
        if (aggSwId > 0) {
            PathOption opt;
            opt.port = port;
            opt.aggSwitchId = aggSwId;
            // Formula: localQueue * 1.2 + remoteQueue
            opt.totalQueue = (uint32_t)(coreToAggQueue * 1.2) + aggToTorQueue;
            pathOptions.push_back(opt);
        }
    }

    if (pathOptions.empty()) {
        return DoLbFlowECMP(p, ch, nexthops);
    }

    auto best = std::min_element(pathOptions.begin(), pathOptions.end(),
        [](const PathOption& a, const PathOption& b) {
            return a.totalQueue < b.totalQueue;
        });

    // Attach path header to packet with selected path info
    InflexPathHeader pathHdr;
    pathHdr.SetDirection(false);  // Downlink
    pathHdr.AddHop(best->aggSwitchId, best->port, best->totalQueue);
    p->AddHeader(pathHdr);

    return best->port;
}

int SwitchNode::SelectInflexAggForward(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    // Agg switches forward packets based on InflexPathHeader
    InflexPathHeader pathHdr;
    if (!p->PeekHeader(pathHdr)) {
        return DoLbDrill(p, ch, nexthops);
    }

    // Check if path is complete
    if (pathHdr.IsPathComplete()) {
        return DoLbDrill(p, ch, nexthops);
    }

    // Get next hop from path header (switchId and port)
    uint32_t nextSwitchId, nextPort;
    if (!pathHdr.GetNextHop(nextSwitchId, nextPort)) {
        return DoLbDrill(p, ch, nexthops);
    }

    // Check if next hop is Core (assume Core IDs >= 1000)
    bool isCoreSwitch = (nextSwitchId >= 1000);

    if (isCoreSwitch) {
        // Next hop is Core -> compare queue lengths and potentially switch path
        // Get the path header's recorded queue length for the planned path
        const std::vector<InflexPathHeader::PathHop>& hops = pathHdr.GetHops();
        uint32_t currentHopIdx = pathHdr.GetCurrentHop();

        // Get current path's queue length from path header
        uint32_t currentPathQueue = 0;
        if (currentHopIdx < hops.size()) {
            currentPathQueue = hops[currentHopIdx].queueLen;
        }

        // Find the port with minimum queue length among all available ports
        uint32_t minQueue = std::numeric_limits<uint32_t>::max();
        int minQueuePort = -1;

        for (int port : nexthops) {
            uint32_t queueLen = CalculateInterfaceLoad(port);
            if (queueLen < minQueue) {
                minQueue = queueLen;
                minQueuePort = port;
            }
        }

        // Check if we should switch to a different path
        // Switch if current path queue > min queue * 1.2
        bool shouldSwitch = (minQueuePort != -1) &&
                            (currentPathQueue > minQueue * 12 / 10);  // 1.2 = 12/10

        int selectedPort;
        if (shouldSwitch && minQueuePort != -1) {
            // Switch to the path with minimum queue
            selectedPort = minQueuePort;
            // Update the path header with new port and queue length
            p->RemoveHeader(pathHdr);
            // Update current hop with new port and queue
            if (currentHopIdx < hops.size()) {
                // Replace the hop with updated info
                std::vector<InflexPathHeader::PathHop> updatedHops = hops;
                updatedHops[currentHopIdx].portId = selectedPort;
                updatedHops[currentHopIdx].queueLen = minQueue;
                // Clear and rebuild path header
                pathHdr.Clear();
                pathHdr.SetDirection(true);  // uplink
                pathHdr.SetCurrentHop(currentHopIdx);
                for (const auto& hop : updatedHops) {
                    pathHdr.AddHop(hop.switchId, hop.portId, hop.queueLen);
                }
            }
            p->AddHeader(pathHdr);
        } else {
            // Use the port from path header
            selectedPort = -1;
            for (int port : nexthops) {
                if (port == (int)nextPort) {
                    selectedPort = port;
                    break;
                }
            }
            if (selectedPort == -1) {
                // Planned port not available, use min queue port
                selectedPort = minQueuePort;
            }
            // Increment hop and update header
            pathHdr.IncrementHop();
            p->RemoveHeader(pathHdr);
            p->AddHeader(pathHdr);
        }

        return selectedPort;
    } else {
        // Next hop is ToR in same pod -> apply dynamic switching logic
        // Get the path header's recorded queue length for the planned path
        const std::vector<InflexPathHeader::PathHop>& hops = pathHdr.GetHops();
        uint32_t currentHopIdx = pathHdr.GetCurrentHop();

        // Get current path's queue length from path header
        uint32_t currentPathQueue = 0;
        if (currentHopIdx < hops.size()) {
            currentPathQueue = hops[currentHopIdx].queueLen;
        }

        // Find the port with minimum queue length among all available ports
        uint32_t minQueue = std::numeric_limits<uint32_t>::max();
        int minQueuePort = -1;

        for (int port : nexthops) {
            uint32_t queueLen = CalculateInterfaceLoad(port);
            if (queueLen < minQueue) {
                minQueue = queueLen;
                minQueuePort = port;
            }
        }

        // Check if we should switch to a different path
        // Switch if current path queue > min queue * 1.2
        bool shouldSwitch = (minQueuePort != -1) &&
                            (currentPathQueue > minQueue * 12 / 10);  // 1.2 = 12/10

        int selectedPort;
        if (shouldSwitch && minQueuePort != -1) {
            // Switch to the path with minimum queue
            selectedPort = minQueuePort;
            // Update the path header with new port and queue length
            p->RemoveHeader(pathHdr);
            // Update current hop with new port and queue
            if (currentHopIdx < hops.size()) {
                // Replace the hop with updated info
                std::vector<InflexPathHeader::PathHop> updatedHops = hops;
                updatedHops[currentHopIdx].portId = selectedPort;
                updatedHops[currentHopIdx].queueLen = minQueue;
                // Clear and rebuild path header
                pathHdr.Clear();
                pathHdr.SetDirection(false);  // downlink
                pathHdr.SetCurrentHop(currentHopIdx);
                for (const auto& hop : updatedHops) {
                    pathHdr.AddHop(hop.switchId, hop.portId, hop.queueLen);
                }
            }
            p->AddHeader(pathHdr);
        } else {
            // Use the port from path header
            selectedPort = -1;
            for (int port : nexthops) {
                if (port == (int)nextPort) {
                    selectedPort = port;
                    break;
                }
            }
            if (selectedPort == -1) {
                // Planned port not available, use min queue port
                selectedPort = minQueuePort;
            }
            // Increment hop and update header
            pathHdr.IncrementHop();
            p->RemoveHeader(pathHdr);
            p->AddHeader(pathHdr);
        }

        return selectedPort;
    }
}

int SwitchNode::GetPortToSwitch(uint32_t switchId) {
    // Find the port that connects to the given switch
    // This requires looking up the routing table or device connections
    // For now, return -1 (not found)
    return -1;
}

} /* namespace ns3 */
