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

/* hybrid mode */
uint32_t Settings::lb_hybrid_enabled = 0;
uint32_t Settings::lb_hybrid_threshold = 100000;  // 100KB default threshold
double Settings::lb_hybrid_ratio = 0.5;           // 50% use DRILL by default

/* flowslice mode */
uint32_t Settings::flowSlice_min_slice = 1;      // 1 packet minimum
uint32_t Settings::flowSlice_max_slice = 32;     // 32 packets maximum
double Settings::flowSlice_safety_factor = 0.8;  // 80% safety factor

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

/* for load balancer */
std::map<uint32_t, uint32_t> Settings::hostIp2SwitchId;

}  // namespace ns3
