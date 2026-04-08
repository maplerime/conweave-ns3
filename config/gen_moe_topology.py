#!/usr/bin/env python3
"""
Generate custom MoE topology:
- 1280 hosts, 5 pods, 256 hosts per pod
- Each pod: 4 groups, each group has 8 agg + 8 tor (8x8 full mesh)
- Core: 4 planes, each plane has 8x8=64 core switches
- Agg-Core: each pod group connects to one core plane
- Host-Tor: striped connection (host i connects to tor (i%8)*32 within pod)
"""

# Configuration
NUM_PODS = 5
HOSTS_PER_POD = 256
NUM_GROUPS_PER_POD = 4
AGG_PER_GROUP = 8
TOR_PER_GROUP = 8
NUM_CORE_PLANES = 4
CORE_PER_PLANE = 64  # 8x8
HOST_LINK_RATE = 400  # Gbps
SWITCH_LINK_RATE = 400  # Gbps
HOST_TOR_LATENCY = 10  # ns
TOR_AGG_LATENCY = 10  # ns
AGG_CORE_LATENCY = 300  # ns

# Calculate totals
num_hosts = NUM_PODS * HOSTS_PER_POD  # 1280
num_tor = NUM_PODS * NUM_GROUPS_PER_POD * TOR_PER_GROUP  # 5 * 4 * 8 = 160
num_agg = NUM_PODS * NUM_GROUPS_PER_POD * AGG_PER_GROUP  # 5 * 4 * 8 = 160
num_core = NUM_CORE_PLANES * CORE_PER_PLANE  # 4 * 64 = 256
num_switches = num_tor + num_agg + num_core  # 576
num_nodes = num_hosts + num_switches  # 1856

# Node ID assignments
id_host_start = 0
id_tor_start = num_hosts  # 1280
id_agg_start = id_tor_start + num_tor  # 1440
id_core_start = id_agg_start + num_agg  # 1600

print(f"Topology Configuration:")
print(f"  Hosts: {num_hosts}")
print(f"  ToR switches: {num_tor}")
print(f"  Agg switches: {num_agg}")
print(f"  Core switches: {num_core}")
print(f"  Total switches: {num_switches}")
print(f"  Total nodes: {num_nodes}")

# Generate topology
filename = "topo_1280_400G_400G_OS1.txt"
links = []

# 1. Host to Tor links (striped connection)
print("\nGenerating Host-Tor links...")
for pod in range(NUM_PODS):
    pod_host_start = pod * HOSTS_PER_POD
    pod_tor_start = id_tor_start + pod * NUM_GROUPS_PER_POD * TOR_PER_GROUP

    for h in range(HOSTS_PER_POD):
        host_id = pod_host_start + h
        # Striping: within each group of 64 hosts (4 groups of 16),
        # hosts connect to different ToRs in a striped pattern
        # Group of 4 hosts: h%4 determines which tor group (0,1,2,3)
        # Within tor group: (h/4)%8 determines which tor

        group_of_4 = h % 4  # 0,1,2,3 - selects tor group
        within_group = (h // 4) % 8  # 0-7 - selects tor within group

        tor_id = pod_tor_start + group_of_4 * 8 + within_group
        links.append((host_id, tor_id, HOST_LINK_RATE, HOST_TOR_LATENCY))

print(f"  Host-Tor links: {len(links)}")

# 2. Tor to Agg links (8x8 full mesh within each group)
print("\nGenerating Tor-Agg links (full mesh)...")
for pod in range(NUM_PODS):
    pod_tor_start = id_tor_start + pod * NUM_GROUPS_PER_POD * TOR_PER_GROUP
    pod_agg_start = id_agg_start + pod * NUM_GROUPS_PER_POD * AGG_PER_GROUP

    for g in range(NUM_GROUPS_PER_POD):
        group_tor_start = pod_tor_start + g * TOR_PER_GROUP
        group_agg_start = pod_agg_start + g * AGG_PER_GROUP

        # Full mesh: each tor connects to all 8 agg
        for t in range(TOR_PER_GROUP):
            for a in range(AGG_PER_GROUP):
                tor_id = group_tor_start + t
                agg_id = group_agg_start + a
                links.append((tor_id, agg_id, SWITCH_LINK_RATE, TOR_AGG_LATENCY))

print(f"  Tor-Agg links: {len(links) - 1280}")

# 3. Agg to Core links
print("\nGenerating Agg-Core links...")
for pod in range(NUM_PODS):
    pod_agg_start = id_agg_start + pod * NUM_GROUPS_PER_POD * AGG_PER_GROUP

    for g in range(NUM_GROUPS_PER_POD):
        group_agg_start = pod_agg_start + g * AGG_PER_GROUP
        core_plane = g  # group 0 -> plane 0, group 1 -> plane 1, etc.

        for a in range(AGG_PER_GROUP):
            agg_id = group_agg_start + a
            # Each agg connects to 8 cores in its plane
            # agg 0 -> core 0-7, agg 1 -> core 8-15, etc.
            core_start = id_core_start + core_plane * CORE_PER_PLANE + a * 8
            for c in range(8):
                core_id = core_start + c
                links.append((agg_id, core_id, SWITCH_LINK_RATE, AGG_CORE_LATENCY))

print(f"  Agg-Core links: {len(links) - 1280 - 1280}")

# Write topology file
print(f"\nWriting topology to {filename}...")
with open(filename, "w") as f:
    # Link lines
    for src, dst, bw, lat in links:
        f.write(f"{src} {dst} {bw}Gbps {lat}ns 0.0\n")

    # Switch IDs line
    switch_ids = [str(i) for i in range(id_tor_start, num_nodes)]
    f.write(" ".join(switch_ids) + "\n")

    # Header line: total_nodes total_switches total_links
    f.write(f"{num_nodes} {num_switches} {len(links)}\n")

# The file was written backwards, need to reverse it
print("Reversing file...")
with open(filename, "r") as f:
    lines = f.readlines()

with open(filename, "w") as f:
    for line in reversed(lines):
        f.write(line)

print("\nTopology Summary:")
print(f"  Total nodes: {num_nodes}")
print(f"  Total switches: {num_switches}")
print(f"  Total links: {len(links)}")
print(f"  Output file: {filename}")
print("\nNode ID ranges:")
print(f"  Hosts:        0 - {num_hosts-1} ({num_hosts})")
print(f"  ToR:         {id_tor_start} - {id_agg_start-1} ({num_tor})")
print(f"  Agg:         {id_agg_start} - {id_core_start-1} ({num_agg})")
print(f"  Core:        {id_core_start} - {num_nodes-1} ({num_core})")
