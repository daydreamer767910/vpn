#!/usr/bin/env python3
import os
import json
import copy
import argparse
from pathlib import Path
import base64
import requests
import random
import urllib.parse as urlparse

BASE_DIR = Path(__file__).resolve().parent

PORT_START = 10000
PORT_END = 20000

NODES_PATH = BASE_DIR / "singbox/nodes.json"
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

    outbounds = []

    # sing-box 格式
    if isinstance(data, dict) and "outbounds" in data:
        for ob in data["outbounds"]:
            if isinstance(ob, dict) and "type" in ob:
                outbounds.append(copy.deepcopy(ob))

    # 纯数组
    elif isinstance(data, list):
        for ob in data:
            if isinstance(ob, dict) and "type" in ob:
                outbounds.append(copy.deepcopy(ob))

    else:
        raise RuntimeError("❌ 不支持的 JSON 订阅格式")

    print(f"✅ 成功解析: {len(outbounds)} 个订阅")

    return outbounds

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

    return outbounds

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


def save_nodes(nodes):
    save_json(NODES_PATH, {"nodes": nodes})


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


# -----------------------
# 端口生成（无状态）
# -----------------------
def generate_port_map(nodes, protocols_template):
    port = PORT_START
    port_map = {}

    nodes_sorted = sorted(nodes, key=lambda x: x["tag"])

    for node in nodes_sorted:
        tag = node["tag"]
        protos = node["protocols"]

        for idx, proto_name in enumerate(protos):
            proto = protocols_template.get(proto_name, {})

            need_port = False

            if "inbound" in proto and "listen_port" in proto["inbound"]:
                need_port = True
            elif "outbound" in proto and "server_port" in proto["outbound"]:
                need_port = True

            if not need_port:
                continue

            if port > PORT_END:
                raise RuntimeError("端口耗尽")

            port_map[(tag, idx)] = port
            port += 1

    return port_map


# -----------------------
# 节点操作
# -----------------------
def add_node(nodes, tag, protocols, type_="normal", next_=None, sub_=None):
    if any(n["tag"] == tag for n in nodes):
        print(f"❌ 已存在 {tag}")
        return nodes

    node = {
        "tag": tag,
        "protocols": protocols,
        "type": type_,
    }

    if type_ == "relay":
        node["next"] = next_
    elif type_ == "exit":
        node["source"] = sub_

    nodes.append(node)
    return nodes


def update_node(nodes, tag, protocols=None, type_=None, next_=None, sub_=None):
    for n in nodes:
        if n["tag"] == tag:
            if protocols:
                n["protocols"] = protocols
            if type_:
                n["type"] = type_
            if type_ == "relay" and next_:
                n["next"] = next_
            if type_ == "exit":
                n["source"] = sub_
            return nodes

    print(f"❌ 不存在 {tag}")
    return nodes


def delete_node(nodes, tag):
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
def build(nodes, protocols_template, tls_template,
          server_config, client_config):
    """
    构建配置：
      - normal 节点A类:生成 inbound ,build config for both server and client
      - relay 节点B类:生成 inbound ,build config for both server and client
      - exit 节点C类:生成 outbound,build config for server only
    """

    port_map = generate_port_map(nodes, protocols_template)
    
    for node in nodes:
        node["inbound_tags"] = []
        node["outbound_tags"] = []
        tag = node["tag"]
        type_ = node.get("type", "normal")  # 默认为 normal
        protos = node["protocols"]

        if type_ == "exit":
            build_exit_outbounds(node, server_config)
            continue
        for i, proto_name in enumerate(protos):
            if proto_name not in protocols_template:
                continue
            if proto_name in SPECIAL_OUTBOUNDS or proto_name in SPECIAL_INBOUNDS:
                continue
            proto = protocols_template[proto_name]

            # inbound
            if type_ in ("normal", "relay") and "inbound" in proto:
                inbound = copy.deepcopy(proto["inbound"])
                inbound = fill_placeholders(inbound, tls_template, tag)

                port = port_map.get((tag, i))
                if port and "listen_port" in inbound:
                    inbound["listen_port"] = port

                upsert_by_tag(server_config["inbounds"], inbound)
                node["inbound_tags"].append(inbound["tag"])
            # outbound
            if "outbound" in proto:
                outbound = copy.deepcopy(proto["outbound"])
                outbound = fill_placeholders(outbound, tls_template, tag)

                port = port_map.get((tag, i))
                if port and "server_port" in outbound:
                    outbound["server_port"] = port
                upsert_by_tag(client_config["outbounds"], outbound)


def build_exit_outbounds(node, server_config):
    tag = node["tag"]

    # ---------- 获取节点 ----------
    if node.get("source"):
        outbounds = parse_subscription(node["source"])
    else:
        raise RuntimeError(f"❌ exit 节点缺少 source: {tag}")

    # ---------- 协议过滤 ----------
    protocols = node.get("protocols")
    if protocols:
        outbounds = [o for o in outbounds if o.get("type") in protocols]

    if not outbounds:
        raise RuntimeError(f"❌ exit 节点无可用 outbound: {tag}")

    # ---------- 选择策略 ----------
    selected = [random.choice(outbounds)]

    # ---------- 写入 ----------
    node.setdefault("outbound_tags", [])

    for i, ob in enumerate(selected):
        ob = ob.copy()

        # 避免 tag 冲突（关键）
        ob["tag"] = f"{tag}-{i}-{ob['type']}-out"

        upsert_by_tag(server_config["outbounds"], ob)
        node["outbound_tags"].append(ob["tag"])

def build_defaults(config):
    if "log" not in config:
        config["log"] = {"level": "info"}

def apply_patches(server_config, client_config, protocols_template, tls_template):
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
    build_dynamic_outbounds(client_config)
    build_defaults(server_config)
    build_defaults(client_config)
# -----------------------
# Selector / URLTest
# -----------------------
def build_dynamic_outbounds(client_config):
    all_tags = [o.get("tag") for o in client_config.get("outbounds", []) if o.get("tag") != "direct"]

    if not all_tags:
        return

    selector_outbound = {
        "tag": "auto-selector",
        "type": "selector",
        "outbounds": all_tags,
        "default": all_tags[0],
        "interrupt_exist_connections": False,
    }
    client_config["outbounds"].append(selector_outbound)

    urltest_outbound = {
        "tag": "auto-proxy",
        "type": "urltest",
        "outbounds": all_tags,
        "url": "https://www.microsoft.com",
    }
    client_config["outbounds"].append(urltest_outbound)

def apply_relay(nodes, server_config):
    """
    为类型为 relay 的节点生成 route 规则：
      - 节点自身必须有 inbound_tags
      - next 节点必须存在 outbound_tags
    """
    node_map = {n["tag"]: n for n in nodes}
    route = server_config.setdefault("route", {})
    rules = route.setdefault("rules", [])

    for node in nodes:
        if node.get("type") != "relay":
            continue

        src_tag = node["tag"]
        dst_tag = node.get("next")

        if not dst_tag:
            raise RuntimeError(f"❌ relay 节点缺少 next: {src_tag}")

        if dst_tag not in node_map:
            raise RuntimeError(f"❌ relay 目标不存在: {dst_tag}")

        if dst_tag == src_tag:
            raise RuntimeError(f"❌ relay 不能指向自己: {src_tag}")

        src_node = node
        dst_node = node_map[dst_tag]

        if not src_node.get("inbound_tags"):
            raise RuntimeError(f"❌ relay 节点没有 inbound_tags: {src_tag}")

        if not dst_node.get("outbound_tags"):
            raise RuntimeError(f"❌ 目标节点没有 outbound_tags: {dst_tag}")

        # 简单规则：每个 relay 节点默认只用第一个 inbound_tag 对应第一个 outbound_tag
        src_in_tag = src_node["inbound_tags"]
        dst_out_tag = dst_node["outbound_tags"][0]

        # 插入 route 规则
        rule = {
            "action": "route",
            "inbound": src_in_tag,
            "outbound": dst_out_tag
        }
        rules.insert(0, rule)  # 优先级靠前

# bug: router: inbound detour not found
def apply_relay_detour(nodes, server_config):
    node_map = {n["tag"]: n for n in nodes}
    inbounds = server_config.get("inbounds", [])

    for node in nodes:
        if node.get("type") != "relay":
            continue

        dst = node.get("next")
        dst_node = node_map.get(dst)

        if not dst_node or not dst_node.get("outbound_tags"):
            continue  # 直接跳过，不报错

        dst_out = dst_node["outbound_tags"][0]

        for inbound in inbounds:
            if inbound.get("tag") in node.get("inbound_tags", []):
                inbound["detour"] = dst_out

def run_manage_users():
    script = BASE_DIR / "manage_users.py"

    if not script.exists():
        print("⚠️ 未找到 manage_users.py，跳过用户同步")
        return

    print("🔄 同步用户配置...")

    ret = os.system(f"python3 {script} --refresh")

    if ret != 0:
        print("❌ 用户同步失败")
    else:
        print("✅ 用户同步完成")
# -----------------------
# 防火墙
# -----------------------
def collect_ports_from_config(server_config):
    ports = set()

    for i in server_config.get("inbounds", []):
        p = i.get("listen_port")
        if p:
            ports.add(p)

    return ports


def apply_firewall(server_config):
    ports = sorted(collect_ports_from_config(server_config))

    print("🔥 同步防火墙...")

    os.system("ufw --force reset")
    os.system("ufw default deny incoming")
    os.system("ufw allow 22/tcp")
    os.system("ufw allow 80/tcp")
    os.system("ufw allow 443/tcp")

    for p in ports:
        os.system(f"ufw allow {p}/tcp")
        os.system(f"ufw allow {p}/udp")

    os.system("ufw --force enable")

    print(f"✅ 已开放端口: {sorted(ports)}")


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

    ports = set()
    for i in server_config.get("inbounds", []):
        p = i.get("listen_port")
        if p:
            if p in ports:
                errors.append(f"❌ 端口冲突: {p}")
            ports.add(p)

    if errors:
        print("\n".join(errors))
        raise RuntimeError("❌ 配置检查失败")
    else:
        print("✅ 配置检查通过")

def show_config(server_config, client_config):
    print("\n=== Server Inbounds ===")
    for i in server_config.get("inbounds", []):
        tag = i.get("tag")
        proto = i.get("type")
        port = i.get("listen_port")
        print(f"tag={tag:<20} type={proto:<12} port={port}")

    print("\n=== Server Outbounds ===")
    for o in server_config.get("outbounds", []):
        tag = o.get("tag")
        proto = o.get("type")
        port = o.get("server_port")
        print(f"tag={tag:<20} type={proto:<12} port={port}")

    print("\n=== Client Inbounds ===")
    for i in client_config.get("inbounds", []):
        tag = i.get("tag")
        proto = i.get("type")
        port = i.get("listen_port")
        print(f"tag={tag:<20} type={proto:<12} port={port}")

    print("\n=== Client Outbounds ===")
    for o in client_config.get("outbounds", []):
        tag = o.get("tag")
        proto = o.get("type")
        port = o.get("server_port")
        print(f"tag={tag:<20} type={proto:<12} port={port}")

# -----------------------
# 主函数
# -----------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs="*")
    parser.add_argument("--update", nargs="*")
    parser.add_argument("--delete", nargs="*")
    parser.add_argument("--protocols", nargs="*")
    parser.add_argument("--firewall", action="store_true")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--list", action="store_true")
    # 新增节点类型参数
    parser.add_argument("--type", choices=["normal", "relay", "exit"], default="normal",
                        help="节点类型: normal/relay/exit")
    # relay 特有字段
    parser.add_argument("--next", help="relay 节点的下一跳目标 tag")
    # exit 特有字段
    parser.add_argument("--sub", help="exit 节点远端订阅配置")

    args = parser.parse_args()

    template_dir = BASE_DIR / "template"

    protocols_template = load_json(template_dir / "protocols.json")
    tls_template = load_json(template_dir / "tls.json")
    route_template = load_json(template_dir / "route.json")
    dns_template = load_json(template_dir / "dns.json")
    endpoints_template = load_json(template_dir / "endpoints.json")

    server_path = BASE_DIR / "singbox/server/config.json"
    client_path = BASE_DIR / "singbox/client/config.json"

    server_config = {
        "inbounds": [],
        "outbounds": [],
        "endpoints": endpoints_template,
        "route": route_template.get("route-server", {}),
        "dns": dns_template.get("dns-server", {})
    }

    client_config = {
        "inbounds": [],
        "outbounds": [],
        "route": route_template.get("route-client", {}),
        "dns": dns_template.get("dns-client", {})
    }

    nodes = load_nodes()
    old_nodes = copy.deepcopy(nodes)

    protocols = []
    if args.protocols:
        for p in args.protocols:
            protocols.extend(p.split(","))
    else:
        protocols = list(protocols_template.keys())

    if args.add:
        if args.type == "relay" and not args.next:
            raise RuntimeError("❌ relay 节点必须指定 --next")
        if args.type == "exit" and (not args.sub):
            raise RuntimeError("❌ exit 节点必须指定 --sub")
        for t in args.add:
            nodes = add_node(nodes, t, protocols, args.type, args.next, args.sub)

    if args.update:
        for t in args.update:
            nodes = update_node(nodes, t, protocols, args.type, args.next, args.sub)

    if args.delete:
        for t in args.delete:
            nodes = delete_node(nodes, t)

    changed = (old_nodes != nodes)
    if changed:
        build(nodes, protocols_template, tls_template,
              server_config, client_config)

        apply_patches(server_config, client_config,
              protocols_template, tls_template)

        apply_relay(nodes, server_config)

        save_nodes(nodes)
        save_json(server_path, server_config)
        save_json(client_path, client_config)
        print("✅ 已更新配置")

        run_manage_users()

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