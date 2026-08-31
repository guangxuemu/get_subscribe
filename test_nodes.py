#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2Ray 节点可用性测试工具
用法：python3 test_nodes.py [订阅文件路径]
"""
import os
import sys
import json
import base64
import socket
from urllib.parse import parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import time

# 配置
MAX_WORKERS = 200  # 并发线程数
TIMEOUT = 5  # 超时（秒）


def parse_node_server_port(line):
    """从节点链接提取服务器和端口"""
    try:
        line = line.strip()
        if not line or '://' not in line:
            return None, None
        
        proto = line.split('://')[0].lower()
        
        if proto == 'ss':
            return parse_ss_server(line)
        elif proto == 'vmess':
            return parse_vmess_server(line)
        elif proto == 'trojan':
            return parse_trojan_server(line)
        elif proto == 'vless':
            return parse_vless_server(line)
        elif proto in ('hysteria2', 'hysteria'):
            return parse_hysteria_server(line)
        
        return None, None
    except:
        return None, None


def parse_ss_server(line):
    """解析 ss:// 服务器和端口"""
    try:
        link = line[5:]
        if '@' not in link:
            link = base64.b64decode(link + '==').decode()
        if '@' in link:
            _, serverpart = link.split('@', 1)
            serverpart = serverpart.split('?')[0]
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                return server.strip(), int(port.strip())
    except:
        pass
    return None, None


def parse_vmess_server(line):
    """解析 vmess:// 服务器和端口"""
    try:
        b64 = line[8:]
        b64 += '=' * (4 - len(b64) % 4) if len(b64) % 4 else ''
        data = json.loads(base64.b64decode(b64).decode())
        server = data.get('add', '')
        port = str(data.get('port', ''))
        if server and port.isdigit():
            return server, int(port)
    except:
        pass
    return None, None


def parse_trojan_server(line):
    """解析 trojan:// 服务器和端口"""
    try:
        link = line[9:]
        if '#' in link:
            link = link.split('#')[0]
        if '@' in link:
            _, serverpart = link.split('@', 1)
            serverpart = serverpart.split('?')[0]
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                return server.strip(), int(port.strip())
    except:
        pass
    return None, None


def parse_vless_server(line):
    """解析 vless:// 服务器和端口"""
    try:
        link = line[7:]
        if '#' in link:
            link = link.split('#')[0]
        if '@' in link:
            _, serverpart = link.split('@', 1)
            serverpart = serverpart.split('?')[0]
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                return server.strip(), int(port.strip())
    except:
        pass
    return None, None


def parse_hysteria_server(line):
    """解析 hysteria2:// 服务器和端口"""
    try:
        link = line[12:]
        if '#' in link:
            link = link.split('#')[0]
        if '@' in link:
            _, serverpart = link.split('@', 1)
            serverpart = serverpart.split('?')[0].split('/')[0]
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                return server.strip(), int(port.strip())
    except:
        pass
    return None, None


def test_tcp(server, port):
    """测试 TCP 连接"""
    if not server or not port:
        return False
    
    try:
        # DNS 解析
        if not server.replace('.', '').replace('-', '').isdigit():
            ip = socket.gethostbyname(server)
        else:
            ip = server
        
        # TCP 连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        result = sock.connect_ex((ip, port))
        sock.close()
        
        return result == 0
    except:
        return False


def test_nodes_availability(nodes):
    """批量测试节点可用性"""
    if not nodes:
        return []
    
    print(f"📊 总节点数：{len(nodes)}")
    print(f"⏳ 开始 TCP 可用性测试 (并发={MAX_WORKERS}, 超时={TIMEOUT}s)...")
    print()
    
    # 解析所有节点
    parsed = []
    parse_failed = 0
    for line in nodes:
        server, port = parse_node_server_port(line)
        if server and port:
            parsed.append((line, server, port))
        else:
            parse_failed += 1
    
    if parse_failed > 0:
        print(f"⚠️ 解析失败：{parse_failed} 个节点（格式错误）")
    
    print(f"✅ 成功解析：{len(parsed)} 个节点")
    print()
    
    # 批量测试
    available = []
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_node = {
            executor.submit(test_tcp, server, port): (line, server, port)
            for line, server, port in parsed
        }
        
        for i, future in enumerate(as_completed(future_to_node), 1):
            line, server, port = future_to_node[future]
            try:
                if future.result():
                    available.append(line)
                
                # 进度显示
                if i % 500 == 0 or i == len(parsed):
                    elapsed = time.time() - start_time
                    speed = i / elapsed if elapsed > 0 else 0
                    ratio = len(available) / i * 100 if i > 0 else 0
                    print(f"  进度：{i}/{len(parsed)} ({ratio:.1f}% 可用) - {speed:.0f} 节点/秒")
            except:
                pass
    
    elapsed = time.time() - start_time
    
    # 统计
    ratio = len(available) / len(parsed) * 100 if parsed else 0
    
    print()
    print("=" * 60)
    print("📈 测试结果")
    print("=" * 60)
    print(f"⏱️  测试耗时：{elapsed:.1f} 秒")
    print(f"📊 总计：{len(nodes)} 个节点")
    print(f"✅ 可用：{len(available)} 个 ({ratio:.1f}%)")
    print(f"❌ 不可用：{len(parsed) - len(available)} 个")
    print(f"⚠️ 解析失败：{parse_failed} 个")
    print("=" * 60)
    
    return available


def main():
    # 获取文件路径
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        input_file = 'v2ray.txt'
    
    if not os.path.exists(input_file):
        print(f"❌ 文件不存在：{input_file}")
        print()
        print("用法：python3 test_nodes.py [订阅文件路径]")
        print("示例：python3 test_nodes.py v2ray.txt")
        sys.exit(1)
    
    print("=" * 60)
    print("🔍 V2Ray 节点可用性测试工具")
    print("=" * 60)
    print()
    
    # 读取节点
    print(f"📖 读取文件：{input_file}")
    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    
    # 测试可用性
    available = test_nodes_availability(lines)
    
    if not available:
        print()
        print("❌ 没有可用节点！")
        sys.exit(1)
    
    # 保存结果
    output_file = input_file.replace('.txt', '_available.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(available))
    
    print()
    print(f"💾 已保存到：{output_file}")
    print(f"📊 可用节点：{len(available)} 个")
    print()
    print("✅ 完成！")


if __name__ == '__main__':
    main()
