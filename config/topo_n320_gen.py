def write_test_f(ll):
    with open("./test.txt",'w') as f:
        for x in ll:
            f.write(str(x)+'\n')
def assign_ids():
    """分配节点ID"""
    # 服务器节点IDs (0-1279)
    # server_ids = list(range(1280))
    server_ids = {}
    for pod in range(5):
        server_ids[pod] = list(range(pod*64*4, pod*64*4 + 64*4))
        print("pod_{} servers: {}*4={} to {}*4-1={}, size {}*4={}".format(pod,server_ids[pod][0]/4, server_ids[pod][0],(server_ids[pod][-1]+1)/4, server_ids[pod][-1],len(server_ids[pod])/4,len(server_ids[pod])))
    
    
    # Tor交换机IDs (1280-1439) 每个Pod 32台, pod从0开始标号
    tor_ids = {}
    for pod in range(5):
        tor_ids[pod] = list(range(1280 + pod*32, 1280 + (pod+1)*32))
        print("pod_{} tor: {} to {}, size {}".format(pod,tor_ids[pod][0], tor_ids[pod][-1], len(tor_ids[pod])))
    
    # Agg交换机IDs (1440-1599) 每个Pod 32台
    agg_ids = {}
    for pod in range(5):
        agg_ids[pod] = list(range(1440 + pod*32, 1440 + (pod+1)*32))
        print("pod_{} agg: {} to {}, size {}".format(pod,agg_ids[pod][0], agg_ids[pod][-1], len(agg_ids[pod])))
    
    # Core交换机IDs (1600-1855) 4个plane，每个plane 64台
    core_ids = {}
    for plane in range(4):
        core_ids[plane] = list(range(1600 + plane*64, 1600 + (plane+1)*64))
        print("plane_{} core: {} to {}, size {}".format(plane,core_ids[plane][0], core_ids[plane][-1], len(core_ids[plane])))
    
    return server_ids, tor_ids, agg_ids, core_ids

def generate_links(server_ids, tor_ids, agg_ids, core_ids):
    """生成所有链路"""
    links = []
    
    # 1. 服务器到Tor的链路
    for pod in range(5):
        pod_server_start = server_ids[pod][0]
        pod_tor_start = tor_ids[pod][0]
        for server in range(64):
            for rail in range(4):
                server_port_node = pod_server_start + 4*server + rail # pod_server_start + (0~255)
                server_group = server // 8                
                target_tor_node = pod_tor_start + 8*rail + server_group
                links.append((server_port_node, target_tor_node, 400, 10, 0.0))
    
    # write_test_f(links)
    # return links
    # links = []

    # 2. Tor到Agg的链路（8x8全连接）
    for pod in range(5):
        pod_tor_ids = tor_ids[pod]
        pod_agg_ids = agg_ids[pod]
        
        for group_idx in range(8):
            group_tor = pod_tor_ids[group_idx*8:(group_idx+1)*8]
            group_agg = pod_agg_ids[group_idx*8:(group_idx+1)*8]
            
            for tor in group_tor:
                for agg in group_agg:
                    links.append((tor, agg, 400, 10, 0.0))

    # write_test_f(links)
    # return links
    # links = []

    # 3. Agg到Core的链路
    for plane in range(4):
        plane_core_ids = core_ids[plane]
        core_groups = [plane_core_ids[i*8:(i+1)*8] for i in range(8)]
        
        for pod in range(5):
            pod_agg_ids = agg_ids[pod]
            agg_group = pod_agg_ids[plane*8:(plane+1)*8]
            
            for agg_idx, agg in enumerate(agg_group):
                if agg_idx < len(core_groups):
                    core_group = core_groups[agg_idx]
                    for core in core_group:
                        links.append((agg, core, 400, 100, 0.0))
    # write_test_f(links)
    # return links

    return links

def write_topology_file(filename, tor_ids, agg_ids, core_ids, links):
    """写入拓扑文件，链路按端点1、端点2升序排序"""
    total_nodes = 1856
    switch_nodes = 576
    
    # 收集所有交换机ID并排序
    all_switches = []
    for pod in range(5):
        all_switches.extend(tor_ids[pod])
        all_switches.extend(agg_ids[pod])
    for plane in range(4):
        all_switches.extend(core_ids[plane])
    all_switches.sort()
    
    # 规范化链路：确保每条链路的小ID在前
    normalized_links = []
    for node1, node2, rate, delay, loss in links:
        if node1 < node2:
            normalized_links.append((node1, node2, rate, delay, loss))
        else:
            normalized_links.append((node2, node1, rate, delay, loss))
    
    # 去重
    unique_links = list(set(normalized_links))
    
    # 排序：先按端点1升序，再按端点2升序
    unique_links.sort(key=lambda x: (x[0], x[1]))
    
    with open(filename, 'w') as f:
        # 第一行
        f.write(f"{total_nodes} {switch_nodes} {len(unique_links)}\n")
        
        # 第二行：交换机ID列表
        f.write(' '.join(map(str, all_switches)) + '\n')
        
        # 后续行：排序后的链路信息
        for node1, node2, rate, delay, loss in unique_links:
            f.write(f"{node1} {node2} {rate}Gbps {delay}ns {loss}\n")

def main():
    print("正在生成网络拓扑...")
    server_ids, tor_ids, agg_ids, core_ids = assign_ids()
    links = generate_links(server_ids, tor_ids, agg_ids, core_ids)
    
    print(f"总节点数: 1856")
    print(f"交换机节点数: 576")
    print(f"生成的原始链路数: {len(links)}")
    
    write_topology_file("topo_320server.txt", tor_ids, agg_ids, core_ids, links)
    print("拓扑文件已生成: network_topology.txt")
    
    # 验证
    expected = 1280 + 2560 + 1280  # 5120
    print(f"\n期望链路数: {expected}")
    
    with open("network_topology.txt", 'r') as f:
        lines = f.readlines()
        first_line = lines[0].strip().split()
        actual_links = int(first_line[2])
        print(f"实际链路数: {actual_links}")
        
        # 显示前10条链路验证排序
        print("\n前10条链路（已排序）:")
        for i in range(1, min(11, len(lines)-2)):
            print(f"  {lines[i+1].strip()}")

if __name__ == "__main__":
    main()
