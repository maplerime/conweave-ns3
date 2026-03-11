/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * FlowSlice Reorder Buffer Implementation
 */

#include "ns3/flowslice-reorder.h"

#include "ns3/log.h"
#include "ns3/simulator.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("FlowSliceReorderBuffer");

FlowSliceReorderBuffer::FlowSliceReorderBuffer()
    : m_deliveryNext(0),
      m_overflowMode(false),
      m_totalPacketsProcessed(0),
      m_totalPacketsBuffered(0),
      m_totalPacketsDelivered(0),
      m_overflowTriggers(0) {
    NS_LOG_FUNCTION(this);
}

FlowSliceReorderBuffer::~FlowSliceReorderBuffer() {
    NS_LOG_FUNCTION(this);
    // Clean up subflow queues
    for (auto& pair : m_subflowQueues) {
        delete pair.second;
    }
    m_subflowQueues.clear();
}

void FlowSliceReorderBuffer::Reset() {
    NS_LOG_FUNCTION(this);

    for (auto& pair : m_subflowQueues) {
        pair.second->Clear();
        delete pair.second;
    }
    m_subflowQueues.clear();

    m_deliveryNext = 0;
    m_overflowMode = false;

    // Reset statistics
    m_totalPacketsProcessed = 0;
    m_totalPacketsBuffered = 0;
    m_totalPacketsDelivered = 0;
    m_overflowTriggers = 0;
}

SubflowQueue* FlowSliceReorderBuffer::GetOrCreateSubflowQueue(uint32_t subflow_id, uint32_t seq_num) {
    auto it = m_subflowQueues.find(subflow_id);

    if (it != m_subflowQueues.end()) {
        return it->second;
    }

    // Check if we can create a new queue (max 2 queues)
    if (m_subflowQueues.size() >= MAX_QUEUES) {
        NS_LOG_WARN("Cannot create new queue for subflow " << subflow_id
                    << ": already have " << m_subflowQueues.size() << " queues");
        return nullptr;
    }

    // Create new subflow queue
    SubflowQueue* queue = new SubflowQueue();
    queue->subflow_id = subflow_id;
    queue->next_expected_seq = seq_num;
    queue->size = 0;
    queue->max_size = MAX_QUEUE_SIZE;
    queue->last_arrival_time = Simulator::Now().GetNanoSeconds();
    queue->first_seq = seq_num;
    queue->active = true;

    m_subflowQueues[subflow_id] = queue;

    NS_LOG_INFO("Created new queue for subflow " << subflow_id
                << ", first_seq=" << seq_num);

    return queue;
}

bool FlowSliceReorderBuffer::ProcessPacket(
    Ptr<Packet> p,
    uint32_t subflow_id,
    uint32_t seq_num,
    Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback) {

    m_totalPacketsProcessed++;

    NS_LOG_DEBUG("Processing packet: subflow=" << subflow_id
                 << ", seq=" << seq_num
                 << ", delivery_next=" << m_deliveryNext);

    // Get or create subflow queue
    SubflowQueue* queue = GetOrCreateSubflowQueue(subflow_id, seq_num);

    // Check if we need to flush this queue (queue is full or cannot create new queue)
    if (queue == nullptr || queue->IsFull()) {
        NS_LOG_WARN("Queue issue: queue=" << (queue ? queue->subflow_id : -1)
                    << " full=" << (queue ? queue->IsFull() : false)
                    << ", n_queues=" << m_subflowQueues.size());

        // If queue exists, flush only this queue (but allow future packets to be buffered)
        if (queue != nullptr) {
            NS_LOG_WARN("Flushing queue " << queue->subflow_id << " (full), will rebuffer future packets");
            FlushQueueButAllowRebuffer(queue->subflow_id, deliver_callback);
        } else {
            // Cannot create new queue (max queues reached), deliver immediately without buffering
            NS_LOG_WARN("Max queues reached, delivering packet immediately");
        }

        // Deliver this packet immediately
        CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
        p->PeekHeader(ch);
        deliver_callback(p, ch);
        m_totalPacketsDelivered++;
        return false;
    }

    // Update RTT estimation for this subflow
    uint64_t now = Simulator::Now().GetNanoSeconds();
    if (queue->last_arrival_time > 0) {
        uint64_t gap = now - queue->last_arrival_time;
        // Simple EWMA update for RTT
        if (m_subflowRTT.find(subflow_id) == m_subflowRTT.end()) {
            m_subflowRTT[subflow_id] = gap;
        } else {
            // Exponential weighted moving average: RTT = 0.875 * RTT + 0.125 * gap
            m_subflowRTT[subflow_id] = (m_subflowRTT[subflow_id] * 7 + gap) / 8;
        }
    }
    queue->last_arrival_time = now;

    // Check if this is the next expected packet globally
    if (seq_num == m_deliveryNext) {
        NS_LOG_DEBUG("Packet seq=" << seq_num << " matches delivery_next, delivering immediately");
        // Deliver immediately without buffering
        CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
        p->PeekHeader(ch);
        deliver_callback(p, ch);
        m_totalPacketsDelivered++;
        m_deliveryNext++;

        // Update subflow's expected sequence
        queue->next_expected_seq = seq_num + 1;

        // Try to deliver any buffered packets from any queue
        TryDeliverBuffered(deliver_callback);

        return false;  // Not buffered
    }

    // Packet is out of order - check if we should buffer it
    // Only buffer if seq_num > m_deliveryNext (future packet)
    if (seq_num > m_deliveryNext) {
        NS_LOG_DEBUG("Out-of-order packet seq=" << seq_num << " > delivery_next=" << m_deliveryNext << ", buffering");

        // Buffer this packet
        queue->buffer.push({seq_num, p});
        queue->size++;
        m_totalPacketsBuffered++;
        queue->next_expected_seq = seq_num + 1;

        NS_LOG_INFO("Buffered OoO packet for subflow " << subflow_id
                    << ", seq=" << seq_num
                    << ", queue_size=" << queue->size
                    << "/" << queue->max_size);

        return true;  // Buffered
    }

    // seq_num < m_deliveryNext: late packet, deliver immediately
    NS_LOG_DEBUG("Late packet seq=" << seq_num << " < delivery_next=" << m_deliveryNext << ", delivering immediately");
    CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
    p->PeekHeader(ch);
    deliver_callback(p, ch);
    m_totalPacketsDelivered++;

    return false;  // Not buffered
}

void FlowSliceReorderBuffer::TryDeliverBuffered(
    Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback) {

    // Try to find and deliver packets that match m_deliveryNext
    bool found = true;

    while (found) {
        found = false;

        for (auto& pair : m_subflowQueues) {
            SubflowQueue* queue = pair.second;

            if (queue->IsEmpty()) {
                continue;
            }

            // Check if the front of this queue matches our expected sequence
            // Buffer stores (seq_num, packet) pairs
            const auto& front_pair = queue->buffer.front();
            uint32_t buffered_seq = front_pair.first;

            if (buffered_seq == m_deliveryNext) {
                // Found the next expected packet - deliver it
                Ptr<Packet> pkt = front_pair.second;
                queue->buffer.pop();
                queue->size--;

                CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
                pkt->PeekHeader(ch);
                deliver_callback(pkt, ch);
                m_totalPacketsDelivered++;

                NS_LOG_DEBUG("Delivered buffered packet seq=" << buffered_seq
                             << " from subflow " << queue->subflow_id
                             << ", queue_size=" << queue->size);

                m_deliveryNext++;
                queue->next_expected_seq = m_deliveryNext;

                found = true;
                break;  // Restart search from beginning
            }
        }
    }
}

void FlowSliceReorderBuffer::EnterOverflowMode(
    Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback) {

    NS_LOG_WARN("ENTERING OVERFLOW MODE - delivering all buffered packets immediately");

    m_overflowMode = true;
    m_overflowTriggers++;

    // Deliver all buffered packets from all queues
    for (auto& pair : m_subflowQueues) {
        SubflowQueue* queue = pair.second;

        while (!queue->buffer.empty()) {
            Ptr<Packet> pkt = queue->buffer.front().second;
            queue->buffer.pop();
            queue->size--;

            CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
            pkt->PeekHeader(ch);
            deliver_callback(pkt, ch);
            m_totalPacketsDelivered++;
        }
        queue->overflow = true;  // Mark all queues as overflow
    }

    NS_LOG_INFO("Overflow mode: delivered all buffered packets, total_delivered="
                << m_totalPacketsDelivered);
}

void FlowSliceReorderBuffer::FlushQueue(
    uint32_t queue_id,
    Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback) {

    auto it = m_subflowQueues.find(queue_id);
    if (it == m_subflowQueues.end()) {
        NS_LOG_WARN("FlushQueue: queue " << queue_id << " not found");
        return;
    }

    SubflowQueue* queue = it->second;

    NS_LOG_WARN("Flushing queue " << queue_id << " (subflow " << queue->subflow_id
                << ") with " << queue->size << " packets");

    // Mark this queue as overflow mode (future packets for this subflow bypass buffering)
    queue->overflow = true;

    // Deliver all buffered packets from this queue only
    while (!queue->buffer.empty()) {
        Ptr<Packet> pkt = queue->buffer.front().second;
        queue->buffer.pop();
        queue->size--;

        CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
        pkt->PeekHeader(ch);
        deliver_callback(pkt, ch);
        m_totalPacketsDelivered++;
    }

    NS_LOG_INFO("Flushed queue " << queue_id << " (subflow " << queue->subflow_id
                << "), total_delivered=" << m_totalPacketsDelivered);
}

void FlowSliceReorderBuffer::FlushQueueButAllowRebuffer(
    uint32_t queue_id,
    Callback<void, Ptr<Packet>, CustomHeader&> deliver_callback) {

    auto it = m_subflowQueues.find(queue_id);
    if (it == m_subflowQueues.end()) {
        NS_LOG_WARN("FlushQueueButAllowRebuffer: queue " << queue_id << " not found");
        return;
    }

    SubflowQueue* queue = it->second;

    NS_LOG_WARN("Flushing queue " << queue_id << " (subflow " << queue->subflow_id
                << ") with " << queue->size << " packets, allowing rebuffer");

    // Do NOT set overflow mode - future packets can be buffered again
    // queue->overflow = true;  <-- REMOVED

    // Deliver all buffered packets from this queue only
    while (!queue->buffer.empty()) {
        Ptr<Packet> pkt = queue->buffer.front().second;
        queue->buffer.pop();
        queue->size--;

        CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
        pkt->PeekHeader(ch);
        deliver_callback(pkt, ch);
        m_totalPacketsDelivered++;
    }

    NS_LOG_INFO("Flushed queue " << queue_id << " (subflow " << queue->subflow_id
                << "), future packets will be rebuffered");
}

uint32_t FlowSliceReorderBuffer::GetTotalBufferedPackets() const {
    uint32_t total = 0;
    for (const auto& pair : m_subflowQueues) {
        total += pair.second->size;
    }
    return total;
}

uint32_t FlowSliceReorderBuffer::GetNumActiveSubflows() const {
    uint32_t count = 0;
    for (const auto& pair : m_subflowQueues) {
        if (pair.second->active) {
            count++;
        }
    }
    return count;
}

uint64_t FlowSliceReorderBuffer::GetEstimatedRTT(uint32_t subflow_id) const {
    auto it = m_subflowRTT.find(subflow_id);
    if (it != m_subflowRTT.end()) {
        return it->second;
    }
    return 0;
}

} // namespace ns3
