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
# 端口管理
# -----------------------
def collect_used_ports(nodes):
    used = set()
    for n in nodes:
        for p in n.get("ports", []):
            used.add(p)
    return used


def allocate_ports(nodes, count):
    used = collect_used_ports(nodes)
    ports = []
    p = PORT_START

    while len(ports) < count and p <= PORT_END:
        if p not in used:
            ports.append(p)
            used.add(p)
        p += 1

    if len(ports) < count:
        raise RuntimeError("端口不够用")

    return ports


# -----------------------
# 节点操作
# -----------------------
def add_node(nodes, tag, protocols, ports):
    if any(n["tag"] == tag for n in nodes):
        print(f"❌ 已存在 {tag}")
        return nodes

    if not ports:
        ports = allocate_ports(nodes, len(protocols))
        print(f"⚡ 自动端口 {ports}")

    nodes.append({
        "tag": tag,
        "protocols": protocols,
        "ports": ports
    })
    return nodes


def update_node(nodes, tag, protocols=None, ports=None):
    for n in nodes:
        if n["tag"] == tag:
            if protocols:
                n["protocols"] = protocols
                if not ports:
                    ports = allocate_ports(nodes, len(protocols))

            if ports:
                n["ports"] = ports

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
            arr[i] = item   # 覆盖
            return

    arr.append(item)  # 不存在才新增

# -----------------------
# 构建 config
# -----------------------
def build(nodes, protocols_template, tls_template,
          server_config, client_config):
    # 哪些协议需要双向处理（server + client）
    BIDIRECTIONAL_PROTOCOLS = {
        "direct",
        # "socks",
        # "http",
        # 以后需要再加
    }

    for node in nodes:
        tag = node["tag"]
        protos = node["protocols"]
        ports = node["ports"]

        for i, proto_name in enumerate(protos):
            if proto_name not in protocols_template:
                continue

            proto = protocols_template[proto_name]
            is_bi = proto_name in BIDIRECTIONAL_PROTOCOLS
            # inbound
            if "inbound" in proto:
                inbound = copy.deepcopy(proto["inbound"])
                inbound = fill_placeholders(inbound, tls_template, tag)

                if i < len(ports) and "listen_port" in inbound:
                    inbound["listen_port"] = ports[i]

                upsert_by_tag(server_config["inbounds"], inbound)
                if is_bi:
                    upsert_by_tag(client_config["inbounds"], inbound)
            # outbound
            if "outbound" in proto:
                outbound = copy.deepcopy(proto["outbound"])

                outbound = fill_placeholders(outbound, tls_template, tag)

                if i < len(ports) and "server_port" in outbound:
                    outbound["server_port"] = ports[i]

                #client_config["outbounds"].append(outbound)
                upsert_by_tag(client_config["outbounds"],outbound)
                if is_bi:
                    #server_config["outbounds"].append(outbound)
                    upsert_by_tag(server_config["outbounds"],outbound)

def ensure_defaults(config):
    # ---------- log ----------
    if "log" not in config:
        config["log"] = {"level": "info"}


# -----------------------
# 构建客户端 Selector / URLTest
# -----------------------
def build_dynamic_outbounds(client_config):
    # 收集普通节点 tag
    all_tags = [o.get("tag") for o in client_config.get("outbounds", []) if o.get("tag") != "direct"]

    if not all_tags:
        return  # 没有节点就不生成

    # ----- Selector -----
    selector_outbound = {
        "tag": "auto-selector",
        "type": "Selector",
        "outbounds": all_tags,
        "strategy": "priority"
    }
    client_config["outbounds"].append(selector_outbound)

    # ----- URLTest -----
    urltest_outbound = {
        "tag": "auto-proxy",
        "type": "urltest",
        "outbounds": all_tags,
        "url": "http://www.google.com/generate_204",
        "interval": 300
    }
    client_config["outbounds"].append(urltest_outbound)

# -----------------------
# 防火墙同步（UFW）
# -----------------------
def apply_firewall(nodes):
    ports = sorted(collect_used_ports(nodes))

    print("🔥 同步防火墙...")

    os.system("ufw --force reset")
    os.system("ufw default deny incoming")
    os.system("ufw allow 22/tcp")

    for p in ports:
        os.system(f"ufw allow {p}")
        os.system(f"ufw allow {p}/udp")

    os.system("ufw --force enable")

    print(f"✅ 已开放端口: {ports}")

def lint_config(server_config, client_config):
    errors = []
    
    # -----------------------
    # 收集 tags
    # -----------------------
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

    # endpoints
    for ep in server_config.get("endpoints", []):
        if "tag" in ep:
            out_tags.add(ep["tag"])

    # -----------------------
    # 检查 route 引用
    # -----------------------
    def check_route(route):
        if not route:
            return
        for r in route.get("rules", []):
            ob = r.get("outbound")
            if ob and ob not in out_tags:
                errors.append(f"❌ route 引用了不存在的 outbound: {ob}")

    check_route(server_config.get("route"))
    check_route(client_config.get("route"))

    # -----------------------
    # 检查 dns
    # -----------------------
    def check_dns(dns):
        if not dns:
            return

        for s in dns.get("servers", []):
            detour = s.get("detour")
            if detour and detour not in out_tags:
                errors.append(f"❌ dns detour 不存在: {detour}")

    check_dns(server_config.get("dns"))
    check_dns(client_config.get("dns"))

    # -----------------------
    # 检查 endpoints
    # -----------------------
    endpoints = server_config.get("endpoints", [])
    for ep in endpoints:
        ob = ep.get("outbound")
        if ob and ob not in out_tags:
            errors.append(f"❌ endpoint 引用了不存在的 outbound: {ob}")

    # -----------------------
    # 检查端口冲突
    # -----------------------
    ports = set()
    for i in server_config.get("inbounds", []):
        p = i.get("listen_port")
        if p:
            if p in ports:
                errors.append(f"❌ 端口冲突: {p}")
            ports.add(p)

    # -----------------------
    # 输出结果
    # -----------------------
    if errors:
        print("\n".join(errors))
        raise RuntimeError("❌ 配置检查失败")
    else:
        print("✅ 配置检查通过")

# -----------------------
# 主函数
# -----------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs="*")
    parser.add_argument("--update", nargs="*")
    parser.add_argument("--delete", nargs="*")
    parser.add_argument("--protocols", nargs="*")
    parser.add_argument("--server_port", nargs="*")
    parser.add_argument("--apply-firewall", action="store_true")
    parser.add_argument("--lint", action="store_true")

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
    ensure_defaults(server_config)
    ensure_defaults(client_config)

    nodes = load_nodes()
    old_nodes = copy.deepcopy(nodes)

    protocols = []
    if args.protocols:
        for p in args.protocols:
            protocols.extend(p.split(","))
    else:
        protocols = list(protocols_template.keys())

    ports = []
    if args.server_port:
        for p in args.server_port:
            ports.extend([int(x) for x in p.split(",")])

    # 操作
    if args.add:
        for t in args.add:
            nodes = add_node(nodes, t, protocols, ports)

    if args.update:
        for t in args.update:
            nodes = update_node(nodes, t, protocols, ports)

    if args.delete:
        for t in args.delete:
            nodes = delete_node(nodes, t)

    changed = (old_nodes != nodes)
    if changed:
        save_nodes(nodes)

        # 构建 config
        build(nodes, protocols_template, tls_template,
            server_config, client_config)

        build_dynamic_outbounds(client_config)

        save_json(server_path, server_config)
        save_json(client_path, client_config)
        print("✅ 已更新配置")

    # 防火墙
    if args.apply_firewall:
        apply_firewall(nodes)
        print("✅ 完成防火墙更新")

    if args.lint:
        server_config = load_json(server_path)
        client_config = load_json(client_path)
        lint_config(server_config, client_config)
        return

if __name__ == "__main__":
    main()