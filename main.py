#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订阅源抓取工具（只去重，不检测可用性）
支持将 v2ray 节点转换为 clash 格式
"""
import os
import re
import sys
import time
import json
import base64
import feedparser
import requests
from urllib.parse import unquote, parse_qs
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


def merge_v2ray_nodes(contents):
    """合并多个 V2Ray 订阅内容（只去重）"""
    all_nodes = set()
    
    for content in contents:
        if not content:
            continue
        
        nodes = [line.strip() for line in content.split('\n') if line.strip() and not line.startswith('#')]
        
        for node in nodes:
            if node.startswith(('vmess://', 'vless://', 'trojan://', 'ss://', 'hysteria2://', 'ssr://')):
                all_nodes.add(node)
    
    write_log(f"去重后共 {len(all_nodes)} 个节点")
    return sorted(list(all_nodes))


def parse_v2ray_node(line):
    """解析 v2ray 节点链接"""
    try:
        line = line.strip()
        if not line or '://' not in line:
            return None
        
        proto = line.split('://')[0].lower()
        
        if proto == 'vmess':
            return parse_vmess(line)
        elif proto in ('ss', 'shadowsocks'):
            return parse_ss(line)
        elif proto == 'trojan':
            return parse_trojan(line)
        elif proto == 'vless':
            return parse_vless(line)
        elif proto in ('hysteria2', 'hysteria'):
            return parse_hysteria2(line)
        
        return None
    except:
        return None


def parse_vmess(line):
    """解析 vmess://"""
    try:
        b64 = line[8:]
        b64 += '=' * (4 - len(b64) % 4) if len(b64) % 4 else ''
        data = json.loads(base64.b64decode(b64).decode('utf-8'))
        
        name = data.get('ps', 'VMess-' + str(hash(line))[:6])
        proxy = {
            'name': name,
            'type': 'vmess',
            'server': data.get('add', '').strip(),
            'port': int(str(data.get('port', 0)).strip()),
            'uuid': data.get('id', '').strip(),
            'alterId': int(str(data.get('aid', 0)).strip()),
            'cipher': 'auto',
            'tls': data.get('tls', '') == 'tls',
            'skip-cert-verify': True
        }
        
        net = data.get('net', 'tcp')
        if net:
            proxy['network'] = net
        
        if net == 'ws':
            path = data.get('path', '/')
            host = data.get('host', '')
            if path or host:
                proxy['ws-opts'] = {
                    'path': path,
                    'headers': {'Host': host}
                }
        
        return proxy
    except:
        return None


def parse_ss(line):
    """解析 ss://"""
    try:
        link = line[5:]
        name = 'SS'
        if '#' in link:
            parts = link.split('#', 1)
            name = unquote(parts[1])
            link = parts[0]
        
        if '@' not in link:
            link = base64.b64decode(link + '==').decode()
        
        if '@' in link:
            userinfo, serverpart = link.split('@', 1)
            try:
                decoded = base64.b64decode(userinfo + '==').decode()
                if ':' in decoded:
                    cipher, password = decoded.split(':', 1)
                else:
                    cipher, password = 'aes-256-gcm', decoded
            except:
                cipher, password = 'aes-256-gcm', userinfo
            
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                server = server.strip()
                port = int(port.strip())
            else:
                return None
        else:
            return None
        
        return {
            'name': name,
            'type': 'ss',
            'server': server,
            'port': port,
            'cipher': cipher,
            'password': password
        }
    except:
        return None


def parse_trojan(line):
    """解析 trojan://"""
    try:
        link = line[9:]
        name = 'Trojan'
        if '#' in link:
            parts = link.split('#', 1)
            name = unquote(parts[1])
            link = parts[0]
        
        if '@' in link:
            password, rest = link.split('@', 1)
            
            if '?' in rest:
                serverpart, params_str = rest.split('?', 1)
                params = parse_qs(params_str)
            else:
                serverpart = rest
                params = {}
            
            serverpart = serverpart.split('/')[0]
            
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                server = server.strip()
                port = int(port.strip())
            else:
                return None
        else:
            return None
        
        proxy = {
            'name': name,
            'type': 'trojan',
            'server': server,
            'port': port,
            'password': password.strip(),
            'skip-cert-verify': True
        }
        
        sni = params.get('sni', [''])[0]
        if sni:
            proxy['sni'] = sni
        
        return proxy
    except:
        return None


def parse_vless(line):
    """解析 vless://"""
    try:
        link = line[7:]
        name = 'VLESS'
        if '#' in link:
            parts = link.split('#', 1)
            name = unquote(parts[1])
            link = parts[0]
        
        if '@' in link:
            uuid, rest = link.split('@', 1)
            uuid = uuid.lstrip('/')
            
            if '?' in rest:
                serverpart, params_str = rest.split('?', 1)
                params = parse_qs(params_str)
            else:
                serverpart = rest
                params = {}
            
            serverpart = serverpart.split('/')[0]
            
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                server = server.strip()
                port = int(port.strip())
            else:
                return None
        else:
            return None
        
        proxy = {
            'name': name,
            'type': 'vless',
            'server': server,
            'port': port,
            'uuid': uuid.strip(),
            'skip-cert-verify': True
        }
        
        security = params.get('security', [''])[0]
        if security == 'tls':
            proxy['tls'] = True
            sni = params.get('sni', [server])[0]
            if sni:
                proxy['sni'] = sni
        
        return proxy
    except:
        return None


def parse_hysteria2(line):
    """解析 hysteria2://"""
    try:
        link = line[12:]
        name = 'Hysteria2'
        if '#' in link:
            parts = link.split('#', 1)
            name = unquote(parts[1])
            link = parts[0]
        
        if '@' in link:
            auth, serverpart = link.split('@', 1)
            serverpart = serverpart.split('?')[0].split('/')[0]
            
            if ':' in serverpart:
                server, port = serverpart.rsplit(':', 1)
                server = server.strip()
                port = int(port.strip())
            else:
                return None
        else:
            return None
        
        return {
            'name': name,
            'type': 'hysteria2',
            'server': server,
            'port': port,
            'password': auth.strip(),
            'skip-cert-verify': True
        }
    except:
        return None


def convert_to_clash(v2ray_nodes):
    """将 v2ray 节点转换为 clash 格式"""
    proxies = []
    
    for line in v2ray_nodes:
        proxy = parse_v2ray_node(line)
        if proxy and proxy.get('server') and proxy.get('port'):
            proxies.append(proxy)
    
    write_log(f"转换成功：{len(proxies)} 个 clash 节点")
    return proxies


def generate_clash_config(proxies):
    """生成完整的 clash 配置"""
    proxy_names = [p['name'] for p in proxies]
    
    config = {
        'mixed-port': 7890,
        'allow-lan': True,
        'mode': 'rule',
        'log-level': 'info',
        'external-controller': '127.0.0.1:9090',
        'dns': {
            'enable': True,
            'listen': '0.0.0.0:1053',
            'ipv6': True,
            'enhanced-mode': 'fake-ip',
            'fake-ip-range': '198.18.0.1/16',
            'default-nameserver': ['223.5.5.5', '119.29.29.29'],
            'nameserver': [
                'https://dns.alidns.com/dns-query',
                'https://doh.pub/dns-query'
            ]
        },
        'proxies': proxies,
        'proxy-groups': [
            {
                'name': '🚀 节点选择',
                'type': 'select',
                'proxies': ['⚖️ 自动选择'] + proxy_names[:100] if proxy_names else ['DIRECT']
            },
            {
                'name': '⚖️ 自动选择',
                'type': 'url-test',
                'proxies': proxy_names[:100] if proxy_names else ['DIRECT'],
                'url': 'https://www.google.com/generate_204',
                'interval': 300,
                'tolerance': 150
            }
        ]
    }
    
    return config


def main():
    """主函数"""
    write_log("=" * 50)
    write_log("开始抓取订阅（只去重，不检测可用性）")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    sources = load_sources()
    if not sources:
        write_log("没有找到可用的订阅源", "ERROR")
        return
    
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
                        if content.startswith('proxies:') or 'proxy-groups:' in content:
                            clash_contents.append(content)
                        else:
                            v2ray_contents.append(content)
                except:
                    pass
    
    # 合并 V2Ray 节点（只去重）
    if v2ray_contents:
        merged_v2ray = merge_v2ray_nodes(v2ray_contents)
        
        if merged_v2ray:
            v2ray_file = os.path.join(OUTPUT_DIR, 'v2ray.txt')
            with open(v2ray_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(merged_v2ray))
            write_log(f"已保存 V2Ray 订阅：{v2ray_file} ({len(merged_v2ray)} 行)")
            
            # 转换为 clash 格式
            write_log("正在转换节点为 clash 格式...")
            clash_proxies = convert_to_clash(merged_v2ray)
            
            if clash_proxies:
                # 生成完整配置
                clash_config = generate_clash_config(clash_proxies)
                
                clash_file = os.path.join(OUTPUT_DIR, 'clash.yml')
                with open(clash_file, 'w', encoding='utf-8') as f:
                    yaml.dump(clash_config, f, default_flow_style=False, allow_unicode=True)
                
                write_log(f"已保存 Clash 订阅：{clash_file} ({len(clash_proxies)} 个节点)")
    
    # 保存原始 Clash 订阅（如果有）
    if clash_contents and not os.path.exists(os.path.join(OUTPUT_DIR, 'clash.yml')):
        clash_file = os.path.join(OUTPUT_DIR, 'clash.yml')
        with open(clash_file, 'w', encoding='utf-8') as f:
            f.write(clash_contents[0])
        write_log(f"已保存原始 Clash 订阅：{clash_file}")
    
    write_log("订阅抓取完成")
    write_log("=" * 50)


if __name__ == '__main__':
    # 导入 yaml
    import yaml
    main()
