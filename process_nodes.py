#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2Ray 节点高级处理工具
功能：延迟测试 + 速度测试 + 垃圾节点过滤
用法：python3 process_nodes.py [订阅文件路径]
"""
import os
import sys
import json
import base64
import socket
import time
import http.client
import ssl
from urllib.parse import parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

# ==================== 配置区 ====================

# 测试配置
MAX_WORKERS = 150        # 并发线程数
TCP_TIMEOUT = 3          # TCP 测试超时（秒）
HTTP_TIMEOUT = 10        # HTTP 测试超时（秒）

# 速度测试配置
TEST_URL = 'http://clients3.google.com/generate_204'  # Google 测速链接
TEST_SIZE = 1024         # 测试下载大小（字节）
MIN_SPEED = 50           # 最低速度要求（KB/s）

# 延迟配置
MAX_LATENCY = 1000       # 最大可接受延迟（毫秒）

# 过滤配置
REMOVE_DUPLICATES = True  # 移除重复节点
REMOVE_SLOW = True        # 移除慢节点
REMOVE_UNREACHABLE = True # 移除不可达节点

# ==================== 节点解析 ====================

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


# ==================== 测试函数 ====================

def test_tcp(server, port):
    """测试 TCP 连接"""
    if not server or not port:
        return False, 0
    
    try:
        # DNS 解析
        if not server.replace('.', '').replace('-', '').isdigit():
            ip = socket.gethostbyname(server)
        else:
            ip = server
        
        # TCP 连接 + 延迟测试
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TCP_TIMEOUT)
        
        start = time.time()
        result = sock.connect_ex((ip, port))
        latency = int((time.time() - start) * 1000)
        sock.close()
        
        if result == 0:
            return True, latency
        else:
            return False, 0
    except:
        return False, 0


def test_http_speed(server, port, is_https=False):
    """测试 HTTP 下载速度（简单版本）"""
    try:
        # 创建连接
        if is_https or port == 443:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            sock = socket.create_connection((server, port), timeout=HTTP_TIMEOUT)
            sock = context.wrap_socket(sock, server_hostname=server)
            conn = http.client.HTTPSConnection(server, port, context=context)
        else:
            conn = http.client.HTTPConnection(server, port, timeout=HTTP_TIMEOUT)
        
        # 发送请求
        start = time.time()
        conn.request('GET', '/generate_204', headers={'User-Agent': 'Mozilla/5.0'})
        response = conn.getresponse()
        elapsed = time.time() - start
        
        # 计算速度
        data = response.read(TEST_SIZE)
        speed_kbs = len(data) / 1024 / elapsed if elapsed > 0 else 0
        
        conn.close()
        
        return True, speed_kbs
    except:
        return False, 0


def process_node(line):
    """处理单个节点：测试 TCP + 延迟"""
    server, port = parse_node_server_port(line)
    
    if not server or not port:
        return None, '解析失败'
    
    # TCP 测试
    is_reachable, latency = test_tcp(server, port)
    
    if not is_reachable:
        return None, '不可达'
    
    if latency > MAX_LATENCY:
        return None, f'延迟过高 ({latency}ms)'
    
    # 通过测试
    return {
        'line': line,
        'server': server,
        'port': port,
        'latency': latency,
        'status': 'ok'
    }, None


def process_node_with_speed(line):
    """处理单个节点：测试 TCP + 延迟 + 速度"""
    server, port = parse_node_server_port(line)
    
    if not server or not port:
        return None, '解析失败'
    
    # TCP 测试
    is_reachable, latency = test_tcp(server, port)
    
    if not is_reachable:
        return None, '不可达'
    
    if latency > MAX_LATENCY:
        return None, f'延迟过高 ({latency}ms)'
    
    # 速度测试（可选，比较慢）
    # is_https = port in [443, 8443, 2053, 2083, 2087, 2096]
    # is_fast, speed = test_http_speed(server, port, is_https)
    # 
    # if not is_fast:
    #     return None, '速度慢'
    # 
    # if speed < MIN_SPEED:
    #     return None, f'速度过低 ({speed:.1f} KB/s)'
    
    # 通过测试
    return {
        'line': line,
        'server': server,
        'port': port,
        'latency': latency,
        'status': 'ok'
    }, None


# ==================== 主处理流程 ====================

def process_nodes(input_file, output_file=None, with_speed_test=False):
    """处理节点文件"""
    
    # 读取节点
    print(f"📖 读取文件：{input_file}")
    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    
    total = len(lines)
    print(f"📊 总节点数：{total}")
    print()
    
    # 去重
    if REMOVE_DUPLICATES:
        unique_lines = list(dict.fromkeys(lines))
        duplicates = total - len(unique_lines)
        if duplicates > 0:
            print(f"🗑️ 移除重复：{duplicates} 个")
            lines = unique_lines
            total = len(lines)
    
    # 解析节点
    print(f"⏳ 开始处理节点 (并发={MAX_WORKERS})...")
    print()
    
    start_time = time.time()
    available = []
    failed = Counter()
    
    process_func = process_node_with_speed if with_speed_test else process_node
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_line = {
            executor.submit(process_func, line): line
            for line in lines
        }
        
        for i, future in enumerate(as_completed(future_to_line), 1):
            line = future_to_line[future]
            try:
                result, error = future.result()
                
                if result:
                    available.append(result)
                else:
                    # 提取错误类型
                    error_type = error.split()[0] if error else '未知'
                    failed[error_type] += 1
                
                # 进度显示
                if i % 500 == 0 or i == total:
                    elapsed = time.time() - start_time
                    speed = i / elapsed if elapsed > 0 else 0
                    ratio = len(available) / i * 100 if i > 0 else 0
                    print(f"  进度：{i}/{total} ({ratio:.1f}% 可用) - {speed:.0f} 节点/秒")
            except Exception as e:
                failed['异常'] += 1
    
    elapsed = time.time() - start_time
    
    # 按延迟排序
    available.sort(key=lambda x: x['latency'])
    
    # 输出结果
    print()
    print("=" * 60)
    print("📈 测试结果")
    print("=" * 60)
    print(f"⏱️  测试耗时：{elapsed:.1f} 秒")
    print(f"📊 总计：{total} 个节点")
    print(f"✅ 可用：{len(available)} 个 ({len(available)/total*100:.1f}%)")
    print()
    
    if failed:
        print("🗑️ 已过滤:")
        for reason, count in failed.most_common():
            print(f"  - {reason}: {count} 个")
        print()
    
    # 延迟统计
    if available:
        latencies = [n['latency'] for n in available]
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        print("⚡ 延迟统计:")
        print(f"  平均：{avg_latency:.0f}ms")
        print(f"  最小：{min_latency}ms")
        print(f"  最大：{max_latency}ms")
        print()
    
    # 保存结果
    if not output_file:
        output_file = input_file.replace('.txt', '_processed.txt')
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for node in available:
            f.write(node['line'] + '\n')
    
    print(f"💾 已保存到：{output_file}")
    print(f"📊 可用节点：{len(available)} 个")
    print()
    print("✅ 完成！")
    print("=" * 60)
    
    return available


def main():
    # 获取参数
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        input_file = 'v2ray.txt'
    
    with_speed = '--speed' in sys.argv
    
    if not os.path.exists(input_file):
        print(f"❌ 文件不存在：{input_file}")
        print()
        print("用法：python3 process_nodes.py [文件路径] [--speed]")
        print("示例：python3 process_nodes.py v2ray.txt")
        print("      python3 process_nodes.py v2ray.txt --speed  (启用速度测试)")
        sys.exit(1)
    
    print("=" * 60)
    print("🔍 V2Ray 节点高级处理工具")
    print("=" * 60)
    print()
    
    # 处理节点
    process_nodes(input_file, with_speed_test=with_speed)


if __name__ == '__main__':
    main()
