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

// Flow key for 5-tuple identification
struct FlowKey {
    uint32_t sip;
    uint32_t dip;
    uint16_t sport;
    uint16_t dport;
    uint8_t proto;

    bool operator<(const FlowKey& other) const {
        if (sip != other.sip) return sip < other.sip;
        if (dip != other.dip) return dip < other.dip;
        if (sport != other.sport) return sport < other.sport;
        if (dport != other.dport) return dport < other.dport;
        return proto < other.proto;
    }
};

// Flow entry with sticky weight
struct FlowEntry {
    int port;           // Cached path
    uint8_t weight;     // Remaining sticky weight (default 4)
};

// Reorder statistics (public for access from simulation script)
struct ReorderStats {
    uint64_t total_buffered;       // Total packets buffered
    uint64_t total_flushed;        // Total packets flushed from buffer
    uint64_t total_dropped;        // Total packets dropped (queue full + mismatch)
    uint64_t total_mismatch_drops; // Drops due to counter mismatch
    uint32_t max_q_size;          // Maximum queue size observed
    uint32_t active_flows;        // Number of flows with buffered packets
};

// Per-flow reorder statistics
struct FlowReorderStats {
    uint64_t packets_received;     // Total packets received for this flow
    uint64_t packets_direct_sent;  // Packets sent directly (in order)
    uint64_t packets_buffered;     // Packets buffered (out of order)
    uint64_t packets_flushed;      // Packets flushed from buffer
    uint64_t packets_dropped;      // Packets dropped (mismatch)
    uint32_t max_buffer_size;      // Maximum buffer size observed

    FlowReorderStats() : packets_received(0), packets_direct_sent(0),
                        packets_buffered(0), packets_flushed(0),
                        packets_dropped(0), max_buffer_size(0) {}
};

// Flow reorder buffer for destination ToR (ECMP counter reordering)
struct FlowReorderBuffer {
    uint16_t expected_counter;              // Next expected counter value
    std::vector<std::queue<Ptr<Packet>>> queues;  // FIFO queues (index = counter % num_queues)
    FlowReorderStats stats;                 // Per-flow statistics

    FlowReorderBuffer() : expected_counter(0) {
        // queues will be resized when buffer is created (in switch-node.cc)
    }
};

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
    void SendToDev(Ptr<Packet> p, CustomHeader &ch, uint32_t inPort);
    void SendToDevContinue(Ptr<Packet> p, CustomHeader &ch);
    uint32_t m_currentInPort;  // Store input port for current packet
    static uint32_t EcmpHash(const uint8_t *key, size_t len, uint32_t seed);
    void CheckAndSendPfc(uint32_t inDev, uint32_t qIndex);
    void CheckAndSendResume(uint32_t inDev, uint32_t qIndex);

    /*----- ECMP Counter Reorder Buffer (for destination ToR) -----*/
    std::map<FlowKey, FlowReorderBuffer> m_flowReorderBuffers;  // Per-flow reorder buffers
    std::map<FlowKey, FlowReorderStats> m_flowReorderStats;     // Per-flow statistics
    bool IsHostPort(uint32_t port);  // Check if port connects to host
    bool ProcessReorderBuffer(Ptr<Packet> p, CustomHeader &ch, uint32_t outPort);
    void FlushReorderQueue(FlowKey &flowKey, FlowReorderBuffer &buf, uint32_t outPort);

    // Reorder statistics
    ReorderStats m_reorderStats;     // Statistics for reorder buffer

    // Live reorder-buffer occupancy (across all flows on this switch), for memory peak analysis
    uint64_t m_curReorderPkts = 0;   // packets currently held in reorder buffer (all flows)
    uint64_t m_curReorderBytes = 0;  // bytes currently held in reorder buffer (all flows)
    uint64_t m_maxReorderPkts = 0;   // peak concurrent packets
    uint64_t m_maxReorderBytes = 0;  // peak concurrent bytes

    /* Sending packet to Egress port */
    void DoSwitchSend(Ptr<Packet> p, CustomHeader &ch, uint32_t outDev, uint32_t qIndex);

    /*----- Load balancer -----*/
    // Flow ECMP (lb_mode = 0)
    uint32_t DoLbFlowECMP(Ptr<const Packet> p, const CustomHeader &ch,
                          const std::vector<int> &nexthops);
    // Flow ECMP with input port (includes in_port in hash)
    uint32_t DoLbFlowECMPWithInPort(Ptr<const Packet> p, const CustomHeader &ch,
                                    const std::vector<int> &nexthops, uint32_t inPort);
    // Flow ECMP with ecmp_counter (includes ecmp_counter in hash)
    uint32_t DoLbFlowECMPWithCounter(Ptr<const Packet> p, const CustomHeader &ch,
                                     const std::vector<int> &nexthops);
    // Compare two paths (with and without inPort), select the better one
    uint32_t DoLbFlowCompareWithInPort(Ptr<const Packet> p, const CustomHeader &ch,
                                       const std::vector<int> &nexthops, uint32_t inPort);
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
    // Adaptive Spraying (lb_mode = 13, hybrid-as)
    uint32_t DoLbAdaptiveSpray(Ptr<const Packet> p, const CustomHeader &ch,
                                const std::vector<int> &nexthops);
    // Random Spraying (lb_mode = 14, hybrid-ss)
    uint32_t DoLbRandomSpray(Ptr<const Packet> p, const CustomHeader &ch,
                              const std::vector<int> &nexthops);

   public:
    // Ptr<BroadcomNode> m_broadcom;
    Ptr<SwitchMmu> m_mmu;
    bool m_isToR;                                 // true if ToR switch
    SwitchType m_switchType;                     // switch type (TOR/AGGREGATION/CORE)
    std::unordered_set<uint32_t> m_isToR_hostIP;  // host's IP connected to this ToR

    /*----- Queue Monitoring -----*/
    // Receiving queue length from remote switches
    // Map: receiving_port -> rx_queue_length on remote end
    std::map<uint32_t, uint32_t> m_remoteRxQueueLen;  // For Tor and Core
    std::map<uint32_t, uint64_t> m_remoteProbeTimestamp;  // port -> timestamp when probe was received

    // For Aggregation switch: separate uplink and downlink storage
    // Uplink: from Core (receiving_port -> rx_queue_length from Core)
    std::map<uint32_t, uint32_t> m_uplinkRxQueueLen;
    std::map<uint32_t, uint64_t> m_uplinkProbeTimestamp;  // timestamp for uplink
    // Downlink: from Tor (receiving_port -> rx_queue_length from Tor)
    std::map<uint32_t, uint32_t> m_downlinkRxQueueLen;
    std::map<uint32_t, uint64_t> m_downlinkProbeTimestamp;  // timestamp for downlink

    // Remote PFC port count storage (received from probes)
    // For Tor and Core: receiving_port -> PFC port count at remote end
    std::map<uint32_t, uint32_t> m_remotePfcPortCount;
    // For Aggregation: separate uplink and downlink storage
    std::map<uint32_t, uint32_t> m_uplinkPfcPortCount;   // from Core
    std::map<uint32_t, uint32_t> m_downlinkPfcPortCount; // from Tor

    /*----- PFC Port Tracking -----*/
    // Track which ports currently have at least one queue in PFC pause state
    std::unordered_set<uint32_t> m_pfc_ports;  // Set of ports in PFC pause state
    uint32_t m_pfc_port_count;                  // Current number of ports in PFC state

    // Inflex call counter (for debugging)
    static uint32_t m_inflexCallCount;
    static uint32_t m_inflexEcmpFallbackCount;
    std::map<uint32_t, uint32_t> m_inflexBestPortMap;  // <dip, previousBestPort> for Inflex

    // Flow stickiness with weight (Inflex)
    std::map<FlowKey, FlowEntry> m_flowStickyMap;  // <flowKey, port + weight>
    static const uint8_t STICKY_WEIGHT_DEFAULT = 4;  // Default sticky weight

    // Probe statistics
    static uint64_t m_pfcTriggeredProbeCount;   // Probes triggered by PFC change
    static uint64_t m_queueTriggeredProbeCount; // Probes triggered by queue>60%
    static uint64_t m_totalProbeSent;           // Total probes sent
    static uint64_t m_totalProbeReceived;       // Total probes received

    // Reorder output file (for mode 16, all flows per-flow statistics)
    static FILE* m_reorderOutputFile;
    static std::string m_reorderOutputFilename;

    // Probe generation - event driven (PFC change or queue occupancy > 60%)
    static const uint64_t QUEUE_OCCUPANCY_THRESHOLD = 60;  // 60% threshold for sending probe
    static const uint64_t PROBE_RATE_LIMIT_NS = 10000;     // 10us minimum between probes

    // Queue monitoring methods
    void StartProbeGeneration();
    void SendProbeToPort(uint32_t port);  // Rate-limited probe for queue>60% trigger
    void SendProbeToPortImmediate(uint32_t port);  // Immediate probe for PFC change trigger
    void ProcessProbePacket(Ptr<Packet> p, uint32_t inDev);

private:
    void SendProbeToPortInternal(uint32_t port, uint64_t rateLimitNs);  // Internal helper

   public:
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

    /*----- PFC Port Tracking -----*/
    uint32_t GetPfcPortCount() const { return m_pfc_port_count; }

    /*----- Reorder Buffer Statistics -----*/
    void GetReorderStats(ReorderStats &stats);
    // Get per-flow reorder statistics (key: sip,sport,dip,dport,proto, value: stats)
    const std::map<FlowKey, FlowReorderStats>& GetFlowReorderStats() const { return m_flowReorderStats; }
    // Output and clear reorder stats for a specific flow (mode 16, all flows)
    void OutputAndClearFlowReorder(uint32_t sip, uint32_t dip, uint16_t sport, uint16_t dport);
    // Open reorder output file
    static void OpenReorderOutputFile(const std::string& path);
};

} /* namespace ns3 */

#endif /* SWITCH_NODE_H */
