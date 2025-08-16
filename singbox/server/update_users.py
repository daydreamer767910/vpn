import json
import argparse
import shutil

# ------------------------
# 命令行参数
# ------------------------
parser = argparse.ArgumentParser(description="Update sing-box users from users.json")
parser.add_argument("--config", default="config.json", help="原始配置文件")
parser.add_argument("--users", default="users.json", help="用户清单文件")
parser.add_argument("--protocols", nargs="*", default=[], help="指定协议更新，例如 tuic vless")
args = parser.parse_args()

# ------------------------
# 读取配置和用户
# ------------------------
try:
    with open(args.config, "r") as f:
        config = json.load(f)
except Exception as e:
    print(f"读取配置失败: {e}")
    exit(1)

try:
    with open(args.users, "r") as f:
        users = json.load(f)
except Exception as e:
    print(f"读取用户列表失败: {e}")
    exit(1)

print(f"读取配置文件: {args.config}")
print(f"读取用户列表: {len(users)} 个用户")

# ------------------------
# 备份原配置
# ------------------------
shutil.copy(args.config, args.config + ".bak")
print(f"已备份原配置 -> {args.config}.bak")

# ------------------------
# helper: 根据协议生成 users
# ------------------------
def format_users(protocol, users):
    protocol = protocol.lower()
    if protocol == "tuic":
        return [{"uuid": u["uuid"], "password": u["password"]} for u in users]
    elif protocol == "vless":
        return [{"uuid": u["uuid"], "flow": "xtls-rprx-vision"} for u in users]
    elif protocol == "hysteria2":
        return [{"name": u.get("name", ""), "password": u["password"]} for u in users]
    elif protocol == "shadowsocks":
        return [{"method": "2022-blake3-aes-128-gcm", "password": u["password"]} for u in users]
    else:
        return []

# ------------------------
# 遍历 inbounds 更新 users
# ------------------------
updated_any = False
for inbound in config.get("inbounds", []):
    protocol = inbound.get("type", "").lower()
    if not protocol:
        continue
    if protocol in ["tuic", "vless", "hysteria2", "shadowsocks"]:
        # 如果指定了协议，只更新选中的协议
        if args.protocols and protocol not in [p.lower() for p in args.protocols]:
            continue
        inbound["users"] = format_users(protocol, users)
        updated_any = True
        user_names = [u.get("name", u.get("uuid", "")) for u in users]
        print(f"更新 {protocol} 用户: {', '.join(user_names)}")

if not updated_any:
    print("没有匹配到需要更新的 inbound 用户，请检查 inbound type 或 protocols 参数。")

# ------------------------
# 覆盖原配置
# ------------------------
with open(args.config, "w") as f:
    json.dump(config, f, indent=2)

print(f"用户已更新 -> {args.config}")
