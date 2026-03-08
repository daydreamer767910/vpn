from flask import Flask, abort, make_response
from pathlib import Path
from datetime import datetime
import json

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
USER_DIR = BASE_DIR / "singbox" / "client" / "users"   # 用户节点配置文件目录
USERS_FILE = BASE_DIR / "singbox" / "users.json"       # 用户信息列表文件

def load_users():
    if not USERS_FILE.exists():
        return []
    with USERS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with USERS_FILE.open("w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)

def get_user_by_token(token):
    users = load_users()
    for u in users:
        if u.get("subscription_token") == token:
            return u
    return None

def update_user_traffic(user, upload_inc=0, download_inc=0):
    """
    累加用户流量（可后续替换为 Sing-Box API 或日志解析）
    """
    user['upload'] = user.get('upload', 0) + upload_inc
    user['download'] = user.get('download', 0) + download_inc
    users = load_users()
    for i, u in enumerate(users):
        if u['subscription_token'] == user['subscription_token']:
            users[i] = user
            break
    save_users(users)

@app.route("/sub/<token>")
def download_by_token(token):
    user = get_user_by_token(token)
    if not user:
        abort(404, description="订阅 token 不存在")

    if not user.get("enabled", True):
        abort(403, description=f"用户 {user['name']} 已停用")

    # 找到用户节点配置文件
    manage_file = (USER_DIR / f"{user['name']}.json").resolve()
    if not str(manage_file).startswith(str(USER_DIR.resolve())):
        abort(403, description="非法路径访问")
    if not manage_file.exists():
        abort(404, description="JSON 配置不存在")

    # 读取节点 JSON 配置
    content = manage_file.read_text(encoding="utf-8")

    # 构建响应
    resp = make_response(content)
    resp.mimetype = "application/json"
    resp.headers["Content-Disposition"] = "inline"

    # subscription-userinfo
    upload = user.get("upload", 0)
    download = user.get("download", 0)
    total = user.get("traffic_limit") or 0
    expire_at = user.get("expire_at")
    expire_ts = int(datetime.fromisoformat(expire_at).timestamp()) if expire_at else 0

    resp.headers["subscription-userinfo"] = f"upload={upload}; download={download}; total={total}; expire={expire_ts}"

    return resp

if __name__ == "__main__":
    # 生产环境建议用 gunicorn 或 uwsgi 部署
    app.run(host="0.0.0.0", port=5000, threaded=True)