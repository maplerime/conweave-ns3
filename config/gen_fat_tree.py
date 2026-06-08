#!/usr/bin/env python3

def generate_fat_tree(k, num_hosts):
    num_core = (k // 2) * (k // 2)
    num_pod_switches = k * k
    num_switches = num_core + num_pod_switches
    total_nodes = num_hosts + num_switches
    
    # 计算链路数
    host_links = num_hosts
    edge_agg_links = (k // 2) * (k // 2) * k  # 每个pod内 (k/2)*(k/2)，共k个pod
    agg_core_links = (k // 2) * num_core * k  # 每个pod (k/2)个agg，连接num_core个core，共k个pod
    total_links = host_links + edge_agg_links + agg_core_links
    
    core_start = num_hosts
    pod_switch_start = num_hosts + num_core
    
    # Line 1: total_nodes num_switches num_links
    print(f"{total_nodes} {num_switches} {total_links}")
    
    # Line 2: core switches
    core_switches = [str(i) for i in range(core_start, core_start + num_core)]
    print(" ".join(core_switches))
    
    # Connect hosts to edge switches
    hosts_per_edge = k // 2
    hosts_per_pod = hosts_per_edge * hosts_per_edge
    edge_start = pod_switch_start
    
    for pod in range(k):
        for edge in range(k // 2):
            edge_switch = pod_switch_start + pod * k + edge
            for h in range(hosts_per_edge):
                host_id = pod * hosts_per_pod + edge * hosts_per_edge + h
                if host_id < num_hosts:
                    print(f"{host_id} {edge_switch} 100Gbps 1000ns 0.000000")
    
    # Edge to Agg within pod
    for pod in range(k):
        pod_start = pod_switch_start + pod * k
        for edge in range(k // 2):
            edge_switch = pod_start + edge
            for agg in range(k // 2):
                agg_switch = pod_start + (k // 2) + agg
                print(f"{edge_switch} {agg_switch} 100Gbps 1000ns 0.000000")
    
    # Agg to Core
    for pod in range(k):
        pod_start = pod_switch_start + pod * k
        for agg in range(k // 2):
            agg_switch = pod_start + (k // 2) + agg
            for c in range(k // 2):
                core_idx = agg * (k // 2) + c
                if core_idx < num_core:
                    print(f"{agg_switch} {core_start + core_idx} 100Gbps 1000ns 0.000000")

# Generate k=8 with 320 hosts
generate_fat_tree(k=8, num_hosts=320)
