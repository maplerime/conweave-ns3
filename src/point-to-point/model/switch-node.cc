#include "switch-node.h"

#include "assert.h"
#include "ns3/boolean.h"
#include "ns3/channel.h"
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
    m_pfc_port_count = 0;  // Initialize PFC counter
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

// Initialize static members
uint32_t SwitchNode::m_inflexCallCount = 0;
uint32_t SwitchNode::m_inflexEcmpFallbackCount = 0;
uint64_t SwitchNode::m_pfcTriggeredProbeCount = 0;
uint64_t SwitchNode::m_queueTriggeredProbeCount = 0;
uint64_t SwitchNode::m_totalProbeSent = 0;
uint64_t SwitchNode::m_totalProbeReceived = 0;

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
#if (DEBUG_FLOW_TRACKING == true)
    std::cout << "[ECMP] Sw(" << m_id << ") " << Settings::hostIp2IdMap[ch.sip]
              << "->" << Settings::hostIp2IdMap[ch.dip]
              << " tag=" << (uint32_t)ch.udp.tag
              << " hash=" << hashVal
              << " path=" << idx << "/" << nexthops.size()
              << " out=" << nexthops[idx]
              << std::endl;
#endif
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
#if (DEBUG_FLOW_TRACKING == true)
    std::cout << "[DRILL] Sw(" << m_id << ") " << Settings::hostIp2IdMap[ch.sip]
              << "->" << Settings::hostIp2IdMap[ch.dip]
              << " tag=" << (uint32_t)ch.udp.tag
              << " out=" << leastLoadInterface
              << " qlen=" << leastLoad
              << " paths=" << nexthops.size()
              << std::endl;
#endif
    return leastLoadInterface;
}

/*------------------ConWeave Dummy ----------------*/
uint32_t SwitchNode::DoLbConWeave(Ptr<const Packet> p, const CustomHeader &ch,
                                  const std::vector<int> &nexthops) {
    return DoLbFlowECMP(p, ch, nexthops);  // flow ECMP (dummy)
}

/*------------------Adaptive Spraying (hybrid-as, lb_mode=13) ----------------*/
uint32_t SwitchNode::DoLbAdaptiveSpray(Ptr<const Packet> p, const CustomHeader &ch,
                                       const std::vector<int> &nexthops) {
    // Step 1: Calculate pathCost for all ports
    std::vector<uint64_t> pathCosts;
    uint64_t currentTime = Simulator::Now().GetNanoSeconds();
    const uint64_t PROBE_MAX_AGE = 10000;  // 10us

    for (int port : nexthops) {
        // Get local tx queue length
        Ptr<NetDevice> dev = GetDevice(port);
        uint32_t localTxQueue = 0;
        Ptr<QbbNetDevice> qbb = DynamicCast<QbbNetDevice>(dev);
        if (qbb) {
            localTxQueue = qbb->GetQueue()->GetNBytesTotal();
        }

        // Get remote queue length and PFC count
        uint32_t remoteRxQueueLen = 0;
        uint32_t remotePfcCount = 0;

        // For simplicity, use remote info based on switch type
        if (m_switchType == SWITCH_TYPE_TOR || m_switchType == SWITCH_TYPE_CORE) {
            auto itTimestamp = m_remoteProbeTimestamp.find(port);
            if (itTimestamp != m_remoteProbeTimestamp.end()) {
                if ((currentTime - itTimestamp->second) > PROBE_MAX_AGE) {
                    m_remoteRxQueueLen[port] = 0;
                }
            }
            auto it = m_remoteRxQueueLen.find(port);
            if (it != m_remoteRxQueueLen.end()) {
                remoteRxQueueLen = it->second;
            }
            auto itPfc = m_remotePfcPortCount.find(port);
            if (itPfc != m_remotePfcPortCount.end()) {
                remotePfcCount = itPfc->second;
            }
        } else if (m_switchType == SWITCH_TYPE_AGGREGATION) {
            uint32_t midPort = GetNDevices() / 2;
            bool isUplink = (static_cast<uint32_t>(port) > midPort);
            if (isUplink) {
                auto itTimestamp = m_uplinkProbeTimestamp.find(port);
                if (itTimestamp != m_uplinkProbeTimestamp.end()) {
                    if ((currentTime - itTimestamp->second) > PROBE_MAX_AGE) {
                        m_uplinkRxQueueLen[port] = 0;
                    }
                }
                auto it = m_uplinkRxQueueLen.find(port);
                if (it != m_uplinkRxQueueLen.end()) {
                    remoteRxQueueLen = it->second;
                }
                auto itPfc = m_uplinkPfcPortCount.find(port);
                if (itPfc != m_uplinkPfcPortCount.end()) {
                    remotePfcCount = itPfc->second;
                }
            } else {
                auto itTimestamp = m_downlinkProbeTimestamp.find(port);
                if (itTimestamp != m_downlinkProbeTimestamp.end()) {
                    if ((currentTime - itTimestamp->second) > PROBE_MAX_AGE) {
                        m_downlinkRxQueueLen[port] = 0;
                    }
                }
                auto it = m_downlinkRxQueueLen.find(port);
                if (it != m_downlinkRxQueueLen.end()) {
                    remoteRxQueueLen = it->second;
                }
                auto itPfc = m_downlinkPfcPortCount.find(port);
                if (itPfc != m_downlinkPfcPortCount.end()) {
                    remotePfcCount = itPfc->second;
                }
            }
        }

        // Calculate path cost: local + remote weighted by PFC
        uint64_t pathCost = localTxQueue + (remoteRxQueueLen + 8192) * (remotePfcCount + 1);
        pathCosts.push_back(pathCost);
    }

    // Step 2: Calculate inverse costs
    std::vector<double> invCosts;
    double totalInvCost = 0.0;
    for (uint64_t cost : pathCosts) {
        double invCost = 1.0 / static_cast<double>(cost + 1);
        invCosts.push_back(invCost);
        totalInvCost += invCost;
    }

    // Step 3: Roulette wheel selection
    double randVal = static_cast<double>(std::rand()) / RAND_MAX;
    double cumulative = 0.0;
    for (size_t i = 0; i < nexthops.size(); i++) {
        cumulative += invCosts[i] / totalInvCost;
        if (randVal < cumulative) {
            return nexthops[i];
        }
    }

    // Fallback: return last port
    return nexthops.back();
}

/*------------------Random Spraying (hybrid-ss, lb_mode=14) ----------------*/
uint32_t SwitchNode::DoLbRandomSpray(Ptr<const Packet> p, const CustomHeader &ch,
                                     const std::vector<int> &nexthops) {
    // Uniform random selection
    size_t idx = std::rand() % nexthops.size();
    return nexthops[idx];
}
/*----------------------------------*/

void SwitchNode::CheckAndSendPfc(uint32_t inDev, uint32_t qIndex) {
    Ptr<QbbNetDevice> device = DynamicCast<QbbNetDevice>(m_devices[inDev]);
    bool pClasses[qCnt] = {0};
    m_mmu->GetPauseClasses(inDev, qIndex, pClasses);

    bool pfcStateChanged = false;

    for (int j = 0; j < qCnt; j++) {
        if (pClasses[j]) {
            uint32_t paused_time = device->SendPfc(j, 0);
            m_mmu->SetPause(inDev, j, paused_time);
            // First time this queue enters PFC: increment counter
            if (!m_mmu->m_pause_remote[inDev][j]) {
                m_pfc_port_count++;
                pfcStateChanged = true;
            }
            m_mmu->m_pause_remote[inDev][j] = true;
            /** PAUSE SEND COUNT ++ */
        }
    }

    for (int j = 0; j < qCnt; j++) {
        if (!m_mmu->m_pause_remote[inDev][j]) continue;

        if (m_mmu->GetResumeClasses(inDev, j)) {
            device->SendPfc(j, 1);
            m_mmu->SetResume(inDev, j);
            // This queue exits PFC: decrement counter
            m_pfc_port_count--;
            pfcStateChanged = true;
            m_mmu->m_pause_remote[inDev][j] = false;
        }
    }

    // Send probe to all ports when PFC counter changes (immediate, no rate limit)
    if (pfcStateChanged && Settings::lb_mode == 12) {
        // PFC变化：立即向所有端口发送probe，不限速
        for (uint32_t port = 1; port < GetNDevices(); port++) {
            SendProbeToPortImmediate(port);
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

    // ECMP-Conweave hybrid (lb_mode=11): tag==2 uses ConWeave, others use ECMP
    // DISABLED
    // if (Settings::lb_mode == 11) {
    //     bool control_pkt = (ch.l3Prot == 0xFF || ch.l3Prot == 0xFE || ch.l3Prot == 0xFD || ch.l3Prot == 0xFC);
    //     if (!control_pkt && ch.udp.tag == 2) {
    //         m_mmu->m_conweaveRouting.RouteInput(p, ch);
    //         return;
    //     }
    //     // tag!=2 or control_pkt: fall through to SendToDevContinue -> GetOutDev -> ECMP
    // }

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
    // idx < 0: Check if this was a probe packet (processed locally, not an error)
    QueueProbeTag probeTag;
    if (!p->PeekPacketTag(probeTag)) {
        // Only print warning if it's not a probe packet
        std::cout << "WARNING - Drop occurs in SendToDevContinue()" << std::endl;
    }
    return;  // Drop otherwise
}

int SwitchNode::GetOutDev(Ptr<Packet> p, CustomHeader &ch) {
    // Check if this is a queue monitoring probe packet
    QueueProbeTag probeTag;
    if (p->PeekPacketTag(probeTag)) {
        // Process probe packet - broadcast to all ports for Agg, store for ToR
        static int probeRecvCount = 0;
        if (probeRecvCount < 10) {
            std::cerr << "[SW " << m_id << "] Received probe packet, QueueProbeTag found!" << std::endl;
            probeRecvCount++;
        }
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

    // Hybrid mode (lb_mode=10): use tag field to determine per-flow load balancing
    if (Settings::lb_mode == 10) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // tag == 0 (no tag) or tag == 2 -> DRILL, tag == 1 -> FlowECMP
        if (ch.udp.tag == 0 || ch.udp.tag == 2) {
            Settings::tag2_drill_count++;
#if (DEBUG_TAG_ROUTING == true)
            std::cout << "[Hybrid] tag=" << ch.udp.tag << " using DRILL (total=" << Settings::tag2_drill_count << ")" << std::endl;
#endif
            return DoLbDrill(p, ch, nexthops);
        }
        Settings::tag1_ecmp_count++;
#if (DEBUG_TAG_ROUTING == true)
        std::cout << "[Hybrid] tag=" << ch.udp.tag << " using ECMP (total=" << Settings::tag1_ecmp_count << ")" << std::endl;
#endif
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // ECMP-Conweave mode (lb_mode=11): use pg field to determine per-flow load balancing
    // DISABLED - Mode removed
    // if (Settings::lb_mode == 11) {
    //     return DoLbFlowECMP(p, ch, nexthops);
    // }

    // Inflex mode (lb_mode=12): explicit path selection based on queue monitoring
    if (Settings::lb_mode == 12) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // tag == 0 (no tag) or tag == 2 -> Inflex with queue monitoring, tag == 1 -> FlowECMP
        if (ch.udp.tag == 0 || ch.udp.tag == 2) {
            // Use Inflex path selection
            Settings::tag2_inflex_count++;
#if (DEBUG_TAG_ROUTING == true)
            std::cout << "[Inflex] tag=" << ch.udp.tag << " using Inflex (total=" << Settings::tag2_inflex_count << ")" << std::endl;
#endif
            if (m_switchType == SWITCH_TYPE_TOR) {
                return SelectInflexUplink(p, ch, nexthops);
            } else if (m_switchType == SWITCH_TYPE_CORE) {
                return SelectInflexDownlink(p, ch, nexthops);
            } else if (m_switchType == SWITCH_TYPE_AGGREGATION) {
                return SelectInflexAggForward(p, ch, nexthops);
            }
            return DoLbFlowECMP(p, ch, nexthops);
        }
        Settings::tag1_ecmp_count++;
#if (DEBUG_TAG_ROUTING == true)
        std::cout << "[Inflex] tag=" << ch.udp.tag << " using ECMP (total=" << Settings::tag1_ecmp_count << ")" << std::endl;
#endif
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // Hybrid-AS mode (lb_mode=13): Adaptive Spray for tag=2, ECMP for tag=1
    if (Settings::lb_mode == 13) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // tag == 0 (no tag) or tag == 2 -> Adaptive Spray, tag == 1 -> FlowECMP
        if (ch.udp.tag == 0 || ch.udp.tag == 2) {
            Settings::tag2_adaptive_spray_count++;
#if (DEBUG_TAG_ROUTING == true)
            std::cout << "[Hybrid-AS] tag=" << ch.udp.tag << " using Adaptive Spray (total=" << Settings::tag2_adaptive_spray_count << ")" << std::endl;
#endif
            return DoLbAdaptiveSpray(p, ch, nexthops);
        }
        Settings::tag1_ecmp_count++;
#if (DEBUG_TAG_ROUTING == true)
        std::cout << "[Hybrid-AS] tag=" << ch.udp.tag << " using ECMP (total=" << Settings::tag1_ecmp_count << ")" << std::endl;
#endif
        return DoLbFlowECMP(p, ch, nexthops);
    }

    // Hybrid-SS mode (lb_mode=14): Random Spray for tag=2, ECMP for tag=1
    if (Settings::lb_mode == 14) {
        if (control_pkt) {
            return DoLbFlowECMP(p, ch, nexthops);
        }
        // tag == 0 (no tag) or tag == 2 -> Random Spray, tag == 1 -> FlowECMP
        if (ch.udp.tag == 0 || ch.udp.tag == 2) {
            Settings::tag2_random_spray_count++;
#if (DEBUG_TAG_ROUTING == true)
            std::cout << "[Hybrid-SS] tag=" << ch.udp.tag << " using Random Spray (total=" << Settings::tag2_random_spray_count << ")" << std::endl;
#endif
            return DoLbRandomSpray(p, ch, nexthops);
        }
        Settings::tag1_ecmp_count++;
#if (DEBUG_TAG_ROUTING == true)
        std::cout << "[Hybrid-SS] tag=" << ch.udp.tag << " using ECMP (total=" << Settings::tag1_ecmp_count << ")" << std::endl;
#endif
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

                // Check if ingress buffer occupancy exceeds threshold for Inflex
                if (Settings::lb_mode == 12) {
                    uint32_t ingressBytes = m_mmu->GetIngressBufferBytes(inDev);
                    uint32_t maxBuffer = m_mmu->GetMaxBufferBytesPerPort();
                    uint32_t pgBytes = m_mmu->GetIngressPGBytes(inDev, qIndex);
                    const uint32_t PG_THRESHOLD = 15 * 1024;  // 15KB threshold for single PG

                    bool triggerProbe = false;
                    if (maxBuffer > 0 && (ingressBytes * 100 / maxBuffer) > QUEUE_OCCUPANCY_THRESHOLD) {
                        // Queue occupancy > 60%, send probe to notify connected switch
                        triggerProbe = true;
                    } else if (pgBytes > PG_THRESHOLD) {
                        // Single PG exceeds 15KB, also send probe
                        triggerProbe = true;
                    }

                    if (triggerProbe) {
                        SendProbeToPort(inDev);  // Rate limiting handled inside
                    }
                }
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
    // Event-driven probe generation: no periodic probes
    // Probes are sent when:
    // 1. PFC counter changes (in CheckAndSendPfc)
    // 2. Queue occupancy exceeds threshold (in UpdateIngressAdmission)
    if (Settings::lb_mode == 12) {
        const char* switchTypeStr = "Unknown";
        if (m_switchType == SWITCH_TYPE_TOR) switchTypeStr = "ToR";
        else if (m_switchType == SWITCH_TYPE_AGGREGATION) switchTypeStr = "Agg";
        else if (m_switchType == SWITCH_TYPE_CORE) switchTypeStr = "Core";

        std::cout << switchTypeStr << " switch " << m_id << " started event-driven probe generation" << std::endl;
    }
}

void SwitchNode::SendProbeToPort(uint32_t port) {
    // Rate-limited probe for queue>60% trigger (20us limit)
    SendProbeToPortInternal(port, PROBE_RATE_LIMIT_NS);
}

void SwitchNode::SendProbeToPortImmediate(uint32_t port) {
    // Immediate probe for PFC change trigger (no rate limit)
    SendProbeToPortInternal(port, 0);
}

void SwitchNode::SendProbeToPortInternal(uint32_t port, uint64_t rateLimitNs) {
    // Internal helper: send probe with specified rate limit
    // rateLimitNs = 0 means immediate, > 0 means minimum interval between probes

    if (Settings::lb_mode != 12) {
        return;
    }

    // Don't send probe to host ports (port 0 is invalid)
    if (port == 0) {
        return;
    }

    Ptr<NetDevice> dev = GetDevice(port);
    if (!dev || !dev->IsLinkUp()) {
        return;
    }

    // For ToR switches, check if connected to a host using topology
    Ptr<QbbNetDevice> qbbDev = DynamicCast<QbbNetDevice>(dev);
    if (!qbbDev) {
        return;
    }

    if (m_switchType == SWITCH_TYPE_TOR) {
        Ptr<Channel> channel = qbbDev->GetChannel();
        if (channel) {
            // Get the device at the other end of the channel
            Ptr<NetDevice> remoteDev = channel->GetDevice(0);
            if (remoteDev && remoteDev->GetNode()) {
                // Check if remote node is a host (nodeType == 0)
                if (remoteDev->GetNode()->GetNodeType() == 0) {
                    return;  // Skip host ports
                }
            }
        }
    }

    // Rate limiting (skip if rateLimitNs > 0 and not enough time passed)
    if (rateLimitNs > 0) {
        static std::map<uint32_t, uint64_t> lastProbeTime;
        uint64_t now = Simulator::Now().GetNanoSeconds();
        if (lastProbeTime.find(port) != lastProbeTime.end()) {
            if (now - lastProbeTime[port] < rateLimitNs) {
                return;  // Too soon, skip
            }
        }
        lastProbeTime[port] = now;
    }

    // Get ingress buffer occupancy for this port
    uint32_t rxQueueLen = m_mmu->GetIngressBufferBytes(port);

    // Construct probe packet with proper headers
    Ptr<Packet> p = Create<Packet>(0);
    QueueProbeTag probeTag;
    p->AddPacketTag(probeTag);

    QueueMonitorHeader monitorHdr;
    monitorHdr.SetSenderRxQueueLen(rxQueueLen);
    monitorHdr.SetSenderPfcPortCount(m_pfc_port_count);
    p->AddHeader(monitorHdr);

    // Add IPv4 header
    Ipv4Header ipv4h;
    ipv4h.SetProtocol(0xFB);
    ipv4h.SetSource(GetObject<Ipv4>()->GetAddress(port, 0).GetLocal());
    ipv4h.SetDestination(Ipv4Address("255.255.255.255"));
    ipv4h.SetPayloadSize(p->GetSize());
    ipv4h.SetTtl(1);
    ipv4h.SetIdentification(m_id * 1000 + port);
    p->AddHeader(ipv4h);

    // Add PPP header
    PppHeader ppp;
    ppp.SetProtocol(0x0021);
    p->AddHeader(ppp);

    // Create CustomHeader for parsing
    CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header);
    ch.getInt = 0;
    p->PeekHeader(ch);

    // Update probe statistics
    m_totalProbeSent++;
    if (rateLimitNs == 0) {
        m_pfcTriggeredProbeCount++;  // Immediate probe = PFC triggered
    } else {
        m_queueTriggeredProbeCount++;  // Rate-limited probe = Queue triggered
    }

    // Send via SwitchSend with queue 0
    qbbDev->SwitchSend(0, p, ch);
}

void SwitchNode::ProcessProbePacket(Ptr<Packet> p, uint32_t inDev) {
    // Update probe statistics
    m_totalProbeReceived++;

    // Remove headers in reverse order: PPP -> IPv4 -> QueueMonitor
    PppHeader ppp;
    p->RemoveHeader(ppp);

    Ipv4Header ipv4h;
    p->RemoveHeader(ipv4h);

    QueueMonitorHeader monitorHdr;
    p->PeekHeader(monitorHdr);
    p->RemoveHeader(monitorHdr);

    // Get the rx queue length and PFC port count from the sender
    uint32_t receivedRxQueueLen = monitorHdr.GetSenderRxQueueLen();
    uint32_t receivedPfcPortCount = monitorHdr.GetSenderPfcPortCount();

    // Get current timestamp
    uint64_t currentTime = Simulator::Now().GetNanoSeconds();

    // Store the received remote queue info based on switch type
    if (m_switchType == SWITCH_TYPE_AGGREGATION) {
        // Aggregation switch: store separately for uplink and downlink
        uint32_t midPort = GetNDevices() / 2;
        bool isFromCore = (inDev > midPort);

        if (isFromCore) {
            // From Core: store in uplink storage
            m_uplinkRxQueueLen[inDev] = receivedRxQueueLen;
            m_uplinkPfcPortCount[inDev] = receivedPfcPortCount;
            m_uplinkProbeTimestamp[inDev] = currentTime;
        } else {
            // From ToR: store in downlink storage
            m_downlinkRxQueueLen[inDev] = receivedRxQueueLen;
            m_downlinkPfcPortCount[inDev] = receivedPfcPortCount;
            m_downlinkProbeTimestamp[inDev] = currentTime;
        }

        // Debug logging
        static int aggProbeRecvCount = 0;
        if (aggProbeRecvCount < 5) {
            const char* fromType = isFromCore ? "Core" : "ToR";
            std::cout << "[Agg " << m_id << "] Received probe from " << fromType
                      << " on port " << inDev << ", rxQueueLen=" << receivedRxQueueLen
                      << ", pfcCount=" << receivedPfcPortCount
                      << ", timestamp=" << currentTime << std::endl;
            aggProbeRecvCount++;
        }
    }
    else if (m_switchType == SWITCH_TYPE_TOR) {
        // ToR switch: store remote queue info from Agg
        m_remoteRxQueueLen[inDev] = receivedRxQueueLen;
        m_remotePfcPortCount[inDev] = receivedPfcPortCount;
        m_remoteProbeTimestamp[inDev] = currentTime;

        // Debug logging
        static int torProbeRecvCount = 0;
        if (torProbeRecvCount < 5) {
            std::cout << "[ToR " << m_id << "] Received probe on port " << inDev
                      << ", remoteRxQueueLen=" << receivedRxQueueLen
                      << ", remotePfcCount=" << receivedPfcPortCount
                      << ", timestamp=" << currentTime << std::endl;
            torProbeRecvCount++;
        }
    }
    else if (m_switchType == SWITCH_TYPE_CORE) {
        // Core switch: store remote queue info from Agg
        m_remoteRxQueueLen[inDev] = receivedRxQueueLen;
        m_remotePfcPortCount[inDev] = receivedPfcPortCount;
        m_remoteProbeTimestamp[inDev] = currentTime;

        // Debug logging
        static int coreProbeRecvCount = 0;
        if (coreProbeRecvCount < 5) {
            std::cout << "[Core " << m_id << "] Received probe on port " << inDev
                      << ", remoteRxQueueLen=" << receivedRxQueueLen
                      << ", remotePfcCount=" << receivedPfcPortCount
                      << ", timestamp=" << currentTime << std::endl;
            coreProbeRecvCount++;
        }
    }
}

/********************************************
 *     Inflex Path Selection Implementation *
 *******************************************/

// Helper function to get local queue bytes for a port
static uint32_t GetLocalQueueBytes(Ptr<NetDevice> dev) {
    Ptr<QbbNetDevice> qbb = DynamicCast<QbbNetDevice>(dev);
    if (qbb) {
        return qbb->GetQueue()->GetNBytesTotal();
    }
    return 0;
}

// Helper function to get remote info and check probe age
static void GetRemoteInfo(uint64_t currentTime, uint32_t port,
                          const std::map<uint32_t, uint64_t>& probeTimestamp,
                          std::map<uint32_t, uint32_t>& rxQueueLen,
                          const std::map<uint32_t, uint32_t>& pfcPortCount,
                          uint32_t& remoteRxQueueLen, uint32_t& remotePfcCount,
                          const uint64_t PROBE_MAX_AGE) {
    auto itTimestamp = probeTimestamp.find(port);
    if (itTimestamp != probeTimestamp.end()) {
        if ((currentTime - itTimestamp->second) > PROBE_MAX_AGE) {
            rxQueueLen[port] = 0;  // Probe info is stale
        }
    }
    auto it = rxQueueLen.find(port);
    if (it != rxQueueLen.end()) {
        remoteRxQueueLen = it->second;
    }
    auto itPfc = pfcPortCount.find(port);
    if (itPfc != pfcPortCount.end()) {
        remotePfcCount = itPfc->second;
    }
}

// Helper function to extract FlowKey from CustomHeader
static FlowKey ExtractFlowKey(const CustomHeader &ch) {
    FlowKey key;
    key.sip = ch.sip;
    key.dip = ch.dip;
    key.proto = ch.l3Prot;

    // Extract source and destination ports based on protocol
    if (ch.l3Prot == 0x6) {  // TCP
        key.sport = ch.tcp.sport;
        key.dport = ch.tcp.dport;
    } else if (ch.l3Prot == 0x11) {  // UDP
        key.sport = ch.udp.sport;
        key.dport = ch.udp.dport;
    } else if (ch.l3Prot == 0xFC || ch.l3Prot == 0xFD) {  // ACK or NACK
        key.sport = ch.ack.sport;
        key.dport = ch.ack.dport;
    } else {
        key.sport = 0;
        key.dport = 0;
    }
    return key;
}

// Helper function to calculate path cost for a single port
// pathCost = localTxQueue + (remoteRxQueueLen + 8192) * (remotePfcCount + 1)
static uint64_t CalculatePortCost(SwitchNode* node, int port,
                                   const std::map<uint32_t, uint64_t>* uplinkProbeTimestamp,
                                   std::map<uint32_t, uint32_t>* uplinkRxQueueLen,
                                   const std::map<uint32_t, uint32_t>* uplinkPfcPortCount,
                                   const std::map<uint32_t, uint64_t>* downlinkProbeTimestamp,
                                   std::map<uint32_t, uint32_t>* downlinkRxQueueLen,
                                   const std::map<uint32_t, uint32_t>* downlinkPfcPortCount,
                                   bool useUplinkInfo, bool useDownlinkInfo, int splitPort,
                                   uint64_t currentTime) {
    const uint64_t PROBE_MAX_AGE = 10000;  // 10us

    // Get local tx queue length
    Ptr<NetDevice> dev = node->GetDevice(port);
    uint32_t localTxQueue = GetLocalQueueBytes(dev);

    // Get remote queue length and PFC count
    uint32_t remoteRxQueueLen = 0;
    uint32_t remotePfcCount = 0;

    bool checkUplink = useUplinkInfo;
    bool checkDownlink = useDownlinkInfo;

    // For Agg: determine direction by port number
    if (useUplinkInfo && useDownlinkInfo && splitPort >= 0) {
        checkUplink = (static_cast<uint32_t>(port) > static_cast<uint32_t>(splitPort));
        checkDownlink = !checkUplink;
    }

    if (checkUplink && uplinkProbeTimestamp) {
        GetRemoteInfo(currentTime, static_cast<uint32_t>(port), *uplinkProbeTimestamp, *uplinkRxQueueLen,
                     *uplinkPfcPortCount, remoteRxQueueLen, remotePfcCount, PROBE_MAX_AGE);
    } else if (checkDownlink && downlinkProbeTimestamp) {
        GetRemoteInfo(currentTime, static_cast<uint32_t>(port), *downlinkProbeTimestamp, *downlinkRxQueueLen,
                     *downlinkPfcPortCount, remoteRxQueueLen, remotePfcCount, PROBE_MAX_AGE);
    }

    // Calculate path cost: local + remote weighted by PFC
    // Formula: localTxQueue + remoteRxQueueLen * 1.2 + remotePFCCount * 1024
    return localTxQueue + (remoteRxQueueLen * 6) / 5 + remotePfcCount * 1024;
}

// Unified Inflex path selection: find port with minimum path cost
// Borrow DRILL's strategy: per-destination stickiness + 2 random sampled ports
static int SelectInflexPath(SwitchNode* node, Ptr<Packet> p, CustomHeader &ch,
                            const std::vector<int> &nexthops,
                            const std::map<uint32_t, uint64_t>* uplinkProbeTimestamp,
                            std::map<uint32_t, uint32_t>* uplinkRxQueueLen,
                            const std::map<uint32_t, uint32_t>* uplinkPfcPortCount,
                            const std::map<uint32_t, uint64_t>* downlinkProbeTimestamp,
                            std::map<uint32_t, uint32_t>* downlinkRxQueueLen,
                            const std::map<uint32_t, uint32_t>* downlinkPfcPortCount,
                            bool useUplinkInfo, bool useDownlinkInfo, int splitPort) {
    node->m_inflexCallCount++;

    uint64_t currentTime = Simulator::Now().GetNanoSeconds();

    // Step 1: Check cached best port for this destination (like DRILL)
    int bestPort = nexthops[0];
    uint64_t minCost = std::numeric_limits<uint64_t>::max();
    bool hasCachedPort = false;

    auto itr = node->m_inflexBestPortMap.find(ch.dip);
    if (itr != node->m_inflexBestPortMap.end()) {
        int cachedPort = itr->second;
        // Verify cached port is still valid (in nexthops)
        if (std::find(nexthops.begin(), nexthops.end(), cachedPort) != nexthops.end()) {
            uint64_t cachedCost = CalculatePortCost(node, cachedPort, uplinkProbeTimestamp, uplinkRxQueueLen,
                                                     uplinkPfcPortCount, downlinkProbeTimestamp, downlinkRxQueueLen,
                                                     downlinkPfcPortCount, useUplinkInfo, useDownlinkInfo,
                                                     splitPort, currentTime);
            minCost = cachedCost;
            bestPort = cachedPort;
            hasCachedPort = true;
        }
    }

    // Step 2: Sample 2 random ports (like DRILL)
    auto rand_nexthops = nexthops;
    std::random_shuffle(rand_nexthops.begin(), rand_nexthops.end());

    const uint32_t INFLEX_SAMPLE_NUM = 2;  // Sample 2 ports
    uint32_t sampleNum = std::min(INFLEX_SAMPLE_NUM, static_cast<uint32_t>(rand_nexthops.size()));

    for (uint32_t i = 0; i < sampleNum; i++) {
        int port = rand_nexthops[i];
        // Skip the cached port if it's in the sampled list
        if (hasCachedPort && port == bestPort) {
            continue;
        }

        uint64_t portCost = CalculatePortCost(node, port, uplinkProbeTimestamp, uplinkRxQueueLen,
                                               uplinkPfcPortCount, downlinkProbeTimestamp, downlinkRxQueueLen,
                                               downlinkPfcPortCount, useUplinkInfo, useDownlinkInfo,
                                               splitPort, currentTime);

        if (portCost < minCost) {
            minCost = portCost;
            bestPort = port;
        }
    }

    // Step 3: Update cache (like DRILL)
    node->m_inflexBestPortMap[ch.dip] = bestPort;

    return bestPort;
}

int SwitchNode::SelectInflexUplink(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    return SelectInflexPath(this, p, ch, nexthops,
                           &m_uplinkProbeTimestamp, &m_uplinkRxQueueLen, &m_uplinkPfcPortCount,
                           nullptr, nullptr, nullptr,
                           true, false, -1);
}

int SwitchNode::SelectInflexDownlink(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    return SelectInflexPath(this, p, ch, nexthops,
                           nullptr, nullptr, nullptr,
                           &m_downlinkProbeTimestamp, &m_downlinkRxQueueLen, &m_downlinkPfcPortCount,
                           false, true, -1);
}

int SwitchNode::SelectInflexAggForward(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops) {
    uint32_t midPort = GetNDevices() / 2;
    return SelectInflexPath(this, p, ch, nexthops,
                           &m_uplinkProbeTimestamp, &m_uplinkRxQueueLen, &m_uplinkPfcPortCount,
                           &m_downlinkProbeTimestamp, &m_downlinkRxQueueLen, &m_downlinkPfcPortCount,
                           true, true, midPort);
}

int SwitchNode::GetPortToSwitch(uint32_t switchId) {
    // Find the port that connects to the given switch
    // This requires looking up the routing table or device connections
    // For now, return -1 (not found)
    return -1;
}

} /* namespace ns3 */
