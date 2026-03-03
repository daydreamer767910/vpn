#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manage_users.py - Sing-box 用户管理脚本（跨平台 UTF-8，支持 Reality SNI）
"""

import json
import os
import shutil
import uuid
import random
import string
import argparse
import subprocess

# ------------------------
# 命令行参数
# ------------------------
parser = argparse.ArgumentParser(description="整合 Sing-box 用户管理脚本")
parser.add_argument("--protocols", nargs="*", default=["tuic", "vless", "hysteria2", "shadowsocks"],
                    help="指定需要更新的协议")
parser.add_argument("--password_length", type=int, default=20, help="随机密码长度")
parser.add_argument("--delete", nargs="*", help="删除指定用户名，空格或逗号分隔")
parser.add_argument("--clear", action="store_true", help="清空所有用户")
parser.add_argument("--auto_check_journal", action="store_true",
                    help="检查 journal/db/users/ 是否有新增/删除用户，并同步")
args = parser.parse_args()

# ------------------------
# 路径设置
# ------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SINGBOX_DIR = os.path.join(BASE_DIR, "singbox")
JOURNAL_DB_USERS = os.path.join(BASE_DIR, "journal", "db", "users")

USERS_FILE = os.path.join(SINGBOX_DIR, "users.json")
SERVER_CONFIG_FILE = os.path.join(SINGBOX_DIR, "server", "config.json")
CLIENT_TEMPLATE_FILE = os.path.join(SINGBOX_DIR, "client", "config.json")
CONFIG_SH = os.path.join(BASE_DIR, "config.sh")

# 自动创建缺失目录
os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
os.makedirs(os.path.dirname(SERVER_CONFIG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(CLIENT_TEMPLATE_FILE), exist_ok=True)
os.makedirs(JOURNAL_DB_USERS, exist_ok=True)

# ------------------------
# 读取 config.sh
# ------------------------
def parse_config_sh(sh_file):
    first_domain = None
    reality_sni = None
    if os.path.exists(sh_file):
        with open(sh_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DOMAINLIST"):
                    start = line.find('(')
                    end = line.find(')')
                    if start != -1 and end != -1:
                        items = line[start+1:end].replace('"', '').split()
                        if items:
                            first_domain = items[0]
                elif line.startswith("SNI="):
                    reality_sni = line.split("=",1)[1].strip().strip('"')
    return first_domain, reality_sni

first_domain, reality_sni = parse_config_sh(CONFIG_SH)
if not first_domain:
    print("⚠️ config.sh 未找到 DOMAINLIST 或为空，客户端 server 和 TLS server_name 不会修改。")
if not reality_sni:
    print("⚠️ config.sh 未找到 SNI，Reality 模式 server_name 不会修改。")

# ------------------------
# 随机密码生成
# ------------------------
def generate_password(length):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

# ------------------------
# 加载用户列表
# ------------------------
if os.path.exists(USERS_FILE):
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
else:
    users = []

existing_names = {u["name"] for u in users}

# ------------------------
# 轮询 journal/db/users/ 检查用户变动
# ------------------------
if args.auto_check_journal:
    journal_users = {f[:-5] for f in os.listdir(JOURNAL_DB_USERS) if f.endswith(".json")}
    current_users = existing_names

    to_add = journal_users - current_users
    to_del = current_users - journal_users

    if to_add:
        print(f"[JOURNAL] 发现新增用户: {', '.join(to_add)}")
        for name in to_add:
            password = generate_password(args.password_length)
            new_user = {
                "name": name,
                "uuid": str(uuid.uuid4()),
                "password": password
            }
            users.append(new_user)
            existing_names.add(name)
    if to_del:
        print(f"[JOURNAL] 发现删除用户: {', '.join(to_del)}")
        users = [u for u in users if u["name"] not in to_del]
        existing_names -= to_del
    if to_add or to_del:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
        print("[JOURNAL] 已同步用户到 users.json")
        # 可选择重启容器
        try:
            subprocess.run(["docker", "restart", "singbox-server"], check=True)
            print("[JOURNAL] 已重启 singbox-server 容器")
        except Exception as e:
            print(f"[WARN] 重启容器失败: {e}")

# ------------------------
# 删除或清空用户
# ------------------------
if args.clear:
    users = []
    existing_names = set()
    print("已清空所有用户")
elif args.delete:
    del_names = [n.strip() for name in args.delete for n in name.replace(",", " ").split()]
    users = [u for u in users if u["name"] not in del_names]
    existing_names -= set(del_names)
    print(f"已删除用户: {', '.join(del_names)}")

# 写回用户文件
with open(USERS_FILE, "w", encoding="utf-8") as f:
    json.dump(users, f, indent=2, ensure_ascii=False)

# ------------------------
# 批量新增用户
# ------------------------
if not args.clear:
    print("请输入用户名（可批量输入，逗号或空格分隔），空输入结束：")
    added_count = 0
    while True:
        raw_input_names = input("用户名: ").strip()
        if not raw_input_names:
            break
        names = [n.strip() for n in raw_input_names.replace(",", " ").split() if n.strip()]
        for name in names:
            if name in existing_names:
                print(f"用户 {name} 已存在，跳过。")
                continue
            password = generate_password(args.password_length)
            new_user = {
                "name": name,
                "uuid": str(uuid.uuid4()),
                "password": password
            }
            users.append(new_user)
            existing_names.add(name)
            added_count += 1
            print(f"已添加用户: {name}, 密码: {password}")

    if added_count > 0:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
        print(f"\n已更新用户清单 -> {USERS_FILE}, 新增 {added_count} 个用户")
    else:
        print("没有新增用户。")

# ------------------------
# 更新服务端配置
# ------------------------
try:
    with open(SERVER_CONFIG_FILE, "r", encoding="utf-8") as f:
        server_config = json.load(f)
except Exception as e:
    print(f"读取服务端配置失败: {e}")
    exit(1)

# 备份
shutil.copy(SERVER_CONFIG_FILE, SERVER_CONFIG_FILE + ".bak")
print(f"已备份服务端配置 -> {SERVER_CONFIG_FILE}.bak")

updated_any = False
for inbound in server_config.get("inbounds", []):
    protocol = inbound.get("type", "").lower()
    if protocol not in [p.lower() for p in args.protocols]:
        continue
    if protocol in ["tuic", "vless", "hysteria2", "shadowsocks"]:
        if protocol == "tuic":
            inbound["users"] = [{"name": u["name"], "uuid": u["uuid"], "password": u["password"]} for u in users]
        elif protocol == "vless":
            inbound["users"] = [{"name": u["name"], "uuid": u["uuid"], "flow": "xtls-rprx-vision"} for u in users]
        elif protocol == "hysteria2":
            inbound["users"] = [{"name": u["name"], "password": u["password"]} for u in users]
        elif protocol == "shadowsocks":
            inbound["users"] = [{"method": "2022-blake3-aes-128-gcm", "password": u["password"]} for u in users]
        updated_any = True
        print(f"更新 {protocol} 用户: {', '.join(u['name'] for u in users)}")

if updated_any:
    with open(SERVER_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(server_config, f, indent=2, ensure_ascii=False)
    print(f"服务端用户已更新 -> {SERVER_CONFIG_FILE}")
else:
    print("没有匹配到需要更新的 inbound 用户。")

# ------------------------
# 生成客户端配置（管理 + 发布）
# ------------------------
try:
    with open(CLIENT_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        client_template = json.load(f)
except Exception as e:
    print(f"读取客户端模板失败: {e}")
    exit(1)

client_manage_dir = os.path.dirname(CLIENT_TEMPLATE_FILE)
os.makedirs(client_manage_dir, exist_ok=True)

for user in users:
    new_config = json.loads(json.dumps(client_template))
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
        elif protocol == "hysteria2":
            outbound["password"] = user["password"]
        elif protocol == "shadowsocks":
            outbound["password"] = user["password"]
        updated = True

        # 替换 server 和 tls.server_name
        if first_domain:
            if "server" in outbound:
                outbound["server"] = first_domain
            if "tls" in outbound and isinstance(outbound["tls"], dict):
                if "reality" in outbound["tls"]:
                    if reality_sni:
                        outbound["tls"]["server_name"] = reality_sni
                else:
                    outbound["tls"]["server_name"] = first_domain

    if not updated:
        print(f"用户 {user['name']} 未匹配任何 outbound 协议，请检查模板和 --protocols 参数。")

    # 管理目录
    manage_file = os.path.join(client_manage_dir, f"{user['name']}-config.json")
    with open(manage_file, "w", encoding="utf-8") as f:
        json.dump(new_config, f, indent=2, ensure_ascii=False)

    # 发布目录（每个用户单独目录）
    user_publish_dir = os.path.join(BASE_DIR, "journal", "public", "uploads", user["name"])
    os.makedirs(user_publish_dir, exist_ok=True)
    publish_file = os.path.join(user_publish_dir, f"{user['name']}-config.json")
    shutil.copy(manage_file, publish_file)
    print(f"已生成客户端配置 -> 管理: {manage_file}, 发布: {publish_file}")

print("\n所有操作完成！")