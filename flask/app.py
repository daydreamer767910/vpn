from flask import Flask, send_file, abort
from pathlib import Path
import json
import logging

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
USER_DIR = BASE_DIR / "singbox" / "client" / "users"
USERS_FILE = BASE_DIR / "singbox" / "users.json"

# -----------------------------
# 日志配置
# -----------------------------
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')


def load_users():
    if not USERS_FILE.exists():
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"读取 users.json 失败: {e}")
        return []


def get_user_by_token(token):
    users = load_users()
    for u in users:
        if u.get("subscription_token") == token:
            return u
    return None


@app.route("/sub/<token>")
def download_by_token(token):
    user = get_user_by_token(token)
    if not user:
        app.logger.warning(f"订阅 token 不存在: {token}")
        abort(404)

    # 检查用户是否停用
    if not user.get("enabled", True):
        app.logger.info(f"用户 {user['name']} 已停用，禁止下载")
        abort(403)

    # 安全路径拼接
    manage_file = (USER_DIR / f"{user['name']}.json").resolve()
    if not str(manage_file).startswith(str(USER_DIR.resolve())):
        app.logger.warning(f"非法路径访问尝试: {manage_file}")
        abort(403)

    if not manage_file.exists():
        app.logger.warning(f"JSON 配置不存在: {manage_file}")
        abort(404)

    app.logger.info(f"用户 {user['name']} 通过 token 下载配置")
    return send_file(manage_file, mimetype="application/json", as_attachment=False)


if __name__ == "__main__":
    # 生产环境建议用 gunicorn 或 uwsgi 部署
    app.run(host="0.0.0.0", port=5000, threaded=True)