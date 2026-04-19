#include "ns3/settings.h"

namespace ns3 {
/* helper function */
Ipv4Address Settings::node_id_to_ip(uint32_t id) {
    return Ipv4Address(0x0b000001 + ((id / 256) * 0x00010000) + ((id % 256) * 0x00000100));
}
uint32_t Settings::ip_to_node_id(Ipv4Address ip) {
    return (ip.Get() >> 8) & 0xffff;
}

/* others */
uint32_t Settings::lb_mode = 0;

std::map<uint32_t, uint32_t> Settings::hostIp2IdMap;
std::map<uint32_t, uint32_t> Settings::hostId2IpMap;

/* statistics */
uint32_t Settings::node_num = 0;
uint32_t Settings::host_num = 0;
uint32_t Settings::switch_num = 0;
uint64_t Settings::cnt_finished_flows = 0;
uint32_t Settings::packet_payload = 1000;

uint32_t Settings::dropped_pkt_sw_ingress = 0;
uint32_t Settings::dropped_pkt_sw_egress = 0;

/* Tag routing statistics */
uint64_t Settings::tag1_ecmp_count = 0;
uint64_t Settings::tag2_inflex_count = 0;
uint64_t Settings::tag2_drill_count = 0;
uint64_t Settings::tag2_compare_count = 0;
uint64_t Settings::tag2_adaptive_spray_count = 0;
uint64_t Settings::tag2_random_spray_count = 0;

/* Reorder buffer settings for mode 16 */
uint32_t Settings::reorder_queue_num = 4;  // Default: 4 reorder queues

/* for load balancer */
std::map<uint32_t, uint32_t> Settings::hostIp2SwitchId;

}  // namespace ns3
