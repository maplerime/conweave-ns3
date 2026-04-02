#!/usr/bin/env python3
"""
Generate topology visualization from NS-3 fat-tree topology file.
Usage: python plot_topology.py <topology_file> [output_png]
"""

import sys
import graphviz
from collections import defaultdict
import subprocess
import os

def parse_topology(filename):
    """Parse NS-3 topology file and extract nodes and links."""
    with open(filename, 'r') as f:
        lines = f.readlines()

    # First line: total_nodes switch_num link_num
    first_line = lines[0].strip().split()
    total_nodes = int(first_line[0])
    switch_num = int(first_line[1])
    link_num = int(first_line[2])

    # Second line: switch IDs
    switch_line = lines[1].strip().split()
    switch_ids = [int(sid) for sid in switch_line]

    # Remaining lines: links (src dst rate latency)
    links = []
    for line in lines[2:]:
        if line.strip():
            parts = line.strip().split()
            src = int(parts[0])
            dst = int(parts[1])
            rate = parts[2]
            links.append((src, dst, rate))

    # Classify nodes
    host_ids = []
    tor_ids = []
    agg_ids = []
    core_ids = []

    # Determine node ranges based on topology structure
    min_switch = min(switch_ids)
    max_switch = max(switch_ids)

    # For fat-tree topology:
    # Hosts: 0 to (min_switch - 1)
    # Switches: min_switch to max_switch
    num_hosts = min_switch

    if num_hosts == 1280:  # 1280 hosts topology
        if switch_num == 56:  # k8 5pods: 20 ToR, 20 Agg, 16 Core
            tor_ids = switch_ids[0:20]
            agg_ids = switch_ids[20:40]
            core_ids = switch_ids[40:56]
        elif switch_num == 144:  # k16 5pods: 40 ToR, 40 Agg, 64 Core
            tor_ids = switch_ids[0:40]
            agg_ids = switch_ids[40:80]
            core_ids = switch_ids[80:144]
        elif switch_num == 80:  # k8 full: 32 ToR, 32 Agg, 16 Core
            tor_ids = switch_ids[0:32]
            agg_ids = switch_ids[32:64]
            core_ids = switch_ids[64:80]
        else:  # Unknown, try to infer
            third = switch_num // 3
            tor_ids = switch_ids[0:third]
            agg_ids = switch_ids[third:2*third]
            core_ids = switch_ids[2*third:]
    else:
        # Generic classification - assume sequential blocks
        third = switch_num // 3
        tor_ids = switch_ids[0:third]
        agg_ids = switch_ids[third:2*third]
        core_ids = switch_ids[2*third:]

    host_ids = list(range(num_hosts))

    return {
        'hosts': host_ids,
        'tors': tor_ids,
        'aggs': agg_ids,
        'cores': core_ids,
        'links': links
    }


def generate_topology_graph(topo_data, output_file='topology'):
    """Generate graphviz graph and render to PNG."""

    dot = graphviz.Digraph(comment='Fat-tree Topology')
    # Use TB (top-to-bottom) layout: Core on top, then Agg, then ToR, then Hosts
    dot.attr(rankdir='TB', splines='ortho', nodesep='0.2', ranksep='0.5')
    dot.attr(fontsize='10')

    # Track which hosts we actually create (only first pod + ellipsis)
    created_hosts = set()

    # Create subgraphs for better layout
    with dot.subgraph(name='cluster_cores') as c:
        c.attr(label='Core Layer', style='filled', color='lightblue', rank='top')
        c.attr(node_shape='box', node_style='rounded,filled', node_color='lightblue', node_margin='0')
        for core_id in topo_data['cores']:
            c.node(f'C{core_id}', label=f'Core\\n{core_id}')

    # Group Agg switches by pod (if possible)
    num_agg = len(topo_data['aggs'])
    num_tor = len(topo_data['tors'])

    # Try to infer pod structure
    num_pods = 0
    agg_per_pod = 0
    tor_per_pod = 0

    # Common configurations
    if num_agg == 20 and num_tor == 20:  # k8 5pods
        num_pods = 5
        agg_per_pod = 4
        tor_per_pod = 4
    elif num_agg == 40 and num_tor == 40:  # k16 5pods
        num_pods = 5
        agg_per_pod = 8
        tor_per_pod = 8
    elif num_agg == 32 and num_tor == 32:  # k8 full
        num_pods = 8
        agg_per_pod = 4
        tor_per_pod = 4
    else:
        # Assume equal distribution
        num_pods = 8
        agg_per_pod = num_agg // 8 if num_agg >= 8 else 1
        tor_per_pod = num_tor // 8 if num_tor >= 8 else 1

    hosts_per_pod = len(topo_data['hosts']) // num_pods

    # Create pods with Agg and ToR switches
    for pod_id in range(num_pods):
        pod_name = f'cluster_pod{pod_id}'

        with dot.subgraph(name=pod_name) as p:
            p.attr(label=f'Pod {pod_id}', style='rounded')

            # Aggregation switches
            agg_start = pod_id * agg_per_pod
            agg_end = min(agg_start + agg_per_pod, len(topo_data['aggs']))

            with p.subgraph(name=f'cluster_pod{pod_id}_agg') as agg_sub:
                agg_sub.attr(label='Aggregation Layer', style='filled', color='lightgreen')
                agg_sub.attr(node_shape='box', node_style='rounded,filled', node_color='lightgreen')
                for idx in range(agg_start, agg_end):
                    if idx < len(topo_data['aggs']):
                        agg_id = topo_data['aggs'][idx]
                        agg_sub.node(f'A{agg_id}', label=f'Agg\\n{agg_id}')

            # ToR switches
            tor_start = pod_id * tor_per_pod
            tor_end = min(tor_start + tor_per_pod, len(topo_data['tors']))

            with p.subgraph(name=f'cluster_pod{pod_id}_tor') as tor_sub:
                tor_sub.attr(label='ToR Layer', style='filled', color='lightyellow')
                tor_sub.attr(node_shape='box', node_style='rounded,filled', node_color='lightyellow')
                for idx in range(tor_start, tor_end):
                    if idx < len(topo_data['tors']):
                        tor_id = topo_data['tors'][idx]
                        tor_sub.node(f'T{tor_id}', label=f'ToR\\n{tor_id}')

            # Hosts for this pod - only show first pod in detail
            host_start = pod_id * hosts_per_pod
            host_end = min(host_start + hosts_per_pod, len(topo_data['hosts']))

            with p.subgraph(name=f'cluster_pod{pod_id}_hosts') as h_sub:
                if pod_id == 0:
                    # First pod: show all hosts
                    h_sub.attr(label='Hosts', style='invis')
                    h_sub.attr(node_shape='circle', node_style='filled', node_color='white')
                    for idx in range(host_start, host_end):
                        if idx < len(topo_data['hosts']):
                            host_id = topo_data['hosts'][idx]
                            created_hosts.add(host_id)
                            # Show representative hosts (every 8th one) to reduce clutter
                            if (idx - host_start) % 8 == 0 or idx == host_end - 1:
                                h_sub.node(f'H{host_id}', label=f'H{host_id}', fontsize='6')
                            else:
                                h_sub.node(f'H{host_id}', label='', width='0.15', fontsize='4')
                else:
                    # Other pods: just show ellipsis
                    h_sub.attr(style='invis')
                    ellipsis_name = f'ellipsis_pod{pod_id}'
                    h_sub.node(ellipsis_name, label=f'... {hosts_per_pod} hosts ...',
                               shape='none', fontsize='8', fontcolor='gray50')
                    # Mark all these hosts as "created" via ellipsis so we don't try to create edges to them
                    for idx in range(host_start, host_end):
                        if idx < len(topo_data['hosts']):
                            created_hosts.add(topo_data['hosts'][idx])

    # Add edges
    edge_counts = defaultdict(int)
    for src, dst, rate in topo_data['links']:
        # Determine node types
        src_type = ''
        dst_type = ''
        src_prefix = ''
        dst_prefix = ''

        if src in topo_data['cores']:
            src_type = 'core'
            src_prefix = 'C'
        elif src in topo_data['aggs']:
            src_type = 'agg'
            src_prefix = 'A'
        elif src in topo_data['tors']:
            src_type = 'tor'
            src_prefix = 'T'
        elif src in topo_data['hosts']:
            src_type = 'host'
            src_prefix = 'H'
        else:
            src_prefix = f'N{src}'
            src_node = src_prefix

        if dst in topo_data['cores']:
            dst_type = 'core'
            dst_prefix = 'C'
        elif dst in topo_data['aggs']:
            dst_type = 'agg'
            dst_prefix = 'A'
        elif dst in topo_data['tors']:
            dst_type = 'tor'
            dst_prefix = 'T'
        elif dst in topo_data['hosts']:
            dst_type = 'host'
            dst_prefix = 'H'
        else:
            dst_prefix = f'N{dst}'
            dst_node = dst_prefix

        # Skip edges to/from hosts not in first pod (they're represented by ellipsis)
        if src_type == 'host' and src not in created_hosts:
            continue
        if dst_type == 'host' and dst not in created_hosts:
            continue

        src_node = f'{src_prefix}{src}'
        dst_node = f'{dst_prefix}{dst}'

        # Edge style based on connection type
        edge_attrs = {}
        if src_type == 'host' and dst_type == 'tor':
            edge_attrs = {'color': 'gray70', 'style': 'solid'}
        elif src_type == 'tor' and dst_type == 'host':
            edge_attrs = {'color': 'gray70', 'dir': 'back', 'style': 'solid'}
        elif src_type == 'tor' and dst_type == 'agg':
            edge_attrs = {'color': 'blue', 'penwidth': '1'}
        elif src_type == 'agg' and dst_type == 'tor':
            edge_attrs = {'color': 'blue', 'dir': 'back', 'penwidth': '1'}
        elif src_type == 'agg' and dst_type == 'core':
            edge_attrs = {'color': 'red', 'penwidth': '1.5'}
        elif src_type == 'core' and dst_type == 'agg':
            edge_attrs = {'color': 'red', 'dir': 'back', 'penwidth': '1.5'}

        # Add edge (avoid duplicates for bidirectional links)
        edge_key = tuple(sorted([src_node, dst_node]))
        if edge_counts[edge_key] == 0:
            dot.edge(src_node, dst_node, **edge_attrs)
            edge_counts[edge_key] += 1

    # Render with high DPI for better quality
    output_base = output_file.replace('.png', '')
    png_file = output_base + '.png'
    svg_file = output_base + '.svg'
    dot_file = output_base + '.gv'

    # Generate dot file
    with open(dot_file, 'w') as f:
        f.write(dot.source)

    # Generate SVG (no size limit, scalable) - keep this for zooming
    subprocess.run(['dot', '-Tsvg', '-o', svg_file, dot_file],
                       check=True, capture_output=True)
    print(f"SVG file saved to: {svg_file} (scalable, open in browser to zoom)")

    # Also generate PNG
    try:
        subprocess.run(['dot', '-Tpng', '-Gdpi=150', '-o', png_file, dot_file],
                       check=True, capture_output=True)
        print(f"PNG file saved to: {png_file}")
    except subprocess.CalledProcessError:
        print("PNG generation failed, but SVG is available")

    # Clean up
    if os.path.exists(dot_file):
        os.remove(dot_file)

    print(f"Nodes: {len(topo_data['hosts'])} hosts, {len(topo_data['tors'])} ToR, "
          f"{len(topo_data['aggs'])} Agg, {len(topo_data['cores'])} Core")
    print(f"Links: {len(topo_data['links'])}")
    print(f"Showing hosts for Pod 0 only ({hosts_per_pod} hosts)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python plot_topology.py <topology_file> [output_png]")
        print("\nExample:")
        print("  python plot_topology.py ../config/fat_k8_100G_400G_OS10.txt")
        print("  python plot_topology.py ../config/fat_k16_5pods_256perPod_100G_400G_OS1.txt my_topology")
        sys.exit(1)

    topology_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else topology_file.split('/')[-1].replace('.txt', '')

    print(f"Parsing topology file: {topology_file}")
    topo_data = parse_topology(topology_file)

    print(f"Generating topology graph...")
    generate_topology_graph(topo_data, output_file)


if __name__ == '__main__':
    main()
