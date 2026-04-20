#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订阅源抓取工具（带节点可用性检测）
从 subscribe_sources.txt 读取订阅源列表，自动抓取并更新订阅文件
"""
import os
import re
import sys
import time
import json
import socket
import feedparser
import requests
import base64
from urllib.parse import parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

requests.packages.urllib3.disable_warnings()

# 配置文件路径
SOURCES_FILE = './subscribe_sources.txt'
CONFIG_FILE = './subscribe_sources.json'
OUTPUT_DIR = './subscribe'
LOG_DIR = './log'

# 请求配置
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
TIMEOUT = 30
OK_CODES = [200, 201, 202, 203, 204, 205, 206]

# 节点检测配置
NODE_TEST_TIMEOUT = 5  # 节点测试超时（秒）
NODE_TEST_WORKERS = 200  # 并发测试数
MIN_AVAILABLE_RATIO = 0.3  # 最低可用率（30%），低于此值认为订阅源失效


def write_log(content, level="INFO"):
    """写入日志"""
    date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
    log_line = f"[{date_str}] [{level}] {content}\n"
    print(log_line.strip())
    
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f'{time.strftime("%Y-%m")}-update.log')
    with open(log_file, 'a', encoding="utf-8") as f:
        f.write(log_line)


def load_sources():
    """加载订阅源列表"""
    sources = []
    
    # 优先使用 JSON 配置
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for src in config.get('sources', []):
                    if src.get('enabled', True):
                        sources.append(src)
                write_log(f"从 {CONFIG_FILE} 加载了 {len(sources)} 个订阅源")
                return sources
        except Exception as e:
            write_log(f"读取 {CONFIG_FILE} 失败：{e}", "ERROR")
    
    # 回退到 TXT 配置
    if os.path.exists(SOURCES_FILE):
        with open(SOURCES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split('|')
                if len(parts) >= 3:
                    src_type, name, url = parts[0], parts[1], parts[2]
                    sources.append({
                        'name': name,
                        'url': url,
                        'type': src_type.lower()
                    })
        
        write_log(f"从 {SOURCES_FILE} 加载了 {len(sources)} 个订阅源")
    
    return sources


def fetch_rss_source(source):
    """抓取 RSS 订阅源"""
    name = source.get('name', 'Unknown')
    url = source.get('url', '')
    
    try:
        write_log(f"正在抓取 RSS: {name}")
        rss = feedparser.parse(url)
        entries = rss.get('entries', [])
        
        if not entries:
            write_log(f"RSS {name} 没有内容", "WARN")
            return None, None
        
        v2ray_url = None
        clash_url = None
        
        # 解析最新条目
        for entry in entries[:3]:
            summary = entry.get('summary', '')
            
            if not v2ray_url:
                matches = re.findall(r">V2Ray[^>]*-&gt;\s*(.*?)</span>", summary)
                if matches:
                    v2ray_url = matches[-1].replace('amp;', '')
            
            if not clash_url:
                matches = re.findall(r">clash[^>]*-&gt;\s*(.*?)</span>", summary)
                if matches and not matches[-1].startswith("订阅地址生成失败"):
                    clash_url = matches[-1].replace('amp;', '')
        
        return v2ray_url, clash_url
        
    except Exception as e:
        write_log(f"RSS {name} 抓取失败：{e}", "ERROR")
        return None, None


def fetch_direct_source(source):
    """抓取直链订阅源"""
    name = source.get('name', 'Unknown')
    url = source.get('url', '')
    
    try:
        write_log(f"正在抓取直链：{name}")
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
        
        if resp.status_code not in OK_CODES:
            write_log(f"直链 {name} 状态码：{resp.status_code}", "WARN")
            return None
        
        content = resp.text
        if not content.strip():
            write_log(f"直链 {name} 内容为空", "WARN")
            return None
        
        return content
        
    except Exception as e:
        write_log(f"直链 {name} 抓取失败：{e}", "ERROR")
        return None


def parse_node(line):
    """解析节点链接，提取服务器和端口"""
    try:
        line = line.strip()
        if not line or '://' not in line:
            return None, None
        
        proto = line.split('://')[0]
        
        if proto == 'ss':
            return parse_ss(line)
        elif proto == 'vmess':
            return parse_vmess(line)
        elif proto == 'trojan':
            return parse_trojan(line)
        elif proto == 'vless':
            return parse_vless(line)
        elif proto in ('hysteria2', 'hysteria'):
            return parse_hysteria2(line)
        
        return None, None
    except:
        return None, None


def parse_ss(line):
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


def parse_vmess(line):
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


def parse_trojan(line):
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


def parse_vless(line):
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


def parse_hysteria2(line):
    try:
        link = line[12:]
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


def test_node(server, port):
    """测试节点 TCP 连接"""
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
        sock.settimeout(NODE_TEST_TIMEOUT)
        result = sock.connect_ex((ip, port))
        sock.close()
        
        return result == 0
    except:
        return False


def test_nodes_availability(nodes):
    """批量测试节点可用性"""
    if not nodes:
        return []
    
    write_log(f"开始测试 {len(nodes)} 个节点的可用性 (并发={NODE_TEST_WORKERS})...")
    
    # 解析所有节点
    parsed = []
    for line in nodes:
        server, port = parse_node(line)
        if server and port:
            parsed.append((line, server, port))
    
    write_log(f"成功解析 {len(parsed)} 个节点")
    
    # 批量测试
    available = []
    
    with ThreadPoolExecutor(max_workers=NODE_TEST_WORKERS) as executor:
        future_to_node = {
            executor.submit(test_node, server, port): (line, server, port)
            for line, server, port in parsed
        }
        
        for i, future in enumerate(as_completed(future_to_node), 1):
            line, server, port = future_to_node[future]
            try:
                if future.result():
                    available.append(line)
                
                if i % 500 == 0:
                    write_log(f"  测试进度：{i}/{len(parsed)} (可用：{len(available)})")
            except:
                pass
    
    # 计算可用率
    ratio = len(available) / len(parsed) if parsed else 0
    write_log(f"节点可用率：{ratio*100:.1f}% ({len(available)}/{len(parsed)})")
    
    return available


def merge_v2ray_nodes(contents):
    """合并多个 V2Ray 订阅内容"""
    all_nodes = set()
    
    for content in contents:
        if not content:
            continue
        
        nodes = [line.strip() for line in content.split('\n') if line.strip()]
        
        for node in nodes:
            if node.startswith(('vmess://', 'vless://', 'trojan://', 'ss://', 'hysteria2://')):
                all_nodes.add(node)
    
    return list(all_nodes)


def main():
    """主函数"""
    write_log("=" * 50)
    write_log("开始抓取订阅")
    
    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 加载订阅源
    sources = load_sources()
    if not sources:
        write_log("没有找到可用的订阅源", "ERROR")
        return
    
    # 分类订阅源
    rss_sources = [s for s in sources if s.get('type') == 'rss']
    direct_sources = [s for s in sources if s.get('type') in ('direct', 'telegram')]
    
    v2ray_contents = []
    clash_contents = []
    
    # 抓取 RSS 源
    if rss_sources:
        write_log(f"处理 {len(rss_sources)} 个 RSS 源...")
        for source in rss_sources:
            v2ray_url, clash_url = fetch_rss_source(source)
            
            if v2ray_url:
                try:
                    resp = requests.get(v2ray_url, headers=HEADERS, timeout=TIMEOUT, verify=False)
                    if resp.status_code in OK_CODES:
                        v2ray_contents.append(resp.text)
                except:
                    pass
            
            if clash_url:
                try:
                    resp = requests.get(clash_url, headers=HEADERS, timeout=TIMEOUT, verify=False)
                    if resp.status_code in OK_CODES:
                        clash_contents.append(resp.text)
                except:
                    pass
    
    # 抓取直链源
    if direct_sources:
        write_log(f"处理 {len(direct_sources)} 个直链源...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_direct_source, s): s for s in direct_sources}
            
            for future in as_completed(futures):
                source = futures[future]
                try:
                    content = future.result()
                    if content:
                        # 判断内容类型
                        if content.startswith('proxies:') or 'proxy-groups:' in content:
                            clash_contents.append(content)
                        else:
                            v2ray_contents.append(content)
                except:
                    pass
    
    # 合并 V2Ray 节点
    if v2ray_contents:
        merged_v2ray = merge_v2ray_nodes(v2ray_contents)
        write_log(f"合并后共 {len(merged_v2ray)} 个 V2Ray 节点")
        
        # 测试节点可用性
        available_v2ray = test_nodes_availability(merged_v2ray)
        
        if available_v2ray:
            v2ray_file = os.path.join(OUTPUT_DIR, 'v2ray.txt')
            with open(v2ray_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(sorted(available_v2ray)))
            write_log(f"已保存 V2Ray 订阅：{v2ray_file} ({len(available_v2ray)} 行)")
    
    # 保存 Clash 订阅
    if clash_contents:
        clash_file = os.path.join(OUTPUT_DIR, 'clash.yml')
        with open(clash_file, 'w', encoding='utf-8') as f:
            f.write(clash_contents[0])
        write_log(f"已保存 Clash 订阅：{clash_file}")
    
    write_log("订阅抓取完成")
    write_log("=" * 50)


if __name__ == '__main__':
    main()
