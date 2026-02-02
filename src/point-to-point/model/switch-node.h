#ifndef SWITCH_NODE_H
#define SWITCH_NODE_H

#include <ns3/node.h>

#include <unordered_map>
#include <unordered_set>

#include "flowslice-sender.h"
#include "qbb-net-device.h"
#include "switch-mmu.h"

namespace ns3 {

class Packet;

class SwitchNode : public Node {
    static const unsigned qCnt = 8;    // Number of queues/priorities used
    static const unsigned pCnt = 128;  // port 0 is not used so + 1	// Number of ports used
    uint32_t m_ecmpSeed;
    std::unordered_map<uint32_t, std::vector<int> >
        m_rtTable;  // map from ip address (u32) to possible ECMP port (index of dev)

    // monitor uplinks
    uint64_t m_txBytes[pCnt];  // counter of tx bytes, for HPCC

   protected:
    bool m_ecnEnabled;
    uint32_t m_ccMode;
    uint32_t m_ackHighPrio;  // set high priority for ACK/NACK

   private:
    int GetOutDev(Ptr<Packet>, CustomHeader &ch);
    void SendToDev(Ptr<Packet> p, CustomHeader &ch);
    void SendToDevContinue(Ptr<Packet> p, CustomHeader &ch);
    static uint32_t EcmpHash(const uint8_t *key, size_t len, uint32_t seed);
    void CheckAndSendPfc(uint32_t inDev, uint32_t qIndex);
    void CheckAndSendResume(uint32_t inDev, uint32_t qIndex);

    /* Sending packet to Egress port */
    void DoSwitchSend(Ptr<Packet> p, CustomHeader &ch, uint32_t outDev, uint32_t qIndex);

    /*----- Load balancer -----*/
    // Flow ECMP (lb_mode = 0)
    uint32_t DoLbFlowECMP(Ptr<const Packet> p, const CustomHeader &ch,
                          const std::vector<int> &nexthops);
    // DRILL (lb_mode = 2)
    uint32_t DoLbDrill(Ptr<const Packet> p, const CustomHeader &ch,
                       const std::vector<int> &nexthops);     // choose egress port
    uint32_t m_drill_candidate;                               // always 2 (power of two)
    std::map<uint32_t, uint32_t> m_previousBestInterfaceMap;  // <dip, previousBestInterface>
    uint32_t CalculateInterfaceLoad(uint32_t interface);      // Get the load of a interface
    // Hybrid (lb_mode = 10): ECMP for small flows, DRILL for large flows
    uint32_t DoLbHybrid(Ptr<const Packet> p, const CustomHeader &ch,
                       const std::vector<int> &nexthops);
    std::map<uint64_t, bool> m_flowUseDrill;                  // <flowHash, useDrill> per flow
    std::map<uint64_t, uint64_t> m_flowByteCount;             // <flowHash, byteCount> track flow size
    // Conga (lb_mode = 3)
    uint32_t DoLbConga(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // Conga (lb_mode = 6)
    uint32_t DoLbLetflow(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // ConWeave (lb_mode = 9)
    uint32_t DoLbConWeave(Ptr<const Packet> p, const CustomHeader &ch,
                           const std::vector<int> &nexthops);  // dummy
    // FlowSlice (lb_mode = 11): RTT-based dynamic flow slicing
    uint32_t DoLbFlowSlice(Ptr<const Packet> p, const CustomHeader &ch,
                           const std::vector<int> &nexthops);

    // Per-slice port mapping at each switch
    // All packets with the same slice_id must use the same port at this switch
    std::map<uint64_t, uint32_t> m_sliceIdToPort;  // <slice_id, out_port>

    // For RTT measurement at destination ToR
    // Track per-path RTT for each flow to calculate max-min difference
    struct PathRttInfo {
        uint64_t phase0_tx_time;     // First packet Tx timestamp from source
        Time phase0_rx_time;          // First packet Rx time at destination
        uint32_t path_id;             // Path identifier
        bool active;                  // Whether this path is active

        PathRttInfo() : phase0_tx_time(0), path_id(0), active(false) {}
    };

    // Per-flow, per-path RTT tracking: <flow_key, <path_id, PathRttInfo>>
    std::map<uint64_t, std::map<uint32_t, PathRttInfo>> m_flowPathRtt;

    // Per-flow current slice tracking: <flow_key, <slice_id, first_rx_time>>
    std::map<uint64_t, std::map<uint32_t, Time>> m_flowSliceRxTime;

    /* FlowSlice Sender - for source ToR to tag packets and process RTT feedback */
    Ptr<FlowSliceSender> m_flowSliceSender;

   public:
    // Ptr<BroadcomNode> m_broadcom;
    Ptr<SwitchMmu> m_mmu;
    bool m_isToR;                                 // true if ToR switch
    std::unordered_set<uint32_t> m_isToR_hostIP;  // host's IP connected to this ToR

    static TypeId GetTypeId(void);
    SwitchNode();
    void SetEcmpSeed(uint32_t seed);
    void AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx);
    void ClearTable();
    bool SwitchReceiveFromDevice(Ptr<NetDevice> device, Ptr<Packet> packet, CustomHeader &ch);
    void SwitchNotifyDequeue(uint32_t ifIndex, uint32_t qIndex, Ptr<Packet> p);
    uint64_t GetTxBytesOutDev(uint32_t outdev);
};

} /* namespace ns3 */

#endif /* SWITCH_NODE_H */
