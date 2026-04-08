#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wg_endpoint_manager.py - WireGuard 资源管理脚本
功能：
- 管理 wg-server 类型资源
- 支持 add, delete, update, list
- 自动生成默认值，包括内网 IP、端口、密钥、peer
"""

import json
import os
import random
import string
import argparse
import subprocess
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENDPOINT_FILE = BASE_DIR / "singbox" / "endpoints.json"
TEMPLATE_FILE = BASE_DIR / "template" / "endpoints.json"
CONFIG_SH = BASE_DIR / "config.sh"

# ------------------ 工具函数 ------------------
def parse_config_sh(sh_file: Path):
    if not sh_file.exists():
        ts_print("config.sh 不存在")
        return None
    cmd = f'''
    source "{sh_file}"
    if [ -n "$DOMAIN" ]; then echo DOMAIN="$DOMAIN"; fi
    '''
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    domain = None
    for line in result.stdout.splitlines():
        if line.startswith("DOMAIN="):
            domain = line.split("=",1)[1].strip()
        
    if not domain: ts_print("⚠️ 没找到 DOMAIN")
    
    return domain

def load_endpoints():
    if ENDPOINT_FILE.exists():
        with open(ENDPOINT_FILE, "r") as f:
            return json.load(f)
    return []

def save_endpoints(resources):
    with open(ENDPOINT_FILE, "w") as f:
        json.dump(resources, f, indent=2)

def generate_wg_keypair():
    cmd = ["docker", "run", "--rm", "ghcr.io/sagernet/sing-box", "generate", "wg-keypair"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    priv = None
    pub = None
    for line in result.stdout.splitlines():
        if line.startswith("PrivateKey"):
            priv = line.split()[1]
        elif line.startswith("PublicKey"):
            pub = line.split()[1]
    if not priv or not pub:
        raise RuntimeError("wg-keypair 生成失败")
    return priv, pub

def generate_wg_server_data(address=None, port=None):
    if not address:
        address = f"10.0.0.1/32"
    if not port:
        port = random.randint(20000, 30000)
    srv_priv, srv_pub = generate_wg_keypair()
    peer_priv, peer_pub = generate_wg_keypair()
    data = {
        "address": address,
        "listen_port": port,
        "private_key": srv_priv,
        "public_key": srv_pub,
        "peer": {
            "allowed_ips": ["0.0.0.0/0"],
            "private_key": peer_priv,
            "public_key": peer_pub
        }
    }
    return data

def export_to_template():
    domain = parse_config_sh(CONFIG_SH)
    resources = load_endpoints()

    if not resources:
        print("⚠️ endpoints 为空，跳过生成")
        return

    endpoint_server_list = []
    endpoint_client_list = []

    for r in resources:
        name = r['name']
        data = r['data']

        # --------------------
        # 解析 IP
        # --------------------
        ip = data['address'].split("/")[0]  # 10.0.0.1
        parts = ip.split(".")
        server_ip = ip
        client_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.{int(parts[3]) + 1}"

        # --------------------
        # SERVER
        # --------------------
        server_ep = {
            "type": "wireguard",
            "tag": f"{name}-ep",
            "mtu": 1280,
            "address": [
                f"{server_ip}/24"
            ],
            "private_key": data['private_key'],
            "listen_port": data['listen_port'],
            "system": False,
            "udp_timeout": "5m",
            "peers": [
                {
                    "public_key": data['peer']['public_key'],
                    "allowed_ips": data['peer']['allowed_ips']
                }
            ]
        }
        endpoint_server_list.append(server_ep)

        # --------------------
        # CLIENT
        # --------------------
        client_ep = {
            "type": "wireguard",
            "tag": f"{name}-ep",
            "name": name,  # wg0 / wg1
            "system": False,
            "mtu": 1280,
            "address": [
                f"{client_ip}/32"
            ],
            "private_key": data['peer']['private_key'],
            "peers": [
                {
                    "address": domain,
                    "port": data['listen_port'],
                    "public_key": data['public_key'],
                    "allowed_ips": ["0.0.0.0/0", "::/0"],
                    "persistent_keepalive_interval": 25
                }
            ]
        }
        endpoint_client_list.append(client_ep)

    # --------------------
    # 读取 template（保留原内容）
    # --------------------
    if TEMPLATE_FILE.exists():
        with open(TEMPLATE_FILE, "r") as f:
            try:
                template_data = json.load(f)
            except:
                template_data = {}
    else:
        template_data = {}

    # --------------------
    # 更新字段
    # --------------------
    template_data["endpoint-server"] = endpoint_server_list
    template_data["endpoint-client"] = endpoint_client_list

    # --------------------
    # 备份
    # --------------------
    import shutil
    if TEMPLATE_FILE.exists():
        shutil.copy(TEMPLATE_FILE, TEMPLATE_FILE.with_suffix(".bak"))

    # --------------------
    # 写入
    # --------------------
    with open(TEMPLATE_FILE, "w") as f:
        json.dump(template_data, f, indent=2)

    print(f"✅ 已生成:")
    print(f"   server: {len(endpoint_server_list)}")
    print(f"   client: {len(endpoint_client_list)}")

# ------------------ 资源操作 ------------------

def add_endpoint(name, user_data={}):
    resources = load_endpoints()
    if any(r["name"]==name for r in resources):
        print(f"❌ 资源 {name} 已存在")
        return

    resource = {
        "name": name,
        "owner": "vps",
        "data": {}
        }

    resource["data"] = generate_wg_server_data(user_data.get("address"),user_data.get("listen_port"))

    resources.append(resource)
    save_endpoints(resources)
    print(f"✅ 已添加资源 {name}")

def delete_endpoint(name):
    resources = load_endpoints()
    new_endpoints = [r for r in resources if r['name'] != name]
    if len(new_endpoints) == len(resources):
        print(f"[ERROR] 未找到资源 {name}")
        return
    save_endpoints(new_endpoints)
    print(f"[OK] 资源 {name} 已删除")

def update_endpoint(name, updates):
    resources = load_endpoints()
    for r in resources:
        if r['name'] == name:
            # 仅允许更新 listen_port, address, peer.allowed_ips
            if 'address' in updates:
                r['data']['address'] = updates['address']
            if 'listen_port' in updates:
                r['data']['listen_port'] = updates['listen_port']
            if 'peer_allowed_ip' in updates:
                r['data']['peer']['allowed_ips'] = [updates['peer_ips']]
            save_endpoints(resources)
            print(f"[OK] 资源 {name} 已更新")
            return
    print(f"[ERROR] 未找到资源 {name}")

def list_endpoints():
    resources = load_endpoints()
    if not resources:
        print("[INFO] 暂无资源")
        return
    for r in resources:
        print(f"- {r['name']} ({r['type']}) status={r['status']} listen_port={r['data']['listen_port']} address={r['data']['address']} peer_ip={r['data']['peer']['allowed_ips'][0]}")

# ------------------ CLI ------------------

def main():
    parser = argparse.ArgumentParser(description="WG 资源管理")
    sub = parser.add_subparsers(dest="cmd")

    # add
    p_add = sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("--address")
    p_add.add_argument("--listen_port", type=int)
    p_add.add_argument("--peer_ips")

    # delete
    p_del = sub.add_parser("delete")
    p_del.add_argument("name")

    # update
    p_upd = sub.add_parser("update")
    p_upd.add_argument("name")
    p_upd.add_argument("--address")
    p_upd.add_argument("--listen_port", type=int)
    p_upd.add_argument("--peer_ips")

    # list
    sub.add_parser("list")

    # CLI
    p_export = sub.add_parser("export", help="将 singbox/endpoints.json 导出到 template/endpoints.json")

    args = parser.parse_args()

    if args.cmd == "add":
        user_data = {}
        if args.address:
            user_data['address'] = args.address
        if args.listen_port:
            user_data['listen_port'] = args.listen_port
        if args.peer_ips:
            user_data['peer_allowed_ip'] = args.peer_ips
        add_endpoint(args.name, user_data)
    elif args.cmd == "delete":
        delete_endpoint(args.name)
    elif args.cmd == "update":
        updates = {}
        if args.address:
            updates['address'] = args.address
        if args.listen_port:
            updates['listen_port'] = args.listen_port
        if args.peer_ips:
            updates['peer_allowed_ip'] = args.peer_ips
        update_endpoint(args.name, updates)
    elif args.cmd == "list":
        list_endpoints()
    elif args.cmd == "export":
        export_to_template()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
