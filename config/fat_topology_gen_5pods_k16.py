#!/usr/bin/env python3
"""
Generate Fat-tree topology for custom configuration (k=16):
- 5 pods out of k=16 fat-tree
- 256 hosts per pod (8 ToR × 32 hosts)
- Total 1280 hosts
- Host-to-Tor: round-robin within each pod
"""

k_fat = 16
pods_used = 5  # Use only 5 out of 16 pods
hosts_per_pod = 256
tors_per_pod = 8  # ToR switches per pod (k/2)
hosts_per_tor = 32  # hosts per ToR (256 / 8)
host_link_rate = 100  # Gbps
switch_link_rate = 400  # Gbps
link_latency = 1000  # ns

assert(k_fat % 2 == 0)
print("Fat K : {}".format(k_fat))
print("Pods used: {} (out of {})".format(pods_used, k_fat))

n_core = int(k_fat / 2 * k_fat / 2)  # 64 cores
n_agg_per_pod = int(k_fat / 2)  # 8 agg per pod
n_tor_per_pod = tors_per_pod  # 8 tor per pod
n_agg_total = n_agg_per_pod * pods_used
n_tor_total = n_tor_per_pod * pods_used
n_server_total = hosts_per_pod * pods_used  # 1280
n_core_total = n_core  # 64 cores

print("\nTopology Configuration:")
print("Number of Core: {}".format(n_core))
print("Number of Aggregation: {} (per pod: {}, total: {})".format(
    n_agg_total, n_agg_per_pod, n_agg_per_pod * k_fat))
print("Number of ToR: {} (per pod: {})".format(n_tor_total, n_tor_per_pod))
print("Number of Servers: {} (per pod: {})".format(n_server_total, hosts_per_pod))
print("Hosts per ToR: {}".format(hosts_per_tor))
print("Connection pattern: round-robin (host→Tor: 1→1, 2→2, ..., 8→8, 9→1, ...)")

# Node ID assignments
i_server = 0
i_tor = n_server_total
i_agg = n_server_total + n_tor_total
i_core = n_server_total + n_tor_total + n_agg_total

# Generate topology filename
filename = "fat_k16_5pods_256perPod_100G_400G_OS1.txt"

num_link = 0
with open(filename, "w") as f:

    # Server to ToR links (host links) - round-robin within each pod
    for pod_id in range(pods_used):
        for tor_id in range(n_tor_per_pod):
            tor_global_id = i_tor + pod_id * n_tor_per_pod + tor_id
            for h in range(hosts_per_tor):
                server_global_id = i_server + pod_id * hosts_per_pod + tor_id * hosts_per_tor + h
                # Round-robin: host h connects to tor (h % 8)
                target_tor_global_id = i_tor + pod_id * n_tor_per_pod + (h % n_tor_per_pod)
                f.write("{} {} {}Gbps {}ns 0.000000\n".format(
                    server_global_id, target_tor_global_id, host_link_rate, link_latency))
                num_link += 1

    # ToR to Aggregator links (switch-to-switch)
    for pod_id in range(pods_used):
        for tor_id in range(n_tor_per_pod):
            tor_global_id = i_tor + pod_id * n_tor_per_pod + tor_id
            for agg_id in range(n_agg_per_pod):
                agg_global_id = i_agg + pod_id * n_agg_per_pod + agg_id
                f.write("{} {} {}Gbps {}ns 0.000000\n".format(
                    tor_global_id, agg_global_id, switch_link_rate, link_latency))
                num_link += 1

    # Aggregator to Core links (switch-to-switch)
    # In Fat-tree, each agg connects to k/2 core switches
    # For k=16: agg (pod p, agg a) connects to cores: a*8 to a*8+7
    for pod_id in range(pods_used):
        for agg_id in range(n_agg_per_pod):
            agg_global_id = i_agg + pod_id * n_agg_per_pod + agg_id
            # Each agg connects to k/2 consecutive core switches
            # Core group for this agg: [agg_id * (k/2), agg_id * (k/2) + (k/2) - 1]
            for c in range(n_agg_per_pod):
                core_global_id = i_core + agg_id * n_agg_per_pod + c
                f.write("{} {} {}Gbps {}ns 0.000000\n".format(
                    agg_global_id, core_global_id, switch_link_rate, link_latency))
                num_link += 1

def line_prepender(filename, line):
    with open(filename, "r+") as f:
        content = f.read()
        f.seek(0, 0)
        f.write(line.rstrip('\r\n') + '\n' + content)

num_total_node = n_server_total + n_tor_total + n_agg_total + n_core_total
num_total_switch = n_tor_total + n_agg_total + n_core_total
id_switch_all = ""
# second line: switch node IDs
for i in range(num_total_switch):
    if i == num_total_switch - 1:
        id_switch_all += "{}\n".format(i + n_server_total)
    else:
        id_switch_all += "{} ".format(i + n_server_total)
line_prepender(filename, id_switch_all)

# first line: total node #, switch node #, link #
line_prepender(filename, "{} {} {}".format(num_total_node, num_total_switch, num_link))


print("\nTopology Summary:")
print("Total nodes: {}".format(num_total_node))
print("Total switches: {}".format(num_total_switch))
print("  - Servers: {}".format(n_server_total))
print("  - ToR switches: {}".format(n_tor_total))
print("  - Aggregation switches: {}".format(n_agg_total))
print("  - Core switches: {}".format(n_core_total))
print("Total links: {}".format(num_link))
print("\nOutput file: {}".format(filename))
