/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * FlowSlice Sender Implementation
 */

#include "ns3/flowslice-sender.h"

#include "ns3/log.h"
#include "ns3/simulator.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("FlowSliceSender");

/***************************************************************
 * FlowSliceTag Implementation
 ***************************************************************/

FlowSliceTag::FlowSliceTag()
    : m_slice_id(0), m_flow_key(0), m_sequence_number(0),
      m_timestamp_tx(0), m_timestamp_tail(0), m_flag(DATA) {}

void FlowSliceTag::SetSliceId(uint64_t slice_id) {
    m_slice_id = slice_id;
}

uint64_t FlowSliceTag::GetSliceId() const {
    return m_slice_id;
}

void FlowSliceTag::SetFlowKey(uint64_t flow_key) {
    m_flow_key = flow_key;
}

uint64_t FlowSliceTag::GetFlowKey() const {
    return m_flow_key;
}

void FlowSliceTag::SetSequenceNumber(uint32_t seq_num) {
    m_sequence_number = seq_num;
}

uint32_t FlowSliceTag::GetSequenceNumber() const {
    return m_sequence_number;
}

void FlowSliceTag::SetTimestampTx(uint64_t timestamp) {
    m_timestamp_tx = timestamp;
}

uint64_t FlowSliceTag::GetTimestampTx() const {
    return m_timestamp_tx;
}

void FlowSliceTag::SetTimestampTail(uint64_t timestamp) {
    m_timestamp_tail = timestamp;
}

uint64_t FlowSliceTag::GetTimestampTail() const {
    return m_timestamp_tail;
}

void FlowSliceTag::SetFlag(uint32_t flag) {
    m_flag = flag;
}

uint32_t FlowSliceTag::GetFlag() const {
    return m_flag;
}

TypeId FlowSliceTag::GetTypeId() {
    static TypeId tid = TypeId("ns3::FlowSliceTag")
                            .SetParent<Tag>()
                            .AddConstructor<FlowSliceTag>();
    return tid;
}

TypeId FlowSliceTag::GetInstanceTypeId() const {
    return GetTypeId();
}

uint32_t FlowSliceTag::GetSerializedSize() const {
    return 36; // 3 x uint64_t + 1 x uint32_t + padding
}

void FlowSliceTag::Serialize(TagBuffer i) const {
    i.WriteU64(m_slice_id);
    i.WriteU64(m_flow_key);
    i.WriteU32(m_sequence_number);
    i.WriteU64(m_timestamp_tx);
    i.WriteU64(m_timestamp_tail);
    i.WriteU32(m_flag);
}

void FlowSliceTag::Deserialize(TagBuffer i) {
    m_slice_id = i.ReadU64();
    m_flow_key = i.ReadU64();
    m_sequence_number = i.ReadU32();
    m_timestamp_tx = i.ReadU64();
    m_timestamp_tail = i.ReadU64();
    m_flag = i.ReadU32();
}

void FlowSliceTag::Print(std::ostream& os) const {
    os << "slice_id=" << m_slice_id << ", flow_key=" << m_flow_key
       << ", seq=" << m_sequence_number << ", tx=" << m_timestamp_tx
       << ", tail=" << m_timestamp_tail << ", flag=" << m_flag;
}

/***************************************************************
 * FlowSliceSender Implementation
 ***************************************************************/

FlowSliceSender::FlowSliceSender()
    : m_maxPaths(4),
      m_minSliceTime(1000),    // 1μs minimum between switches
      m_minSlicePackets(1),    // 1 packet minimum
      m_maxSlicePackets(32),   // 32 packets maximum
      m_safetyFactor(0.8),     // 80% safety factor
      m_totalSlices(0),
      m_totalPacketsTagged(0) {
    NS_LOG_FUNCTION(this);
}

FlowSliceSender::~FlowSliceSender() {
    NS_LOG_FUNCTION(this);
    // Clean up flow states
    for (auto& pair : m_flowStates) {
        delete pair.second;
    }
    m_flowStates.clear();
}

void FlowSliceSender::SetGetQueueSizeCallback(Callback<uint32_t, uint32_t> callback) {
    m_getQueueSizeCallback = callback;
}

uint32_t FlowSliceSender::GetQueueSize(uint32_t path_id) {
    if (!m_getQueueSizeCallback.IsNull()) {
        return m_getQueueSizeCallback(path_id);
    }
    return 0;
}

FlowSliceSenderState* FlowSliceSender::GetOrCreateFlowState(uint64_t flow_key) {
    auto it = m_flowStates.find(flow_key);
    if (it != m_flowStates.end()) {
        return it->second;
    }

    // Create new flow state
    FlowSliceSenderState* state = new FlowSliceSenderState();
    state->flow_key = flow_key;
    state->current_slice = 0;
    state->packets_in_slice = 0;
    state->next_seq_num = 0;
    state->current_path = 0;
    state->last_switch_time = Simulator::Now();
    state->last_tail_time = 0;
    state->phase0_tx_time = Simulator::Now();
    state->phase0_rx_time = Simulator::Now();
    state->current_slice_size = 1;  // Start with 1 packet
    state->measured_rtt_diff = 0;

    m_flowStates[flow_key] = state;

    NS_LOG_INFO("Created new FlowSlice state for flow " << flow_key);

    return state;
}

uint32_t FlowSliceSender::SelectShortestQueuePath() {
    uint32_t best_path = 0;
    uint32_t min_queue = UINT32_MAX;

    for (uint32_t path = 0; path < m_maxPaths; path++) {
        uint32_t queue_size = GetQueueSize(path);

        if (queue_size < min_queue) {
            min_queue = queue_size;
            best_path = path;
        }
    }

    NS_LOG_DEBUG("Selected shortest queue path: " << best_path
                 << " with queue_size=" << min_queue);

    return best_path;
}

uint32_t FlowSliceSender::SwitchPath(FlowSliceSenderState* state,
                                     Callback<uint32_t> path_callback) {
    // Calculate new slice size based on measured RTT difference
    uint32_t new_slice_size = CalculateSliceSizeFromRttDiff(state->measured_rtt_diff);

    // Use callback to get available paths if provided
    uint32_t new_path;

    if (!path_callback.IsNull()) {
        new_path = path_callback();
    } else {
        // Default: select path with shortest local queue
        new_path = SelectShortestQueuePath();
    }

    // Update state
    state->current_slice++;
    state->packets_in_slice = 0;
    state->current_path = new_path;
    state->current_slice_size = new_slice_size;
    state->last_switch_time = Simulator::Now();
    state->phase0_tx_time = Simulator::Now();  // Start new phase

    m_totalSlices++;

    NS_LOG_INFO("Flow " << state->flow_key << " switched to slice "
                 << state->current_slice << ", path=" << new_path
                 << ", slice_size=" << new_slice_size << " packets"
                 << ", rtt_diff=" << state->measured_rtt_diff / 1000.0 << "μs");

    return new_path;
}

uint32_t FlowSliceSender::CalculateSliceSizeFromRttDiff(uint64_t rtt_diff_ns) {
    // Dynamic slice size based on RTT difference
    // RTT diff small → larger slices (better utilization)
    // RTT diff large → smaller slices (less reordering)

    double rtt_diff_us = rtt_diff_ns / 1000.0;  // Convert to microseconds

    uint32_t slice_size;

    if (rtt_diff_us < 0.5) {
        // Very small RTT diff (< 0.5μs): use max slice size
        slice_size = m_maxSlicePackets;
    } else if (rtt_diff_us < 1.0) {
        // Small RTT diff (0.5-1μs): 16-32 packets
        slice_size = 16 + static_cast<uint32_t>((1.0 - rtt_diff_us) * 16);
    } else if (rtt_diff_us < 2.0) {
        // Medium RTT diff (1-2μs): 8-16 packets
        slice_size = 8 + static_cast<uint32_t>((2.0 - rtt_diff_us) * 8);
    } else if (rtt_diff_us < 5.0) {
        // Large RTT diff (2-5μs): 2-8 packets
        slice_size = 2 + static_cast<uint32_t>((5.0 - rtt_diff_us) * 2);
    } else {
        // Very large RTT diff (> 5μs): use min slice size
        slice_size = m_minSlicePackets;
    }

    // Clamp to bounds
    if (slice_size < m_minSlicePackets) slice_size = m_minSlicePackets;
    if (slice_size > m_maxSlicePackets) slice_size = m_maxSlicePackets;

    // Apply safety factor
    slice_size = static_cast<uint32_t>(slice_size * m_safetyFactor);
    if (slice_size < m_minSlicePackets) slice_size = m_minSlicePackets;

    NS_LOG_DEBUG("RTT diff=" << rtt_diff_us << "μs → slice_size=" << slice_size << " packets");

    return slice_size;
}

uint32_t FlowSliceSender::ProcessPacket(Ptr<Packet> p, uint64_t flow_key,
                                        Callback<uint32_t> path_callback) {
    m_totalPacketsTagged++;

    // Get or create flow state
    FlowSliceSenderState* state = GetOrCreateFlowState(flow_key);

    // Generate unique slice_id: (flow_key << 16) | current_slice
    uint64_t slice_id = (flow_key << 16) | state->current_slice;
    uint64_t timestamp_tx = Simulator::Now().GetNanoSeconds();

    // Check if we need to switch to new slice/path
    bool is_tail = false;
    if (state->packets_in_slice >= state->current_slice_size) {
        Time time_since_switch = Simulator::Now() - state->last_switch_time;

        // Only switch if minimum time has passed
        if (time_since_switch.GetNanoSeconds() >= m_minSliceTime) {
            // Mark this packet as TAIL
            is_tail = true;
            state->last_tail_time = timestamp_tx;

            NS_LOG_DEBUG("Flow " << flow_key << " slice " << state->current_slice
                         << " complete: " << state->packets_in_slice << " packets");
            SwitchPath(state, path_callback);
        }
    }

    // Tag packet with slice information
    FlowSliceTag tag;
    tag.SetFlowKey(flow_key);
    tag.SetSliceId(slice_id);
    tag.SetSequenceNumber(state->next_seq_num);
    tag.SetTimestampTx(timestamp_tx);
    tag.SetTimestampTail(state->last_tail_time);
    tag.SetFlag(is_tail ? FlowSliceTag::TAIL : FlowSliceTag::DATA);
    p->AddPacketTag(tag);

    NS_LOG_DEBUG("Tagged packet: flow=" << flow_key
                 << ", slice_id=" << slice_id
                 << ", seq=" << state->next_seq_num
                 << ", path=" << state->current_path
                 << ", flag=" << (is_tail ? "TAIL" : "DATA"));

    // Update state
    state->packets_in_slice++;
    state->next_seq_num++;

    return state->current_path;
}

void FlowSliceSender::ProcessRttFeedback(uint64_t flow_key, uint64_t rtt_diff_ns,
                                         Time phase0_rx_time) {
    auto it = m_flowStates.find(flow_key);
    if (it == m_flowStates.end()) {
        NS_LOG_WARN("Received RTT feedback for unknown flow " << flow_key);
        return;
    }

    FlowSliceSenderState* state = it->second;

    // Update measured RTT difference with EWMA
    if (state->measured_rtt_diff == 0) {
        state->measured_rtt_diff = rtt_diff_ns;
    } else {
        // EWMA: new = 0.875 * old + 0.125 * new
        state->measured_rtt_diff = (state->measured_rtt_diff * 7 + rtt_diff_ns) / 8;
    }

    state->phase0_rx_time = phase0_rx_time;

    // Recalculate slice size for next slice
    uint32_t new_slice_size = CalculateSliceSizeFromRttDiff(state->measured_rtt_diff);
    state->current_slice_size = new_slice_size;

    NS_LOG_INFO("Flow " << flow_key << " RTT feedback: rtt_diff="
                 << rtt_diff_ns / 1000.0 << "μs, ewma="
                 << state->measured_rtt_diff / 1000.0 << "μs"
                 << ", new_slice_size=" << new_slice_size << " packets");
}

void FlowSliceSender::ResetFlow(uint64_t flow_key) {
    auto it = m_flowStates.find(flow_key);
    if (it != m_flowStates.end()) {
        delete it->second;
        m_flowStates.erase(it);

        NS_LOG_INFO("Reset FlowSlice state for flow " << flow_key
                    << ", active_flows=" << m_flowStates.size());
    }
}

} // namespace ns3
