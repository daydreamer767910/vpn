#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manage_users.py - Sing-box 用户管理脚本（全功能本地时间版）
功能：
- CLI / journal 用户管理
- 用户停用、逾期删除、恢复、延长有效期
- 服务器配置仅在实际变化时更新，避免无意义重启
- 客户端配置和订阅同步生成
- 日志循环写入，防止无限增长
"""

import json, os, shutil, uuid, random, string, argparse, subprocess, datetime, re, copy, logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ------------------------
# 路径设置
# ------------------------
BASE_DIR = Path(__file__).resolve().parent
SINGBOX_DIR = BASE_DIR / "singbox"
JOURNAL_DB_USERS = BASE_DIR / "journal" / "db" / "users"
USERS_FILE = SINGBOX_DIR / "users.json"
SERVER_CONFIG_FILE = SINGBOX_DIR / "server" / "config.json"
CLIENT_TEMPLATE_FILE = SINGBOX_DIR / "client" / "config.json"
CONFIG_SH = BASE_DIR / "config.sh"
LOG_FILE = BASE_DIR / "log" / "manage_users.log"

# 自动创建缺失目录
for path in [USERS_FILE, SERVER_CONFIG_FILE, CLIENT_TEMPLATE_FILE]:
    path.parent.mkdir(parents=True, exist_ok=True)
JOURNAL_DB_USERS.mkdir(parents=True, exist_ok=True)

# ------------------------
# 日志配置（循环写入 + 控制台）
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
parser.add_argument("--protocols", nargs="*", default=["tuic","vless","hysteria2","shadowsocks"])
parser.add_argument("--password_length", type=int, default=20)
parser.add_argument("--add", nargs="*")
parser.add_argument("--delete", nargs="*")
parser.add_argument("--clear", action="store_true")
parser.add_argument("--update", action="store_true")
parser.add_argument("--enable", nargs="*")
parser.add_argument("--extend", type=int, help="恢复用户时延长有效期，单位天")
parser.add_argument("--expire_grace_days", type=int, default=5)
args = parser.parse_args()

# ------------------------
# 工具函数
# ------------------------
def load_json(file_path, default=None):
    try:
        with open(file_path,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else []

def save_json(file_path,data):
    with open(file_path,"w",encoding="utf-8") as f:
        json.dump(data,f,indent=2,ensure_ascii=False)

def generate_password(length):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

def create_user(name,password_length,source="cli",expire_days=None):
    now = datetime.datetime.now()  # 本地时间
    expire_at = (now + datetime.timedelta(days=expire_days)).isoformat() if expire_days else None
    return {
        "name": name,
        "uuid": str(uuid.uuid4()),
        "password": generate_password(password_length),
        "created_at": now.isoformat(),
        "enabled": True,
        "traffic_limit": None,
        "subscription_token": uuid.uuid4().hex,
        "source": source,
        "expire_at": expire_at
    }

def parse_config_sh(sh_file: Path):
    if not sh_file.exists():
        ts_print("config.sh 不存在")
        return None, None
    cmd = f'''
    source "{sh_file}"
    if [ -n "${{DOMAINLIST[0]}}" ]; then echo FIRST_DOMAIN="${{DOMAINLIST[0]}}"; fi
    if [ -n "$SNI" ]; then echo REALITY_SNI="$SNI"; fi
    '''
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    first_domain = None
    reality_sni = None
    for line in result.stdout.splitlines():
        if line.startswith("FIRST_DOMAIN="):
            first_domain = line.split("=", 1)[1].strip()
        elif line.startswith("REALITY_SNI="):
            reality_sni = line.split("=", 1)[1].strip()
    if not first_domain: ts_print("⚠️ 没找到 DOMAINLIST[0]")
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

def cleanup_user_files(names, first_domain):
    client_manage_dir = CLIENT_TEMPLATE_FILE.parent / "users"
    uploads_root = BASE_DIR / "journal" / "public" / "uploads"
    for name in names:
        manage_file = client_manage_dir / f"{first_domain}-{name}.json"
        if manage_file.exists(): manage_file.unlink()
        user_publish_dir = uploads_root / name
        if user_publish_dir.exists(): shutil.rmtree(user_publish_dir)

# ------------------------
# 主逻辑
# ------------------------
def main():
    first_domain, reality_sni = parse_config_sh(CONFIG_SH)
    if not first_domain:
        ts_print("FIRST_DOMAIN 未找到，脚本无法运行")
        return

    users = load_json(USERS_FILE, [])
    existing_names = {u["name"] for u in users}
    updated_users = set()
    users_updated = False
    config_updated = False
    now = datetime.datetime.now()  # 本地时间

    # ------------------------
    # 自动更新 journal 用户
    # ------------------------
    if args.update:
        journal_users = {f.stem for f in JOURNAL_DB_USERS.glob("*.json")}
        to_add = journal_users - existing_names
        if to_add:
            ts_print(f"[UPDATE] 发现新增用户: {', '.join(to_add)}")
            for name in to_add:
                new_user = create_user(name, args.password_length, source="journal")
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
        cleanup_user_files(all_users, first_domain)
        ts_print("已清空所有用户")
        users_updated = True

    elif args.delete:
        del_names = {n.strip() for name in args.delete for n in split_user_input(name)}
        if del_names:
            users[:] = [u for u in users if u["name"] not in del_names]
            existing_names -= del_names
            cleanup_user_files(del_names, first_domain)
            ts_print(f"已删除用户: {', '.join(del_names)}")
            users_updated = True

    elif args.add:
        names = []
        for name_str in args.add: names.extend(split_user_input(name_str))
        for name in names:
            if name in existing_names:
                ts_print(f"用户 {name} 已存在，跳过。")
                continue
            new_user = create_user(name, args.password_length)
            users.append(new_user)
            existing_names.add(name)
            updated_users.add(name)
            ts_print(f"已添加用户: {name}")
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
                if expire_at and args.extend:
                    expire_dt = datetime.datetime.fromisoformat(expire_at)
                    if expire_dt < now: expire_dt = now
                    u["expire_at"] = (expire_dt + datetime.timedelta(days=args.extend)).isoformat()
                updated_users.add(u["name"])
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
            overdue_dt = expire_dt + datetime.timedelta(days=args.expire_grace_days)
            if now >= overdue_dt:
                delete_users.add(u["name"])

    if delete_users:
        users[:] = [u for u in users if u["name"] not in delete_users]
        existing_names -= delete_users
        cleanup_user_files(delete_users, first_domain)
        ts_print(f"逾期删除用户: {', '.join(delete_users)}")
        users_updated = True

    # ------------------------
    # 保存 users.json
    # ------------------------
    if users_updated:
        save_json(USERS_FILE, users)
        ts_print(f"用户列表已更新 -> {USERS_FILE}")

    # ------------------------
    # 更新服务端配置
    # ------------------------
    server_config = load_json(SERVER_CONFIG_FILE)
    if not server_config: return

    for inbound in server_config.get("inbounds", []):
        protocol = inbound.get("type","").lower()
        old_users = inbound.get("users",[])
        new_users = [make_user_entry(protocol,u) for u in users if u.get("enabled",True)]
        if old_users != new_users:
            inbound["users"] = new_users
            config_updated = True

    if config_updated:
        shutil.copy(SERVER_CONFIG_FILE, str(SERVER_CONFIG_FILE)+".bak")
        save_json(SERVER_CONFIG_FILE, server_config)
        ts_print(f"服务端用户已更新 -> {SERVER_CONFIG_FILE}")
        restart_container("singbox-server")
    else:
        ts_print("服务器配置无变更，无需重启容器")

    # ------------------------
    # 客户端配置和订阅
    # ------------------------
    client_template = load_json(CLIENT_TEMPLATE_FILE)
    if not client_template: return
    client_manage_dir = CLIENT_TEMPLATE_FILE.parent / "users"
    client_manage_dir.mkdir(parents=True,exist_ok=True)

    for user in users:
        if user["name"] not in updated_users: continue
        new_config = copy.deepcopy(client_template)
        for outbound in new_config.get("outbounds", []):
            protocol = outbound.get("type","").lower()
            if protocol not in [p.lower() for p in args.protocols]: continue
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

        manage_file = client_manage_dir / f"{first_domain}-{user['name']}.json"
        save_json(manage_file,new_config)
        ts_print(f"已生成客户端配置 -> {manage_file}")

        user_publish_dir = BASE_DIR / "journal" / "public" / "uploads" / user["name"]
        user_publish_dir.mkdir(parents=True,exist_ok=True)
        publish_file = user_publish_dir / f"{first_domain}-{user['name']}.json"
        shutil.copy(manage_file,publish_file)
        sub_file = user_publish_dir / f"{first_domain}-{user['name']}.txt"
        if not user.get("enabled",True):
            sub_file.write_text("⚠️ 账号已停用",encoding="utf-8")
            ts_print(f"已生成停用提示订阅文件: {sub_file}")
        else:
            sub_url = f"https://{first_domain}/sub/{user['subscription_token']}"
            sub_file.write_text(sub_url,encoding="utf-8")
            ts_print(f"已发布订阅地址: {sub_file} -> {sub_url}")

    ts_print("所有操作完成！")

if __name__ == "__main__":
    main()