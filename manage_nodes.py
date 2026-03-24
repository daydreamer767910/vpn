#!/usr/bin/env python3
import os
import json
import copy
import argparse
from pathlib import Path

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
def add_node(nodes, tag, protocols):
    if any(n["tag"] == tag for n in nodes):
        print(f"❌ 已存在 {tag}")
        return nodes

    nodes.append({
        "tag": tag,
        "protocols": protocols
    })
    return nodes


def update_node(nodes, tag, protocols=None):
    for n in nodes:
        if n["tag"] == tag:
            if protocols:
                n["protocols"] = protocols
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

    port_map = generate_port_map(nodes, protocols_template)

    for node in nodes:
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

                port = port_map.get((tag, i))
                if port and "listen_port" in inbound:
                    inbound["listen_port"] = port

                upsert_by_tag(server_config["inbounds"], inbound)

            # outbound
            if "outbound" in proto:
                outbound = copy.deepcopy(proto["outbound"])
                outbound = fill_placeholders(outbound, tls_template, tag)

                port = port_map.get((tag, i))
                if port and "server_port" in outbound:
                    outbound["server_port"] = port

                upsert_by_tag(client_config["outbounds"], outbound)


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
        "outbounds": all_tags,
        "default": all_tags[0],
        "interrupt_exist_connections": False,
    }
    client_config["outbounds"].append(selector_outbound)

    urltest_outbound = {
        "tag": "auto-proxy",
        "type": "urltest",
        "outbounds": all_tags,
        "url": "http://www.google.com/generate_204",
    }
    client_config["outbounds"].append(urltest_outbound)

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
        for t in args.add:
            nodes = add_node(nodes, t, protocols)

    if args.update:
        for t in args.update:
            nodes = update_node(nodes, t, protocols)

    if args.delete:
        for t in args.delete:
            nodes = delete_node(nodes, t)

    changed = (old_nodes != nodes)
    if changed:
        save_nodes(nodes)

        build(nodes, protocols_template, tls_template,
              server_config, client_config)

        apply_patches(server_config, client_config,
              protocols_template, tls_template)

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