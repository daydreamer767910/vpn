#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manage_users.py - Sing-box 用户管理脚本（安全 & 原子写入版）
功能：
- CLI / journal 用户管理
- 用户停用、逾期删除、恢复、延长有效期
- 服务端配置仅在实际变化时更新
- 客户端配置和订阅同步生成
- 日志循环写入，防止无限增长
"""

import json, os, shutil, uuid, secrets, random, string, argparse, subprocess, datetime, re, copy, logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

SPECIAL_OUTBOUNDS = {
    "direct",
    "block",
    "tor",
    "selector",
    "urltest",
}
# ------------------------
# 路径设置
# ------------------------
BASE_DIR = Path(__file__).resolve().parent
SINGBOX_DIR = BASE_DIR / "singbox"
JOURNAL_DB_USERS = BASE_DIR / "journal" / "db" / "users"
USERS_FILE = SINGBOX_DIR / "users.json"
NODES_FILE = SINGBOX_DIR / "nodes.json"
SERVER_CONFIG_FILE = SINGBOX_DIR / "server" / "config.json"
CLIENT_TEMPLATE_FILE = SINGBOX_DIR / "client" / "config.json"
CONFIG_SH = BASE_DIR / "config.sh"
LOG_FILE = BASE_DIR / "log" / "manage_users.log"

# 自动创建缺失目录
for path in [USERS_FILE, SERVER_CONFIG_FILE, CLIENT_TEMPLATE_FILE]:
    path.parent.mkdir(parents=True, exist_ok=True)
JOURNAL_DB_USERS.mkdir(parents=True, exist_ok=True)
(LOG_FILE.parent).mkdir(parents=True, exist_ok=True)

# ------------------------
# 日志配置
# ------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def ts_print(msg):
    logger.info(msg)

# ------------------------
# 命令行参数
# ------------------------
parser = argparse.ArgumentParser(description="Sing-box 用户管理脚本")
parser.add_argument("--nodes", nargs="*")
parser.add_argument("--add", nargs="*")
parser.add_argument("--delete", nargs="*")
parser.add_argument("--clear", action="store_true")
parser.add_argument("--sync", action="store_true", help="同步journal(在目录journal/db/users下)用户到singbox")
parser.add_argument("--enable", nargs="*")
parser.add_argument("--extend", type=int, help="恢复或新增用户时延长有效期，单位天")
parser.add_argument("--refresh", nargs="*", help="刷新指定用户uuid 和 password,同步到服务和客户端配置,但保留subscription token")
parser.add_argument("--apply", action="store_true", help="仅同步用户到服务端配置（不修改用户数据）")
args = parser.parse_args()

# ------------------------
# 工具函数
# ------------------------
def load_json(file_path, default=None):
    try:
        with open(file_path,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        ts_print(f"[WARN] 读取 {file_path} 失败: {e}")
        return default if default is not None else []

def save_json_atomic(file_path, data):
    tmp_file = file_path.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_file.replace(file_path)

def generate_password(length):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

def create_user(name, nodes, source="cli", expire_days=None):
    now = datetime.datetime.now()
    expire_at = (now + datetime.timedelta(days=expire_days)).isoformat() if expire_days else None

    return {
        "name": name,
        "uuid": str(uuid.uuid4()),
        "password": generate_password(20),
        "created_at": now.isoformat(),
        "enabled": True,
        "upload": 0,
        "download": 0,
        "traffic_limit": None,
        "subscription_token": secrets.token_urlsafe(32),#uuid.uuid4().hex,
        "source": source,
        "expire_at": expire_at,
        "nodes": nodes
    }

def parse_config_sh(sh_file: Path):
    if not sh_file.exists():
        ts_print("config.sh 不存在")
        return None, None
    cmd = f'''
    source "{sh_file}"
    if [ -n "$DOMAIN" ]; then echo FIRST_DOMAIN="$DOMAIN"; fi
    if [ -n "$SNI" ]; then echo REALITY_SNI="$SNI"; fi
    '''
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    first_domain = None
    reality_sni = None
    for line in result.stdout.splitlines():
        if line.startswith("FIRST_DOMAIN="):
            first_domain = line.split("=",1)[1].strip()
        elif line.startswith("REALITY_SNI="):
            reality_sni = line.split("=",1)[1].strip()
    if not first_domain: ts_print("⚠️ 没找到 DOMAIN")
    if not reality_sni: ts_print("⚠️ 没找到 SNI")
    return first_domain, reality_sni

def make_user_entry(protocol, user):
    protocol = protocol.lower()
    if protocol == "tuic": return {"name": user["name"], "uuid": user["uuid"], "password": user["password"]}
    elif protocol == "vless": return {"name": user["name"], "uuid": user["uuid"], "flow": "xtls-rprx-vision"}
    elif protocol == "hysteria2": return {"name": user["name"], "password": user["password"]}
    elif protocol == "shadowsocks": return {"method": "2022-blake3-aes-128-gcm", "password": user["password"]}
    return {}

def restart_container(name):
    try:
        subprocess.run(["docker","restart",name],check=True)
        ts_print(f"已重启容器 {name}")
    except subprocess.CalledProcessError as e:
        ts_print(f"[WARN] 重启容器失败: {e}")

def split_user_input(raw_input):
    return [n.strip() for n in re.split(r'[,\s;]+', raw_input) if n.strip()]

def cleanup_user_files(names):
    client_manage_dir = CLIENT_TEMPLATE_FILE.parent / "users"
    uploads_root = BASE_DIR / "journal" / "public" / "uploads"

    for name in names:
        # 删除管理目录的 JSON 配置
        manage_file = client_manage_dir / f"{name}.json"
        if manage_file.exists(): manage_file.unlink()

        # 删除 token txt
        sub_file = uploads_root / name / f"{name}_sub.token"
        if sub_file.exists(): sub_file.unlink()

def parse_and_validate(input_list, all_items, name):
    import warnings
    if input_list:
        values = [
            v.strip()
            for item in input_list
            for v in item.split(",")
            if v.strip()
        ]
        invalid = [v for v in values if v not in all_items]
        if invalid:
            warnings.warn(f"{name} not exist: {invalid}", RuntimeWarning)

        return values
    return list(all_items)

def resolve_user_nodes(node_names: list, all_nodes: list) -> list:
    """
    从 nodes.json 中解析出用户实际拥有的 node objects
    """
    node_set = set(node_names)

    return [
        node
        for node in all_nodes
        if node.get("tag") in node_set
    ]

def tag_belongs_to_user(tag, type_, user_node_names, all_nodes) -> bool:
    """
    判断 tag 是否属于用户 node 权限范围
    """

    user_nodes = resolve_user_nodes(user_node_names, all_nodes)

    for node in user_nodes:
        if tag in node.get(type_, []) :
            return True

    return False

def build_dynamic_outbounds(client_config):
    outbounds = client_config.get("outbounds", [])
    endpoints = client_config.get("endpoints") or []
    all_eptags = list(dict.fromkeys(
        item.get("tag")
        for item in endpoints
        if item.get("type") != "direct"
    ))
    # ------------------------
    # 收集 tag
    # ------------------------
    all_outtags = []

    for o in outbounds:
        tag = o.get("tag")
        if not tag:
            continue
        if o.get("type") == "direct":
            continue
        all_outtags.append(tag)

    # 去重（保持顺序）
    all_outtags = list(dict.fromkeys(all_outtags))

    if not all_outtags:
        return

    # ------------------------
    # all-selector（全集）
    # ------------------------
    all_selector = {
        "tag": "all-selector",
        "type": "selector",
        "outbounds": (all_eptags or []) + all_outtags,
        "default": all_outtags[0],
        "interrupt_exist_connections": False,
    }

    client_config["outbounds"].append(all_selector)  

def build_route(client_config):
    eps = client_config.get("endpoints", [])

    # 收集 wireguard endpoint 的 tag
    all_tags = list(dict.fromkeys(
        ep.get("tag")
        for ep in eps
        if ep.get("type") == "wireguard" and ep.get("tag")
    ))

    if not all_tags:
        return

    # 确保 route 存在
    route = client_config.setdefault("route", {})
    rules = route.setdefault("rules", [])

    # 构造规则
    rule = {
        "inbound": all_tags,
        "outbound": "direct"
    }

    # 插入到最前面（优先级最高）
    rules.insert(0, rule)
# ------------------------
# 主逻辑
# ------------------------
def main():
    first_domain, reality_sni = parse_config_sh(CONFIG_SH)
    if not first_domain:
        ts_print("FIRST_DOMAIN 未找到，脚本无法运行")
        return

    users = load_json(USERS_FILE, [])
    nodes = load_json(NODES_FILE, {}).get("nodes", [])
    all_nodes = {
        node.get("tag")
        for node in nodes
        if node.get("tag")
    }
    existing_names = {u["name"] for u in users}
    updated_users = set()
    users_updated = False
    config_updated = False
    force_apply = args.apply
    now = datetime.datetime.now()

    node_names = parse_and_validate(args.nodes, all_nodes, "node")
    # ------------------------
    # 自动更新 journal 用户
    # ------------------------
    if args.sync:
        journal_users = {f.stem for f in JOURNAL_DB_USERS.glob("*.json")}
        to_add = journal_users - existing_names
        if to_add:
            ts_print(f"[UPDATE] 发现新增用户: {', '.join(to_add)}")
            for name in to_add:
                new_user = create_user(name, node_names, source="journal")
                users.append(new_user)
                existing_names.add(name)
                updated_users.add(name)
            users_updated = True

    # ------------------------
    # CLI 删除 / 清空 / 新增
    # ------------------------
    if args.clear:
        all_users = {u["name"] for u in users}
        users = []
        existing_names = set()
        cleanup_user_files(all_users)
        ts_print("已清空所有用户")
        users_updated = True

    elif args.delete:
        del_names = {n.strip() for name in args.delete for n in split_user_input(name)}
        if del_names:
            users[:] = [u for u in users if u["name"] not in del_names]
            existing_names -= del_names
            cleanup_user_files(del_names)
            ts_print(f"已删除用户: {', '.join(del_names)}")
            users_updated = True

    elif args.add:
        names = []
        for name_str in args.add: names.extend(split_user_input(name_str))
        for name in names:
            if name not in existing_names:
                new_user = create_user(name, node_names, source="cli", expire_days=args.extend)
                users.append(new_user)
                existing_names.add(name)
                updated_users.add(name)
                ts_print(f"已添加用户: {name}")
            else:
                ts_print(f"用户 {name} 已存在，仍生成客户端配置")
                updated_users.add(name)
        if names: users_updated = True

    # ------------------------
    # 恢复用户逻辑
    # ------------------------
    if args.enable:
        enable_names = {n.strip() for name in args.enable for n in split_user_input(name)}
        for u in users:
            if u["name"] in enable_names and not u.get("enabled", True):
                u["enabled"] = True
                expire_at = u.get("expire_at")
                if args.extend:
                    expire_dt = datetime.datetime.fromisoformat(expire_at) if expire_at else now
                    if expire_dt < now: expire_dt = now
                    u["expire_at"] = (expire_dt + datetime.timedelta(days=args.extend)).isoformat()
                updated_users.add(u["name"])
        if enable_names: users_updated = True

    # ------------------------
    # 刷新用户 uuid / password和配置
    # ------------------------
    if args.refresh is not None:  # 用户指定了 --refresh
        refresh_names = []
        for name_str in args.refresh: refresh_names.extend(split_user_input(name_str))

        if refresh_names:
            # 刷新指定用户
            target_users = [u for u in users if u["name"] in refresh_names and u.get("enabled", True)]
        else:
            # 空列表，刷新所有启用用户
            target_users = [u for u in users if u.get("enabled", True)]

        if target_users:
            for u in target_users:
                old_uuid = u["uuid"]
                #old_pass = u["password"]
                u["uuid"] = str(uuid.uuid4())
                u["password"] = generate_password(20)
                updated_users.add(u["name"])
                ts_print(f"用户 {u['name']} uuid/password 已刷新: {old_uuid} -> {u['uuid']}")
            users_updated = True
    # ------------------------
    # 停用到期 / 宽限期删除
    # ------------------------
    delete_users = set()
    for u in users:
        expire_at = u.get("expire_at")
        if expire_at:
            expire_dt = datetime.datetime.fromisoformat(expire_at)
            if now >= expire_dt and u.get("enabled", True):
                u["enabled"] = False
                updated_users.add(u["name"])
                ts_print(f"用户到期停用: {u['name']}")
                users_updated = True
            overdue_dt = expire_dt + datetime.timedelta(days=7)
            if now >= overdue_dt:
                delete_users.add(u["name"])

    if delete_users:
        users[:] = [u for u in users if u["name"] not in delete_users]
        existing_names -= delete_users
        cleanup_user_files(delete_users)
        ts_print(f"逾期删除用户: {', '.join(delete_users)}")
        users_updated = True

    # ------------------------
    # 保存 users.json（原子写入）
    # ------------------------
    if users_updated:
        save_json_atomic(USERS_FILE, users)
        ts_print(f"用户列表已更新 -> {USERS_FILE}")

    # ------------------------
    # 更新服务端配置
    # ------------------------
    server_config = load_json(SERVER_CONFIG_FILE)
    if not server_config: return

    for inbound in server_config.get("inbounds", []):
        protocol = inbound.get("type","").lower()
        tag = inbound.get("tag")
        old_users = inbound.get("users",[])
        new_users = [
            make_user_entry(protocol, u) 
            for u in users 
            if u.get("enabled", True) and tag_belongs_to_user(tag,"inbound_tags",u.get("nodes"),nodes)
        ]
        if force_apply or old_users != new_users:
            inbound["users"] = new_users
            config_updated = True

    # ------------------------
    # 客户端配置和订阅
    # ------------------------
    client_template = load_json(CLIENT_TEMPLATE_FILE)
    if not client_template: return
    client_manage_dir = CLIENT_TEMPLATE_FILE.parent / "users"
    client_manage_dir.mkdir(parents=True,exist_ok=True)

    for user in users:
        if user["name"] not in updated_users: continue
        if user.get("enabled", True):
            new_config = copy.deepcopy(client_template)
            node_tags = user.get("nodes")
            # 真正过滤 outbounds，不属于用户 tags 的删除
            filtered_outbounds = []
            for outbound in new_config.get("outbounds", []):
                # direct
                if outbound.get("type") == "direct":
                    filtered_outbounds.append(outbound)
                    continue
                tag = outbound.get("tag","")
                # 订阅
                if tag_belongs_to_user(tag,"subscription_tags",user.get("nodes"),nodes):
                    filtered_outbounds.append(outbound)
                    continue
                # 跳过不属于当前 outbound 的用户
                if not tag_belongs_to_user(tag,"inbound_tags",user.get("nodes"),nodes):
                    continue
                protocol = outbound.get("type","").lower()
                if protocol == "tuic":
                    outbound["uuid"] = user["uuid"]
                    outbound["password"] = user["password"]
                elif protocol == "vless":
                    outbound["uuid"] = user["uuid"]
                elif protocol in ["hysteria2","shadowsocks"]:
                    outbound["password"] = user["password"]
                if first_domain:
                    if "server" in outbound: outbound["server"] = first_domain
                    if "tls" in outbound and isinstance(outbound["tls"],dict):
                        if "reality" in outbound["tls"] and reality_sni:
                            outbound["tls"]["server_name"] = reality_sni
                        else:
                            outbound["tls"]["server_name"] = first_domain
                # 保留这个 outbound
                filtered_outbounds.append(outbound)
            # 替换 new_config 的 outbounds
            new_config["outbounds"] = filtered_outbounds

            filtered_eps = []
            for ep in new_config.get("endpoints", []):
                tag = ep.get("tag")
                # 订阅
                if tag_belongs_to_user(tag,"subscription_tags",user.get("nodes"),nodes):
                    filtered_eps.append(ep)
                    continue
                if not tag_belongs_to_user(tag,"endpoint_tags",user.get("nodes"),nodes):
                    continue
                filtered_eps.append(ep)
            new_config["endpoints"] = filtered_eps

            build_dynamic_outbounds(new_config)
            build_route(new_config)

            manage_file = client_manage_dir / f"{user['name']}.json"
            save_json_atomic(manage_file,new_config)
            ts_print(f"已生成客户端配置 -> {manage_file}")

        # 发布 token
        user_publish_dir = BASE_DIR / "journal" / "public" / "uploads" / user["name"]
        user_publish_dir.mkdir(parents=True,exist_ok=True)
        sub_file = user_publish_dir / f"{user['name']}_sub.token"
        if not user.get("enabled",True):
            sub_file.write_text("⚠️ 账号已停用",encoding="utf-8")
            ts_print(f"已生成停用提示订阅文件: {sub_file}")
        else:
            sub_url = f"https://{first_domain}/sub/{user['subscription_token']}"
            sub_file.write_text(sub_url,encoding="utf-8")
            ts_print(f"已发布订阅地址: {sub_file} -> {sub_url}#{first_domain}-{user["name"]}")
    # ------------------------
    # 重启服务(放在最后一步)
    # ------------------------   
    if config_updated:
        shutil.copy(SERVER_CONFIG_FILE, str(SERVER_CONFIG_FILE)+".bak")
        save_json_atomic(SERVER_CONFIG_FILE, server_config)
        ts_print(f"服务端用户已更新 -> {SERVER_CONFIG_FILE}")
        restart_container("singbox-server")
    else:
        ts_print("服务器配置无变更，无需重启容器")

    ts_print("所有操作完成！")

if __name__ == "__main__":
    main()