/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * FlowSlice Sender
 *
 * Dynamic flow slicing at source ToR based on local queue shortest path principle
 * - Default slice size: 32KB
 * - Switch path after each slice completes
 * - Tag packets with subflow_id and sequence number
 */

#ifndef __FLOWSLICE_SENDER_H__
#define __FLOWSLICE_SENDER_H__

#include <map>
#include <unordered_map>

#include "ns3/callback.h"
#include "ns3/custom-header.h"
#include "ns3/event-id.h"
#include "ns3/log.h"
#include "ns3/packet.h"
#include "ns3/ptr.h"
#include "ns3/simulator.h"

namespace ns3 {

/**
 * @brief Tag for FlowSlice packets (carries slice_id, sequence number, and timestamps)
 *
 * Each slice has a unique identifier. All packets in the same slice must travel
 * through the same path at each hop. Switches use slice_id to maintain per-slice
 * port mapping.
 */
class FlowSliceTag : public Tag {
public:
    FlowSliceTag();

    // Slice identification
    void SetSliceId(uint64_t slice_id);
    uint64_t GetSliceId() const;
    void SetFlowKey(uint64_t flow_key);
    uint64_t GetFlowKey() const;

    // Sequence number (for reordering at destination)
    void SetSequenceNumber(uint32_t seq_num);
    uint32_t GetSequenceNumber() const;

    // Timestamps for RTT measurement
    void SetTimestampTx(uint64_t timestamp);
    uint64_t GetTimestampTx() const;
    void SetTimestampTail(uint64_t timestamp);
    uint64_t GetTimestampTail() const;

    // Flags
    void SetFlag(uint32_t flag);
    uint32_t GetFlag() const;

    static TypeId GetTypeId();
    virtual TypeId GetInstanceTypeId() const;
    virtual uint32_t GetSerializedSize() const;
    virtual void Serialize(TagBuffer i) const;
    virtual void Deserialize(TagBuffer i);
    virtual void Print(std::ostream& os) const;

    enum Flag {
        DATA = 0,   // Normal data packet
        TAIL = 1,   // Last packet of current slice (triggers RTT measurement)
    };

private:
    uint64_t m_slice_id;        // Unique slice identifier (flow_key << 16 | slice_num)
    uint64_t m_flow_key;        // Flow identifier (4-tuple hash)
    uint32_t m_sequence_number; // Sequence number within flow
    uint64_t m_timestamp_tx;    // Timestamp when packet was sent from source ToR
    uint64_t m_timestamp_tail;  // Timestamp of tail packet from previous slice
    uint32_t m_flag;            // Packet flag (DATA/TAIL)
};

/**
 * @brief Per-flow sender state for FlowSlice
 */
struct FlowSliceSenderState {
    uint64_t flow_key;              // Flow identifier (4-tuple hash)
    uint32_t current_slice;         // Current slice number (0, 1, 2, ...)
    uint32_t packets_in_slice;      // Packets sent in current slice
    uint32_t next_seq_num;          // Next sequence number to assign
    uint32_t current_path;          // Current path ID
    Time last_switch_time;          // Last time we switched paths

    // RTT measurement
    uint64_t last_tail_time;        // Timestamp of last TAIL packet sent
    Time phase0_tx_time;            // Tx time of first packet in current phase
    Time phase0_rx_time;            // Rx time of first packet (from ACK)

    // Dynamic slice size
    uint32_t current_slice_size;    // Current slice size (in packets)
    uint64_t measured_rtt_diff;     // Measured RTT difference (nanoseconds)

    FlowSliceSenderState()
        : flow_key(0), current_slice(0), packets_in_slice(0),
          next_seq_num(0), current_path(0), last_switch_time(Simulator::Now()),
          last_tail_time(0), phase0_tx_time(0), phase0_rx_time(0),
          current_slice_size(1), measured_rtt_diff(0) {}
};

/**
 * @brief FlowSlice Sender at source ToR
 *
 * Key features:
 * - Dynamically calculates slice size based on measured RTT difference
 * - RTT diff small → larger slices (better utilization)
 * - RTT diff large → smaller slices (less reordering)
 * - Switches to shortest queue path after each slice
 * - Tags packets with slice_id, sequence number, and timestamps
 * - Supports up to 4 paths (typical fat-tree)
 */
class FlowSliceSender {
public:
    FlowSliceSender();
    ~FlowSliceSender();

    /**
     * @brief Process an outgoing packet
     * @param p The packet to process
     * @param flow_key Flow identifier (4-tuple hash)
     * @param path_callback Callback to get best path based on queue sizes
     * @return The selected path ID for this packet
     */
    uint32_t ProcessPacket(Ptr<Packet> p, uint64_t flow_key,
                           Callback<uint32_t> path_callback);

    /**
     * @brief Process RTT feedback from destination
     * @param flow_key Flow identifier
     * @param rtt_diff_ns Measured RTT difference in nanoseconds
     * @param phase0_rx_time Receive time of first packet in current phase
     */
    void ProcessRttFeedback(uint64_t flow_key, uint64_t rtt_diff_ns, Time phase0_rx_time);

    /**
     * @brief Reset flow state (e.g., after flow completes)
     */
    void ResetFlow(uint64_t flow_key);

    /**
     * @brief Get statistics
     */
    uint32_t GetTotalSlices() const { return m_totalSlices; }
    uint32_t GetActiveFlows() const { return m_flowStates.size(); }

    /**
     * @brief Set callback to get queue size for a path
     */
    void SetGetQueueSizeCallback(Callback<uint32_t, uint32_t> callback);

    /**
     * @brief Get queue size for a specific path
     */
    uint32_t GetQueueSize(uint32_t path_id);

private:
    /**
     * @brief Get or create flow state
     */
    FlowSliceSenderState* GetOrCreateFlowState(uint64_t flow_key);

    /**
     * @brief Select path based on shortest local queue
     */
    uint32_t SelectShortestQueuePath();

    /**
     * @brief Switch to new path for next slice
     */
    uint32_t SwitchPath(FlowSliceSenderState* state,
                        Callback<uint32_t> path_callback);

    /**
     * @brief Calculate dynamic slice size based on RTT difference
     * @param rtt_diff_ns RTT difference in nanoseconds
     * @return Slice size in packets
     *
     * Formula:
     * - RTT diff small (< 1μs) → larger slices (8-32 packets)
     * - RTT diff medium (1-5μs) → medium slices (2-8 packets)
     * - RTT diff large (> 5μs) → small slices (1 packet)
     */
    uint32_t CalculateSliceSizeFromRttDiff(uint64_t rtt_diff_ns);

    // Configuration
    uint32_t m_maxPaths;            // Default: 4 paths
    uint32_t m_minSliceTime;        // Minimum time between slice switches (ns)
    uint32_t m_minSlicePackets;     // Minimum slice size (packets)
    uint32_t m_maxSlicePackets;     // Maximum slice size (packets)
    double m_safetyFactor;          // Safety factor for slice size calculation

    // Per-flow state
    std::unordered_map<uint64_t, FlowSliceSenderState*> m_flowStates;

    // Callback for getting queue sizes
    Callback<uint32_t, uint32_t> m_getQueueSizeCallback;

    // Statistics
    uint32_t m_totalSlices;
    uint64_t m_totalPacketsTagged;
};

} // namespace ns3

#endif /* __FLOWSLICE_SENDER_H__ */
