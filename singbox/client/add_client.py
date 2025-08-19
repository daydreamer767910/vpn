import json
import argparse
import os
import shutil

# ------------------------
# 命令行参数
# ------------------------
parser = argparse.ArgumentParser(description="Update client config with users from users.json")
parser.add_argument("--template", default="config.json", help="客户端模板配置文件")
parser.add_argument("--users", default="users.json", help="用户清单文件")
parser.add_argument("--output_dir", default=".", help="输出目录")
parser.add_argument("--protocols", nargs="*", default=["tuic", "vless", "hysteria2", "shadowsocks"],
                    help="需要更新的协议")
args = parser.parse_args()

# ------------------------
# 读取模板
# ------------------------
try:
    with open(args.template, "r") as f:
        template = json.load(f)
except Exception as e:
    print(f"读取模板失败: {e}")
    exit(1)

# ------------------------
# 读取用户
# ------------------------
try:
    with open(args.users, "r") as f:
        users = json.load(f)
except Exception as e:
    print(f"读取用户列表失败: {e}")
    exit(1)

# ------------------------
# 确保输出目录存在
# ------------------------
os.makedirs(args.output_dir, exist_ok=True)

# ------------------------
# 遍历模板 outbounds，更新用户信息
# ------------------------
for user in users:
    # 拷贝模板，避免修改原模板
    new_config = json.loads(json.dumps(template))
    updated = False

    for outbound in new_config.get("outbounds", []):
        protocol = outbound.get("type", "").lower()
        if protocol in [p.lower() for p in args.protocols]:
            if protocol == "tuic":
                outbound["uuid"] = user["uuid"]
                outbound["password"] = user["password"]
            elif protocol == "vless":
                outbound["uuid"] = user["uuid"]
            elif protocol == "hysteria2":
                outbound["password"] = user["password"]  # 更新用户认证密码
            elif protocol == "shadowsocks":
                outbound["password"] = user["password"]
            updated = True

    if not updated:
        print(f"用户 {user['name']} 未找到匹配的 outbound 协议，请检查模板和 --protocols 参数。")

    # 输出文件
    template_filename = os.path.basename(args.template)
    output_file = os.path.join(args.output_dir, f"{user['name']}-{template_filename}")
    with open(output_file, "w") as f:
        json.dump(new_config, f, indent=2)
    print(f"已生成配置 -> {output_file}")
