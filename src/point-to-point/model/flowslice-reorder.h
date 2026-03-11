/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * FlowSlice Reorder Buffer
 *
 * Dynamic flow slicing based on RTT with bounded reordering buffer
 * - Max 2 queues at destination ToR
 * - Max 32 packets per queue
 * - When any queue is full, enter overflow mode (deliver all packets immediately)
 */

#ifndef __FLOWSLICE_REORDER_H__
#define __FLOWSLICE_REORDER_H__

#include <map>
#include <queue>
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
 * @brief Per-subflow reorder queue
 */
struct SubflowQueue {
    uint32_t subflow_id;           // Subflow identifier (0, 1, 2, ...)
    uint32_t next_expected_seq;    // Next expected sequence number for this subflow
    std::queue<std::pair<uint32_t, Ptr<Packet>>> buffer; // (seq_num, packet) pairs
    uint32_t size;                 // Current buffer size
    uint32_t max_size;             // Maximum buffer size (32 packets)
    uint64_t last_arrival_time;    // Last packet arrival time (for RTT estimation)
    uint32_t first_seq;            // First sequence number seen in this subflow
    bool active;                   // Whether this subflow is active
    bool overflow;                 // Whether this queue is in overflow mode (bypass buffering)

    SubflowQueue() : subflow_id(0), next_expected_seq(0), size(0),
                     max_size(32), last_arrival_time(0), first_seq(0), active(false), overflow(false) {}

    void Clear() {
        while (!buffer.empty()) {
            buffer.pop();
        }
        size = 0;
        next_expected_seq = first_seq;
        overflow = false;  // Reset overflow mode
    }

    bool IsFull() const {
        return size >= max_size;
    }

    bool IsEmpty() const {
        return buffer.empty();
    }
};

/**
 * @brief FlowSlice Reorder Buffer for destination ToR
 *
 * Key features:
 * - Manages up to 2 subflow queues
 * - Each queue max 32 packets
 * - Overflow mode: when any queue is full, deliver all packets immediately
 * - RTT-based dynamic flow slicing
 */
class FlowSliceReorderBuffer {
public:
    FlowSliceReorderBuffer();
    ~FlowSliceReorderBuffer();

    /**
     * @brief Process an incoming packet
     * @param p The packet to process
     * @param subflow_id The subflow identifier (0, 1, 2, ...)
     * @param seq_num The sequence number of this packet
     * @param deliver_callback Callback to deliver packet to destination
     * @return true if packet was buffered, false if delivered immediately
     */
    bool ProcessPacket(Ptr<Packet> p, uint32_t subflow_id, uint32_t seq_num,
                       Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback);

    /**
     * @brief Get statistics
     */
    uint32_t GetTotalBufferedPackets() const;
    uint32_t GetNumActiveSubflows() const;
    uint64_t GetEstimatedRTT(uint32_t subflow_id) const;

    /**
     * @brief Check if in overflow mode
     */
    bool IsOverflowMode() const { return m_overflowMode; }

    /**
     * @brief Reset buffer state
     */
    void Reset();

private:
    /**
     * @brief Try to deliver buffered packets in order
     */
    void TryDeliverBuffered(Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback);

    /**
     * @brief Find or create a subflow queue
     */
    SubflowQueue* GetOrCreateSubflowQueue(uint32_t subflow_id, uint32_t seq_num);

    /**
     * @brief Enter overflow mode - deliver all buffered packets from all queues
     * @deprecated Use FlushQueue instead for per-queue flush
     */
    void EnterOverflowMode(Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback);

    /**
     * @brief Flush only the specified queue (deliver all buffered packets from this queue)
     * @param queue_id The subflow queue to flush
     * @param deliver_callback Callback to deliver packet to destination
     */
    void FlushQueue(uint32_t queue_id, Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback);

    /**
     * @brief Flush the specified queue but allow future packets to be buffered again
     * @param queue_id The subflow queue to flush
     * @param deliver_callback Callback to deliver packet to destination
     */
    void FlushQueueButAllowRebuffer(uint32_t queue_id, Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback);

    static const uint32_t MAX_QUEUES = 2;     // Max 2 subflows
    static const uint32_t MAX_QUEUE_SIZE = 32;

    // Subflow queues (max 2)
    std::unordered_map<uint32_t, SubflowQueue*> m_subflowQueues;

    // Global delivery pointer
    uint32_t m_deliveryNext;

    // Overflow mode flag
    bool m_overflowMode;

    // Statistics
    uint64_t m_totalPacketsProcessed;
    uint64_t m_totalPacketsBuffered;
    uint64_t m_totalPacketsDelivered;
    uint64_t m_overflowTriggers;

    // RTT estimation
    std::map<uint32_t, uint64_t> m_subflowRTT;  // subflow_id -> RTT in nanoseconds
};

} // namespace ns3

#endif /* __FLOWSLICE_REORDER_H__ */
