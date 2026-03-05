from flask import Flask, send_file, abort
from pathlib import Path
import json

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
USER_DIR = BASE_DIR / "singbox"
UPLOAD_DIR = BASE_DIR / "public" / "uploads"


def get_user_by_token(token):
    users_file = USER_DIR / "users.json"
    if not users_file.exists():
        return None

    with open(users_file, "r", encoding="utf-8") as f:
        users = json.load(f)

    for u in users:
        if u.get("subscription_token") == token:
            return u

    return None


@app.route("/sub/<token>")
def download_by_token(token):

    user = get_user_by_token(token)
    if not user:
        abort(404)

    user_dir = UPLOAD_DIR / user["name"]

    if not user_dir.exists():
        abort(404)

    files = list(user_dir.glob(f"*-{user['name']}.json"))

    if not files:
        abort(404)

    return send_file(files[0], mimetype="application/json")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)