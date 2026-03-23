#!/usr/bin/env python3
import os
import json
import copy
from pathlib import Path
import argparse

# -----------------------
# 基础工具
# -----------------------
BASE_DIR = Path(__file__).resolve().parent

def load_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ JSON解析失败: {path} -> {e}")
        return {}

def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

# -----------------------
# 占位符替换
# -----------------------
def fill_placeholders(obj, tls_templates=None, node_tag=None, user=None, _depth=0, _max_depth=10):
    if _depth > _max_depth:
        return obj

    if isinstance(obj, dict):
        return {k: fill_placeholders(v, tls_templates, node_tag, user, _depth+1) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fill_placeholders(i, tls_templates, node_tag, user, _depth+1) for i in obj]
    elif isinstance(obj, str):
        # $tls:xxx
        if obj.startswith("$tls:"):
            key = obj[5:]
            return copy.deepcopy(tls_templates.get(key, obj))

        # $env:VAR:default
        elif obj.startswith("$env:"):
            parts = obj[5:].split(":", 1)
            var = parts[0]
            default = parts[1] if len(parts) > 1 else ""
            return os.environ.get(var, default)

        # $tag
        elif "$tag" in obj and node_tag:
            return obj.replace("$tag", node_tag)

        # $user:xxx
        elif obj.startswith("$user:") and user:
            return user.get(obj[6:], obj)

        else:
            return obj
    else:
        return obj

# -----------------------
# 确保 direct outbound 存在
# -----------------------
def ensure_direct_outbound(config):
    outbounds = config.setdefault("outbounds", [])

    exists = any(ob.get("tag") == "direct" for ob in outbounds)

    if not exists:
        outbounds.append({
            "tag": "direct",
            "type": "direct"
        })

# -----------------------
# 主逻辑
# -----------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs="*")
    parser.add_argument("--update", nargs="*")
    parser.add_argument("--delete", nargs="*")
    parser.add_argument("--protocols", nargs="*")
    parser.add_argument("--server_port", nargs="*")
    parser.add_argument("--template-dir", default="template")

    args = parser.parse_args()

    if not args.add and not args.update and not args.delete:
        print("❌ 没有操作（--add/--update/--delete）")
        return

    template_dir = BASE_DIR / args.template_dir

    protocols_template = load_json(template_dir / "protocols.json")
    tls_template = load_json(template_dir / "tls.json")
    route_template = load_json(template_dir / "route.json")
    dns_template = load_json(template_dir / "dns.json")
    endpoints_template = load_json(template_dir / "endpoints.json")

    if not protocols_template:
        print("❌ protocols.json 为空")
        return

    # 输出路径
    server_path = BASE_DIR / "singbox/server/config.json"
    client_path = BASE_DIR / "singbox/client/config.json"

    old_server = load_json(server_path)
    old_client = load_json(client_path)

    # 保留旧配置
    server_config = {
        "inbounds": copy.deepcopy(old_server.get("inbounds", [])),
        "outbounds": copy.deepcopy(old_server.get("outbounds", [])),
        "route": route_template.get("route-server", {}),
        "dns": dns_template.get("dns-server", {}),
        "endpoints": endpoints_template
    }

    client_config = {
        "inbounds": copy.deepcopy(old_client.get("inbounds", [])),
        "outbounds": copy.deepcopy(old_client.get("outbounds", [])),
        "route": route_template.get("route-client", {}),
        "dns": dns_template.get("dns-client", {})
    }

    # 解析 protocols 参数（支持逗号）
    if args.protocols:
        protocols = []
        for p in args.protocols:
            protocols.extend(p.split(","))
    else:
        protocols = list(protocols_template.keys())

    ports = [int(p) for p in args.server_port] if args.server_port else []

    # -----------------------
    # 核心函数
    # -----------------------
    def add_or_update_node(tag, update=False):
        # 校验 tag 是否重复（只针对新增）
        if not update:
            existing_tags = {x.get("tag") for x in server_config.get("inbounds", [])}
            if tag in existing_tags:
                print(f"❌ 节点 {tag} 已存在，跳过新增")
                return

        for i, proto_name in enumerate(protocols):
            if proto_name not in protocols_template:
                print(f"[WARN] 协议不存在: {proto_name}")
                continue

            proto_def = protocols_template[proto_name]

            # ===== inbound =====
            if "inbound" in proto_def:
                inbound = copy.deepcopy(proto_def["inbound"])
                # 通过占位符替换 tag
                inbound = fill_placeholders(inbound, tls_template, tag)

                if i < len(ports) and "listen_port" in inbound:
                    inbound["listen_port"] = ports[i]

                if update:
                    server_config["inbounds"] = [
                        x for x in server_config["inbounds"]
                        if x.get("tag") != tag
                    ]

                server_config["inbounds"].append(inbound)

            # ===== outbound =====
            if "outbound" in proto_def:
                outbound = copy.deepcopy(proto_def["outbound"])
                outbound["server"] = os.environ.get("VPS_HOST", outbound.get("server"))

                outbound = fill_placeholders(outbound, tls_template, tag)

                if i < len(ports) and "server_port" in outbound:
                    outbound["server_port"] = ports[i]

                if update:
                    client_config["outbounds"] = [
                        x for x in client_config["outbounds"]
                        if x.get("tag") != tag
                    ]

                client_config["outbounds"].append(outbound)

    # -----------------------
    # 执行操作
    # -----------------------
    if args.add:
        for tag in args.add:
            add_or_update_node(tag, update=False)
            print(f"✅ 新增节点: {tag}")

    if args.update:
        for tag in args.update:
            add_or_update_node(tag, update=True)
            print(f"♻️ 更新节点: {tag}")

    if args.delete:
        for tag in args.delete:
            server_config["inbounds"] = [
                x for x in server_config["inbounds"] if x.get("tag") != tag
            ]
            client_config["outbounds"] = [
                x for x in client_config["outbounds"] if x.get("tag") != tag
            ]
            print(f"🗑 删除节点: {tag}")

    # -----------------------
    # 确保 direct 存在
    # -----------------------
    ensure_direct_outbound(server_config)
    ensure_direct_outbound(client_config)

    # -----------------------
    # 保存
    # -----------------------
    print("server inbounds:", len(server_config["inbounds"]))
    print("client outbounds:", len(client_config["outbounds"]))

    save_json(server_path, server_config)
    save_json(client_path, client_config)

    print("✅ 已生成配置")

# -----------------------
if __name__ == "__main__":
    main()