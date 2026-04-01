#ifndef SWITCH_NODE_H
#define SWITCH_NODE_H

#include <ns3/node.h>

#include <unordered_map>
#include <unordered_set>

#include "qbb-net-device.h"
#include "switch-mmu.h"
#include "queue-monitor-header.h"
#include <map>

namespace ns3 {

class Packet;

// Switch type enumeration
enum SwitchType {
    SWITCH_TYPE_UNKNOWN = 0,
    SWITCH_TYPE_TOR = 1,         // Top-of-Rack switch
    SWITCH_TYPE_AGGREGATION = 2, // Aggregation switch
    SWITCH_TYPE_CORE = 3         // Core switch
};

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
    // Conga (lb_mode = 3)
    uint32_t DoLbConga(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // Conga (lb_mode = 6)
    uint32_t DoLbLetflow(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // ConWeave (lb_mode = 9)
    uint32_t DoLbConWeave(Ptr<const Packet> p, const CustomHeader &ch,
                           const std::vector<int> &nexthops);  // dummy

   public:
    // Ptr<BroadcomNode> m_broadcom;
    Ptr<SwitchMmu> m_mmu;
    bool m_isToR;                                 // true if ToR switch
    SwitchType m_switchType;                     // switch type (TOR/AGGREGATION/CORE)
    std::unordered_set<uint32_t> m_isToR_hostIP;  // host's IP connected to this ToR

    /*----- Queue Monitoring -----*/
    // Stored queue info from probes (what downstream switches see from us)
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
    static const uint64_t DEFAULT_PROBE_INTERVAL = 50000;  // 50us in nanoseconds

    // Queue monitoring methods
    void StartProbeGeneration();
    void GenerateAndSendProbe();
    void ProcessProbePacket(Ptr<Packet> p, uint32_t inDev);
    void AttachQueueMonitorToPacket(Ptr<Packet> p, uint32_t outDev);

    /*----- Inflex Path Selection -----*/
    // Select uplink path (ToR -> Agg -> Core) based on queue info
    int SelectInflexUplink(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // Select downlink path (Core -> Agg -> ToR) based on queue info
    int SelectInflexDownlink(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // Forward packet at Agg based on path header or Drill
    int SelectInflexAggForward(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // Get port to reach specific switch
    int GetPortToSwitch(uint32_t switchId);

    static TypeId GetTypeId(void);
    SwitchNode();
    void SetEcmpSeed(uint32_t seed);
    void SetSwitchType(SwitchType type);
    SwitchType GetSwitchType() const;
    void AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx);
    void ClearTable();
    bool SwitchReceiveFromDevice(Ptr<NetDevice> device, Ptr<Packet> packet, CustomHeader &ch);
    void SwitchNotifyDequeue(uint32_t ifIndex, uint32_t qIndex, Ptr<Packet> p);
    uint64_t GetTxBytesOutDev(uint32_t outdev);
};

} /* namespace ns3 */

#endif /* SWITCH_NODE_H */
