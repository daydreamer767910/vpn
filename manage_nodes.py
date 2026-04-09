#!/usr/bin/env python3
import os
import json
import yaml
import copy
import argparse
from pathlib import Path
import base64
import requests
import random
import subprocess
import urllib.parse as urlparse

BASE_DIR = Path(__file__).resolve().parent

PORT_START = 10000
PORT_END = 20000

CLASH_API_TOKEN="123456"
NODES_PATH = BASE_DIR / "singbox/nodes.json"
DOCKER_COMPOSE_PATH = BASE_DIR / "docker-compose.yml"

SPECIAL_OUTBOUNDS = {
    "direct",
    "block",
    "tor",
    "selector",
    "urltest",
}

SPECIAL_INBOUNDS = {
    "tun",
}

# 协议 -> 要映射的端口类型
# tcp: 只映射 TCP
# udp: 只映射 UDP
# both: TCP 和 UDP
PROTOCOL_PORT_TYPE = {
    "vless": "tcp",
    "tuic": "udp",
    "hysteria2": "udp",
    "shadowsocks": "both",
    "trojan": "tcp",
    "socks": "tcp",
    "http": "tcp",
    "wireguard": "udp",
    "tun": None,   # 不映射 Docker
    "direct": None,
    "block": None,
    "selector": None,
    "urltest": None,
}

ENDPOINT_TYPES = {
    "wireguard",
    "tailscale",
    # 未来可扩展
}

def run_cmd(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ 命令失败: {' '.join(cmd)}")
        print(result.stderr)
        raise RuntimeError("command failed")
    return result.stdout

def load_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return {}


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def load_nodes():
    return load_json(NODES_PATH).get("nodes", [])


def save_nodes():
    save_json(NODES_PATH, {"nodes": nodes})


template_dir = BASE_DIR / "template"

protocols_template = load_json(template_dir / "protocols.json")
tls_template = load_json(template_dir / "tls.json")
route_template = load_json(template_dir / "route.json")
dns_template = load_json(template_dir / "dns.json")
endpoints_template = load_json(template_dir / "endpoints.json")
nodes = load_nodes()
old_nodes = copy.deepcopy(nodes)
# -----------------------
# 工具
# -----------------------
def parse_vless(link: str) -> dict:
    u = urlparse.urlparse(link)

    uuid = u.username
    server = u.hostname
    port = u.port

    query = urlparse.parse_qs(u.query)

    def q(key, default=None):
        return query.get(key, [default])[0]

    outbound = {
        "type": "vless",
        "tag": f"{server}-vless-out",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "tls": {}
    }

    # flow
    if q("flow"):
        outbound["flow"] = q("flow")

    # TLS / Reality
    if q("security") == "reality":
        outbound["tls"] = {
            "enabled": True,
            "reality": {
                "public_key": q("pbk"),
                "short_id": q("sid", "")
            },
            "server_name": q("sni"),
        }
    elif q("security") == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": q("sni"),
        }

    return outbound

def parse_tuic(link: str) -> dict:
    u = urlparse.urlparse(link)

    uuid = u.username
    password = u.password
    server = u.hostname
    port = u.port

    query = urlparse.parse_qs(u.query)

    def q(key, default=None):
        return query.get(key, [default])[0]

    outbound = {
        "type": "tuic",
        "tag": f"{server}-tuic-out",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": q("sni")
        }
    }

    if q("congestion_control"):
        outbound["congestion_control"] = q("congestion_control")

    return outbound

def parse_hy2(link: str) -> dict:
    u = urlparse.urlparse(link)

    password = u.username
    server = u.hostname
    port = u.port

    query = urlparse.parse_qs(u.query)

    def q(key, default=None):
        return query.get(key, [default])[0]

    outbound = {
        "type": "hysteria2",
        "tag": f"{server}-hy2-out",
        "server": server,
        "server_port": port,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": q("sni")
        }
    }

    if q("obfs"):
        outbound["obfs"] = {
            "type": q("obfs"),
            "password": q("obfs-password")
        }

    return outbound

def parse_link(link: str) -> dict:
    if link.startswith("vless://"):
        return parse_vless(link)
    elif link.startswith("tuic://"):
        return parse_tuic(link)
    elif link.startswith("hysteria2://") or link.startswith("hy2://"):
        return parse_hy2(link)
    else:
        raise ValueError(f"❌ 不支持的协议: {link}")

def fetch_subscription(url):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.text.strip()


def try_base64_decode(text):
    try:
        return base64.b64decode(text).decode()
    except:
        return None

def parse_json_subscription(content):
    data = json.loads(content)

    result = {
        "outbounds": [],
        "endpoints": []
    }

    if isinstance(data, dict):

        # ---------- endpoints ----------
        for ep in data.get("endpoints", []):
            if isinstance(ep, dict) and "type" in ep:
                result["endpoints"].append(copy.deepcopy(ep))

        # ---------- outbounds ----------
        for ob in data.get("outbounds", []):
            if isinstance(ob, dict) and "type" in ob:
                result["outbounds"].append(copy.deepcopy(ob))

        # ---------- 兼容错误格式（关键）----------
        # 有些订阅会把 endpoint 写进 outbounds（很常见）
        fixed_outbounds = []
        for ob in result["outbounds"]:
            if ob.get("type") in ENDPOINT_TYPES:
                result["endpoints"].append(ob)
            else:
                fixed_outbounds.append(ob)

        result["outbounds"] = fixed_outbounds

    elif isinstance(data, list):
        for ob in data:
            if not isinstance(ob, dict):
                continue

            if ob.get("type") in ENDPOINT_TYPES:
                result["endpoints"].append(copy.deepcopy(ob))
            else:
                result["outbounds"].append(copy.deepcopy(ob))

    else:
        raise RuntimeError("❌ 不支持的 JSON 订阅格式")

    print(f"✅ outbounds={len(result['outbounds'])}, endpoints={len(result['endpoints'])}")

    return result

def parse_subscription(url):
    content = fetch_subscription(url)

    # ---------- JSON ----------
    if content.startswith("{") or content.startswith("["):
        return parse_json_subscription(content)

    # ---------- base64 ----------
    decoded = try_base64_decode(content)
    if decoded:
        lines = decoded.splitlines()
    else:
        lines = content.splitlines()

    # ---------- 链接 ----------
    outbounds = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ob = parse_link(line)
            outbounds.append(copy.deepcopy(ob))
        except Exception as e:
            print(f"⚠️ 跳过: {line[:30]}... {e}")

    return {
        "outbounds": outbounds,
        "endpoints": []
    }

# -----------------------
# 占位符
# -----------------------
def fill_placeholders(obj, tls_templates=None, node_tag=None):
    if isinstance(obj, dict):
        return {k: fill_placeholders(v, tls_templates, node_tag) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fill_placeholders(i, tls_templates, node_tag) for i in obj]
    elif isinstance(obj, str):
        if obj.startswith("$tls:"):
            key = obj[5:]
            return copy.deepcopy(tls_templates.get(key, obj))

        if obj.startswith("$env:"):
            parts = obj[5:].split(":", 1)
            return os.environ.get(parts[0], parts[1] if len(parts) > 1 else "")

        if "$tag" in obj and node_tag:
            return obj.replace("$tag", node_tag)

    return obj

def assign_ports_for_node(tag, protocols, user_ports=None):
    """
    给每个协议分配端口：
      - 如果端口已经被占用，但协议类型不同（TCP/UDP）可共用
      - 用户指定端口重复时检查协议类型
    """
    # used_ports: { port: set of protocol types }
    used_ports = {}
    for n in nodes:
        for idx, p in enumerate(n.get("ports", [])):
            if p is None:
                continue
            proto_name = n.get("protocols", [])[idx] if idx < len(n.get("protocols", [])) else None
            proto_type = PROTOCOL_PORT_TYPE.get(proto_name)
            if proto_type is None:
                continue
            types = set()
            if proto_type in ("tcp", "both"):
                types.add("tcp")
            if proto_type in ("udp", "both"):
                types.add("udp")
            if p in used_ports:
                used_ports[p].update(types)
            else:
                used_ports[p] = types

    ports = []
    port_idx = 0
    for idx, proto_name in enumerate(protocols):
        proto_type = PROTOCOL_PORT_TYPE.get(proto_name)
        if proto_type is None:
            ports.append(None)
            continue

        types = set()
        if proto_type in ("tcp", "both"):
            types.add("tcp")
        if proto_type in ("udp", "both"):
            types.add("udp")

        # 用户指定端口
        if user_ports and port_idx < len(user_ports):
            port = user_ports[port_idx]
            if port in used_ports and used_ports[port] & types:
                raise RuntimeError(f"❌ 用户指定端口 {port} 已被占用（协议冲突）")
        else:
            # 自动生成端口
            port = PORT_START + (abs(hash(f"{tag}-{idx}")) % (PORT_END - PORT_START))
            while port in used_ports and used_ports[port] & types:
                port += 1
                if port > PORT_END:
                    port = PORT_START
            if port in used_ports and used_ports[port] & types:
                raise RuntimeError("❌ 端口耗尽")

        # 更新已用端口映射
        if port in used_ports:
            used_ports[port].update(types)
        else:
            used_ports[port] = types

        ports.append(port)
        port_idx += 1

    return ports
# -----------------------
# 节点操作
# -----------------------
def add_node(tag, protocols, sub_=None, ports_=None):
    if any(n["tag"] == tag for n in nodes):
        print(f"❌ 已存在 {tag}")
        return nodes

    assigned_ports = assign_ports_for_node(tag, protocols, ports_)

    node = {
        "tag": tag,
        "protocols": protocols,
        "ports": assigned_ports,
    }

    node["source"] = sub_

    nodes.append(node)
    return nodes


def update_node(tag, protocols=None, sub_=None, ports_=None):
    for n in nodes:
        if n["tag"] == tag:
            if protocols:
                n["protocols"] = protocols
            if ports_:
                n["ports"] = assign_ports_for_node(tag, protocols, ports_)
            if sub_:
                n["source"] = sub_
            return nodes

    print(f"❌ 不存在 {tag}")
    return nodes


def delete_node(tag):
    return [n for n in nodes if n["tag"] != tag]


def upsert_by_tag(arr, item):
    tag = item.get("tag")
    if not tag:
        arr.append(item)
        return

    for i, x in enumerate(arr):
        if x.get("tag") == tag:
            arr[i] = item
            return

    arr.append(item)


# -----------------------
# 构建 config
# -----------------------
def build(server_config, client_config):

    for node in nodes:
        node["inbound_tags"] = []
        node["outbound_tags"] = []
        tag = node["tag"]
        
        protos = node["protocols"]

        for i, proto_name in enumerate(protos):
            if proto_name not in protocols_template:
                continue
            if proto_name in SPECIAL_OUTBOUNDS or proto_name in SPECIAL_INBOUNDS:
                continue
            proto = protocols_template[proto_name]

            # inbound
            if "inbound" in proto:
                inbound = copy.deepcopy(proto["inbound"])
                inbound = fill_placeholders(inbound, tls_template, tag)

                port = node["ports"][i]
                if port and "listen_port" in inbound:
                    inbound["listen_port"] = port

                upsert_by_tag(server_config["inbounds"], inbound)
                node["inbound_tags"].append(inbound["tag"])
            # outbound
            if "outbound" in proto:
                outbound = copy.deepcopy(proto["outbound"])
                outbound = fill_placeholders(outbound, tls_template, tag)

                port = node["ports"][i]
                if port and "server_port" in outbound:
                    outbound["server_port"] = port
                upsert_by_tag(client_config["outbounds"], outbound)


def build_subscription(client_config):
    outbounds = client_config.get("outbounds", [])
    all_tags = list(dict.fromkeys(
        item.get("tag")
        for item in outbounds
        if is_valid_selector_outbound(item)
    ))
    detour = all_tags[-1]
    for node in nodes:
        tag = "<sub>"
        # ---------- 获取节点 ----------
        if node.get("source"):
            sub = parse_subscription(node["source"])

            outbounds = sub.get("outbounds", [])
            endpoints = sub.get("endpoints", [])
        else:
            continue

        for ep in endpoints:
            ep["tag"] = f"{tag}-{ep["tag"]}"
            ep["detour"] = detour
            upsert_by_tag(client_config["endpoints"], ep)

        # ---------- 协议过滤 ----------
        outbounds = [o for o in outbounds if o.get("type") not in SPECIAL_OUTBOUNDS]

        for ob in outbounds:
            ob["tag"] = f"{tag}-{ob["tag"]}"
            ob["detour"] = detour
            upsert_by_tag(client_config["outbounds"], ob)

def build_defaults(config, ui_=None):
    if "log" not in config:
        config["log"] = {"level": "info"}
    if "experimental" not in config:
        if ui_:
            config["experimental"] = {
                "clash_api": {
                    "external_controller": "127.0.0.1:9090",
                    "external_ui": "dashboard",
                    "secret": CLASH_API_TOKEN
                }
            }
        else:
            config["experimental"] = {
                "clash_api": {
                    "external_controller": "0.0.0.0:9090",
                    "secret": CLASH_API_TOKEN
                }
            }

def apply_patches(server_config, client_config):
    # -------- direct --------
    if "direct" in protocols_template:
        proto = protocols_template["direct"]

        if "outbound" in proto:
            outbound = copy.deepcopy(proto["outbound"])
            upsert_by_tag(client_config["outbounds"], outbound)
            upsert_by_tag(server_config["outbounds"], outbound)

    # -------- block --------
    if "block" in protocols_template:
        proto = protocols_template["block"]

        if "outbound" in proto:
            outbound = copy.deepcopy(proto["outbound"])
            upsert_by_tag(client_config["outbounds"], outbound)
            upsert_by_tag(server_config["outbounds"], outbound)

    # -------- tun（只客户端）--------
    if "tun" in protocols_template:
        proto = protocols_template["tun"]

        if "inbound" in proto:
            inbound = copy.deepcopy(proto["inbound"])
            inbound = fill_placeholders(inbound, tls_template, None)
            upsert_by_tag(client_config["inbounds"], inbound)

    # -------- selector / urltest --------
    build_subscription(client_config)
    build_dynamic_outbounds(client_config)
    build_defaults(server_config)
    build_defaults(client_config, True)

# -----------------------
# Selector / URLTest
# -----------------------
def is_valid_selector_outbound(o):
    tag = o.get("tag")
    typ = o.get("type")

    if typ in SPECIAL_OUTBOUNDS or not tag:
        return False

    return True

def build_dynamic_outbounds(client_config):
    outbounds = client_config.get("outbounds", [])
    endpoints = client_config.get("endpoints", [])
    all_tags = list(dict.fromkeys(
        item.get("tag")
        for item in (endpoints + outbounds)
        if is_valid_selector_outbound(item)
    ))

    if not all_tags:
        return
    local_tags  = [t for t in all_tags if t and not t.startswith("<sub>-")]
    remote_tags = [t for t in all_tags if t and t.startswith("<sub>-")]

    if local_tags:
        selector_outbound_local = {
            "tag": "auto-selector",
            "type": "selector",
            "outbounds": local_tags,
            "default": local_tags[-1],
            "interrupt_exist_connections": False,
        }
        upsert_by_tag(client_config["outbounds"], selector_outbound_local)

    if remote_tags:
        selector_outbound_remote = {
            "tag": "auto-selector-sub",
            "type": "selector",
            "outbounds": remote_tags,
            "default": remote_tags[-1],
            "interrupt_exist_connections": False,
        }
        upsert_by_tag(client_config["outbounds"], selector_outbound_remote)

    urltest_sources = []
    if local_tags:
        urltest_sources.append("auto-selector")
    if remote_tags:
        urltest_sources.append("auto-selector-sub")

    if urltest_sources:
        urltest_outbound = {
            "tag": "auto-proxy",
            "type": "urltest",
            "outbounds": urltest_sources,
            "url": "https://www.gstatic.com",
        }
        upsert_by_tag(client_config["outbounds"], urltest_outbound)

def run_manage_users():
    script = BASE_DIR / "manage_users.py"

    if not script.exists():
        print("⚠️ 未找到 manage_users.py，跳过用户同步")
        return

    print("🔄 同步用户配置...")

    ret = run_cmd(["python3", str(script), "--apply"])

    print(ret)

# -----------------------
# 防火墙
# -----------------------
def collect_ports_from_config(server_config):
    """
    返回 dict: port -> 类型
      例如 {443: "tcp", 10000: "both"}
    支持 TCP/UDP 共用端口
    """
    ports = {}

    def process(items):
        for i in items:
            port = i.get("listen_port")
            proto = i.get("type")
            if not port or port == 0 or proto not in PROTOCOL_PORT_TYPE:
                continue
            typ = PROTOCOL_PORT_TYPE[proto]
            if port in ports:
                if ports[port] != typ:
                    ports[port] = "both"
            else:
                ports[port] = typ

    process(server_config.get("inbounds", []))
    process(server_config.get("endpoints", []))
    return ports

def apply_firewall(server_config):
    port_map = collect_ports_from_config(server_config)

    print("🔥 同步防火墙...")

    run_cmd(["ufw", "--force", "reset"])
    run_cmd(["ufw", "default", "deny", "incoming"])
    run_cmd(["ufw", "allow", "22/tcp"])
    run_cmd(["ufw", "allow", "80/tcp"])
    run_cmd(["ufw", "allow", "443/tcp"])

    for port, typ in port_map.items():
        if typ == "tcp":
            run_cmd(["ufw", "allow", "{port}/tcp"])
        elif typ == "udp":
            run_cmd(["ufw", "allow", "{port}/udp"])
        elif typ == "both":
            run_cmd(["ufw", "allow", "{port}/tcp"])
            run_cmd(["ufw", "allow", "{port}/udp"])
        # None 表示不开放

    run_cmd(["ufw", "--force", "enable"])

    print(f"✅ 已开放端口: {sorted(port_map.keys())}")

def update_docker_compose_ports(server_config):
    port_mappings = []
    port_map = collect_ports_from_config(server_config)
    for port, typ in port_map.items():
        if typ == "both":
            port_mappings.append(f"{port}:{port}/tcp")
            port_mappings.append(f"{port}:{port}/udp")
        else:
            port_mappings.append(f"{port}:{port}/{typ}")


    if not port_mappings:
        print("⚠️ 没有有效端口需要同步到 Docker Compose")
        return

    if not DOCKER_COMPOSE_PATH.exists():
        print(f"⚠️ 未找到 {DOCKER_COMPOSE_PATH}")
        return

    with open(DOCKER_COMPOSE_PATH, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    if "sing-box" not in compose_data.get("services", {}):
        print("⚠️ docker-compose.yml 中未找到 sing-box 服务")
        return

    compose_data["services"]["sing-box"]["ports"] = port_mappings

    with open(DOCKER_COMPOSE_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(compose_data, f, sort_keys=False)

    print(f"✅ 已同步端口到 docker-compose.yml: {port_mappings}")

def restart_singbox_container():
    print("🔄 重启 sing-box 容器...")
    ret = subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "sing-box"],
        cwd=BASE_DIR
    )
    if ret.returncode == 0:
        print("✅ sing-box 容器已重启")
    else:
        print("❌ 重启失败，请手动检查")
# -----------------------
# Lint
# -----------------------
def lint_config(server_config, client_config):
    errors = []

    in_tags = set()
    out_tags = set()

    for i in server_config.get("inbounds", []):
        tag = i.get("tag")
        if tag in in_tags:
            errors.append(f"❌ inbound tag 重复: {tag}")
        in_tags.add(tag)

    for o in client_config.get("outbounds", []):
        tag = o.get("tag")
        if tag in out_tags:
            errors.append(f"❌ outbound tag 重复: {tag}")
        out_tags.add(tag)

    for ep in server_config.get("endpoints", []):
        if "tag" in ep:
            out_tags.add(ep["tag"])

    for o in server_config.get("outbounds", []):
        tag = o.get("tag")
        if tag:
            out_tags.add(tag)

    def check_route(route):
        if not route:
            return
        for r in route.get("rules", []):
            ob = r.get("outbound")
            if ob and ob not in out_tags:
                errors.append(f"❌ route 引用了不存在的 outbound: {ob}")

    check_route(server_config.get("route"))
    check_route(client_config.get("route"))

    def check_dns(dns):
        if not dns:
            return
        for s in dns.get("servers", []):
            detour = s.get("detour")
            if detour and detour not in out_tags:
                errors.append(f"❌ dns detour 不存在: {detour}")

    check_dns(server_config.get("dns"))
    check_dns(client_config.get("dns"))

    ports = {}  # {port: set("tcp", "udp")}
    for i in server_config.get("inbounds", []):
        p = i.get("listen_port")
        proto_name = i.get("type")
        if not p or not proto_name:
            continue
        proto_type = PROTOCOL_PORT_TYPE.get(proto_name)
        # 不需要映射端口的协议跳过
        if proto_type is None:
            continue
        types = set()
        if proto_type in ("tcp", "both"):
            types.add("tcp")
        if proto_type in ("udp", "both"):
            types.add("udp")
        # 检查冲突
        if p in ports:
            conflict = ports[p] & types
            if conflict:
                errors.append(f"❌ 端口冲突: {p} ({'/'.join(conflict)})")

            ports[p].update(types)
        else:
            ports[p] = types

    if errors:
        print("\n".join(errors))
        raise RuntimeError("❌ 配置检查失败")
    else:
        print("✅ 配置检查通过")

def show_config(server_config, client_config):
    print("\n==================== Server Inbounds ====================")
    for i in server_config.get("inbounds", []):
        tag = i.get("tag")
        proto = i.get("type")
        port = i.get("listen_port")
        print(f"tag={tag:<25} type={proto:<12} port={port}")

    print("\n==================== Server Outbounds ====================")
    for o in server_config.get("outbounds", []):
        tag = o.get("tag")
        proto = o.get("type")
        port = o.get("server_port")
        print(f"tag={tag:<25} type={proto:<12} port={port}")

    print("\n====================  Server  Endpoints  ====================")
    for o in server_config.get("endpoints", []):
        tag = o.get("tag")
        proto = o.get("type")
        port = o.get("listen_port")
        print(f"tag={tag:<25} type={proto:<12} port={port}")

    print("\n====================  Client  Inbounds  ====================")
    for i in client_config.get("inbounds", []):
        tag = i.get("tag")
        proto = i.get("type")
        port = i.get("listen_port")
        print(f"tag={tag:<25} type={proto:<12} port={port}")

    print("\n====================  Client  Outbounds  ====================")
    for o in client_config.get("outbounds", []):
        tag = o.get("tag")
        proto = o.get("type")
        port = o.get("server_port")
        print(f"tag={tag:<25} type={proto:<12} port={port}")

    print("\n====================  Client  Endpoints  ====================")
    for o in client_config.get("endpoints", []):
        tag = o.get("tag", "-")
        proto = o.get("type", "-")
        peers = o.get("peers", [])
        ports = [str(p.get("port")) for p in peers if p.get("port")]
        port_str = ",".join(ports) if ports else "-"
        print(f"tag={tag:<25} type={proto:<12} port={port_str}")

# -----------------------
# 主函数
# -----------------------
def main():
    global nodes
    global old_nodes
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs="*")
    parser.add_argument("--port", nargs="*", type=int, help="指定端口（按协议顺序）")
    parser.add_argument("--update", nargs="*")
    parser.add_argument("--delete", nargs="*")
    parser.add_argument("--protocols", nargs="*")
    parser.add_argument("--firewall", action="store_true")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--sub", help="其它节点远端订阅配置")

    args = parser.parse_args()

    server_path = BASE_DIR / "singbox/server/config.json"
    client_path = BASE_DIR / "singbox/client/config.json"

    server_config = {
        "inbounds": [],
        "outbounds": [],
        "endpoints": endpoints_template.get("endpoint-server", {}),
        "route": route_template.get("route-server", {}),
        "dns": dns_template.get("dns-server", {})
    }

    client_config = {
        "inbounds": [],
        "outbounds": [],
        "endpoints": endpoints_template.get("endpoint-client", {}),
        "route": route_template.get("route-client", {}),
        "dns": dns_template.get("dns-client", {})
    }

    def is_real_protocol(name):
        if name in SPECIAL_OUTBOUNDS or name in SPECIAL_INBOUNDS:
            return False
        proto = protocols_template.get(name, {})
        return "inbound" in proto or "outbound" in proto

    protocols = []
    if args.protocols:
        for p in args.protocols:
            protocols.extend(p.split(","))
    else:
        protocols = [
            name for name in protocols_template.keys()
            if is_real_protocol(name)
        ]

    if args.add:
        for t in args.add:
            nodes = add_node(t, protocols, args.sub, args.port)

    if args.update:
        for t in args.update:
            nodes = update_node(t, protocols, args.sub, args.port)

    if args.delete:
        for t in args.delete:
            nodes = delete_node(t)

    changed = (old_nodes != nodes)
    if changed or args.refresh:
        build(server_config, client_config)

        apply_patches(server_config, client_config)

        save_nodes()
        save_json(server_path, server_config)
        save_json(client_path, client_config)
        print("✅ 已更新配置")
        run_manage_users()
        # 同步端口到 docker-compose
        update_docker_compose_ports(server_config)
        restart_singbox_container()

    if args.firewall:
        server_config = load_json(server_path)
        apply_firewall(server_config)

    if args.lint:
        server_config = load_json(server_path)
        client_config = load_json(client_path)
        lint_config(server_config, client_config)

    if args.list:
        server_config = load_json(server_path)
        client_config = load_json(client_path)
        show_config(server_config, client_config)

if __name__ == "__main__":
    main()