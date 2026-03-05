#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manage_users.py - Sing-box 用户管理脚本（跨平台 UTF-8，支持 Reality SNI）
优化版：函数化、去重复、增强可读性与扩展性
发布目录只生成新增/更新用户
"""

import json
import os
import shutil
import uuid
import random
import string
import argparse
import subprocess
import datetime
import re
import copy
import logging
from pathlib import Path

# ------------------------
# 配置日志
# ------------------------
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# ------------------------
# 命令行参数
# ------------------------
parser = argparse.ArgumentParser(description="整合 Sing-box 用户管理脚本")
parser.add_argument("--protocols", nargs="*", default=["tuic", "vless", "hysteria2", "shadowsocks"],
                    help="指定需要更新的协议")
parser.add_argument("--password_length", type=int, default=20, help="随机密码长度")
parser.add_argument("--add", nargs="*", help="新增用户，空格或逗号分隔")
parser.add_argument("--delete", nargs="*", help="删除指定用户名，空格或逗号分隔")
parser.add_argument("--clear", action="store_true", help="清空所有用户")
parser.add_argument("--auto_check_journal", action="store_true",
                    help="检查 journal/db/users/ 是否有新增/删除用户，并同步")
args = parser.parse_args()

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

# 自动创建缺失目录
for path in [USERS_FILE, SERVER_CONFIG_FILE, CLIENT_TEMPLATE_FILE]:
    path.parent.mkdir(parents=True, exist_ok=True)
JOURNAL_DB_USERS.mkdir(parents=True, exist_ok=True)

# ------------------------
# 工具函数
# ------------------------
def ts_print(msg):
    logging.info(msg)

def load_json(file_path, default=None):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else []

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def generate_password(length):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

def create_user(name, password_length):
    return {
        "name": name,
        "uuid": str(uuid.uuid4()),
        "password": generate_password(password_length),
        "created_at": datetime.datetime.utcnow().isoformat(),
        "enabled": True,
        "traffic_limit": None,
        "subscription_token": uuid.uuid4().hex
    }

def parse_config_sh(sh_file):
    first_domain = None
    reality_sni = None
    if sh_file.exists():
        for line in sh_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DOMAINLIST"):
                m = re.search(r'\((.*?)\)', line)
                if m:
                    items = m.group(1).replace('"', '').split()
                    if items:
                        first_domain = items[0]
            elif line.startswith("SNI="):
                reality_sni = line.split("=",1)[1].strip().strip('"')
    if not first_domain:
        ts_print("⚠️ config.sh 未找到 DOMAINLIST 或为空，客户端 server 和 TLS server_name 不会修改。")
    if not reality_sni:
        ts_print("⚠️ config.sh 未找到 SNI，Reality 模式 server_name 不会修改。")
    return first_domain, reality_sni

def make_user_entry(protocol, user):
    protocol = protocol.lower()
    if protocol == "tuic":
        return {"name": user["name"], "uuid": user["uuid"], "password": user["password"]}
    elif protocol == "vless":
        return {"name": user["name"], "uuid": user["uuid"], "flow": "xtls-rprx-vision"}
    elif protocol == "hysteria2":
        return {"name": user["name"], "password": user["password"]}
    elif protocol == "shadowsocks":
        return {"method": "2022-blake3-aes-128-gcm", "password": user["password"]}
    return {}

def restart_container(name):
    try:
        subprocess.run(["docker", "restart", name], check=True)
        ts_print(f"已重启容器 {name}")
    except subprocess.CalledProcessError as e:
        ts_print(f"[WARN] 重启容器失败: {e}")

def split_user_input(raw_input):
    return [n.strip() for n in re.split(r'[,\s;]+', raw_input) if n.strip()]

# ------------------------
# 主逻辑
# ------------------------
def main():
    first_domain, reality_sni = parse_config_sh(CONFIG_SH)
    
    # 读取用户列表
    users = load_json(USERS_FILE, [])

    existing_names = {u["name"] for u in users}

    # ------------------------
    # 记录新增或更新用户
    # ------------------------
    updated_users = set()

    # 轮询 journal/db/users
    if args.auto_check_journal:
        journal_users = {f.stem for f in JOURNAL_DB_USERS.glob("*.json")}
        current_users = existing_names
        to_add = journal_users - current_users
        #to_del = current_users - journal_users

        if to_add:
            ts_print(f"[JOURNAL] 发现新增用户: {', '.join(to_add)}")
            for name in to_add:
                new_user = create_user(name, args.password_length)
                users.append(new_user)
                existing_names.add(name)
                updated_users.add(name)
                ts_print(f"已添加用户: {name}, 订阅号: {new_user['subscription_token']}")
        #if to_del:
            #ts_print(f"[JOURNAL] 发现删除用户: {', '.join(to_del)}")
            #users = [u for u in users if u["name"] not in to_del]
            #existing_names -= to_del
        #if to_add or to_del:
            save_json(USERS_FILE, users)
            ts_print("[JOURNAL] 已同步用户到 users.json")
        else:
            #ts_print("======nothing need to sync, done")
            return
    # 删除或清空用户
    elif args.clear:
        users = []
        existing_names = set()
        ts_print("已清空所有用户")
        save_json(USERS_FILE, users)
    elif args.delete:
        del_names = {n.strip() for name in args.delete for n in split_user_input(name)}
        users = [u for u in users if u["name"] not in del_names]
        existing_names -= del_names
        ts_print(f"已删除用户: {', '.join(del_names)}")
        save_json(USERS_FILE, users)
    # 批量新增用户
    elif args.add:
        # 把参数解析成用户名列表
        names = []
        for name_str in args.add:
            names.extend(split_user_input(name_str))
        
        added_count = 0
        for name in names:
            if name in existing_names:
                ts_print(f"用户 {name} 已存在，跳过。")
                continue
            new_user = create_user(name, args.password_length)
            users.append(new_user)
            existing_names.add(name)
            updated_users.add(name)
            added_count += 1
            ts_print(f"已添加用户: {name}, 订阅号: {new_user['subscription_token']}")

        if added_count > 0:
            save_json(USERS_FILE, users)
            ts_print(f"已更新用户清单 -> {USERS_FILE}, 新增 {added_count} 个用户")
        else:
            ts_print("没有新增用户。")
            return

    # 更新服务端配置
    server_config = load_json(SERVER_CONFIG_FILE)
    if not server_config:
        ts_print(f"读取服务端配置失败: {SERVER_CONFIG_FILE}")
        return
    
    for inbound in server_config.get("inbounds", []):
        protocol = inbound.get("type", "").lower()
        if protocol not in [p.lower() for p in args.protocols]:
            inbound["users"] = []
            ts_print(f"{protocol}禁用, 清除其用户")
        else:
            inbound["users"] = [make_user_entry(protocol, u) for u in users]
            ts_print(f"更新 {protocol} 用户: {', '.join(u['name'] for u in users)}")
    shutil.copy(SERVER_CONFIG_FILE, str(SERVER_CONFIG_FILE) + ".bak")
    ts_print(f"已备份服务端配置 -> {SERVER_CONFIG_FILE}.bak")
    save_json(SERVER_CONFIG_FILE, server_config)
    ts_print(f"服务端用户已更新 -> {SERVER_CONFIG_FILE}")
    restart_container("singbox-server")

    # 生成客户端配置
    client_template = load_json(CLIENT_TEMPLATE_FILE)
    if not client_template:
        ts_print(f"读取客户端模板失败: {CLIENT_TEMPLATE_FILE}")
        return
    client_manage_dir = CLIENT_TEMPLATE_FILE.parent / "users"
    client_manage_dir.mkdir(parents=True, exist_ok=True)

    for user in users:
        # 只生成新增/更新用户
        if user["name"] not in updated_users:
            continue
        # 管理目录始终生成
        new_config = copy.deepcopy(client_template)
        updated = False
        for outbound in new_config.get("outbounds", []):
            protocol = outbound.get("type", "").lower()
            if protocol not in [p.lower() for p in args.protocols]:
                continue
            # 更新用户字段
            if protocol == "tuic":
                outbound["uuid"] = user["uuid"]
                outbound["password"] = user["password"]
            elif protocol == "vless":
                outbound["uuid"] = user["uuid"]
            elif protocol in ["hysteria2", "shadowsocks"]:
                outbound["password"] = user["password"]
            updated = True

            # 替换 server 和 tls.server_name
            if first_domain:
                if "server" in outbound:
                    outbound["server"] = first_domain
                if "tls" in outbound and isinstance(outbound["tls"], dict):
                    if "reality" in outbound["tls"] and reality_sni:
                        outbound["tls"]["server_name"] = reality_sni
                    else:
                        outbound["tls"]["server_name"] = first_domain

        # 管理目录
        manage_file = client_manage_dir / f"{first_domain}-{user['name']}.json"
        save_json(manage_file, new_config)
        ts_print(f"已生成客户端配置 -> : {manage_file}")
        
        user_publish_dir = BASE_DIR / "journal" / "public" / "uploads" / user["name"]
        user_publish_dir.mkdir(parents=True, exist_ok=True)
        publish_file = user_publish_dir / f"{first_domain}-{user['name']}.json"
        shutil.copy(manage_file, publish_file)
        ts_print(f"已发布客户端配置: {publish_file}")
        # 订阅号文件
        if "subscription_token" in user:
            sub_url = f"https://{first_domain}/sub/{user['subscription_token']}"
            sub_file = user_publish_dir / f"{first_domain}-{user['name']}.txt"
            sub_file.write_text(sub_url, encoding="utf-8")
            ts_print(f"已发布订阅地址: {sub_file} -> {sub_url}")

    ts_print("所有操作完成！")

if __name__ == "__main__":
    main()