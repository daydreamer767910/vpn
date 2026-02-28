#!/bin/bash
# deploy.sh - 自动化部署脚本
# Root 执行，创建最终用户并初始化环境
# 处理仓库根目录本身是 vpn 的情况

set -e

# -------------------------
# 配置
# -------------------------
REPO_URL="https://github.com/daydreamer767910/vpn.git"
CONFIG_FILE="config.sh"
TMP_DIR="/root/vpn_tmp"

echo "==== [DEPLOY] Starting deployment ===="

# -------------------------
# 安装必要工具
# -------------------------
apt update
apt install -y git curl ufw apt-transport-https ca-certificates gnupg lsb-release software-properties-common

# 安装 Docker
curl -fsSL https://get.docker.com | sh

# -------------------------
# 克隆仓库到临时目录
# -------------------------
mkdir -p "$TMP_DIR"
cd "$TMP_DIR"
if [ ! -d vpn ]; then
    git clone "$REPO_URL"
else
    echo "[INFO] VPN repo already exists in $TMP_DIR/vpn, skipping clone"
fi

# -------------------------
# 加载配置
# -------------------------
source "$TMP_DIR/vpn/$CONFIG_FILE"

# -------------------------
# 创建最终用户
# -------------------------
if ! id "$DEPLOY_USER" &>/dev/null; then
    echo "[INFO] Creating user $DEPLOY_USER..."
    adduser --gecos "" "$DEPLOY_USER"
    usermod -aG sudo,docker "$DEPLOY_USER"
else
    echo "[INFO] User $DEPLOY_USER already exists, skipping creation."
fi

# -------------------------
# 准备用户目录
# -------------------------
mkdir -p /home/$DEPLOY_USER
chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER

# -------------------------
# 移动仓库内容到用户目录
# -------------------------
echo "[INFO] Moving repository contents to /home/$DEPLOY_USER..."

# 仓库里根目录是 vpn，移动其内容而不是整个 vpn 文件夹
rsync -a --exclude='.git' "$TMP_DIR/vpn/" /home/$DEPLOY_USER/
chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER

# -------------------------
# 配置防火墙
# -------------------------
echo "[INFO] Configuring UFW..."
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw allow 8443/udp
ufw allow 51820/udp
ufw --force enable

# -------------------------
# 配置 sudo NOPASSWD 给最终用户
# -------------------------
echo "[INFO] Configuring sudoers..."
echo "Defaults:$DEPLOY_USER !use_pty" >> /etc/sudoers
echo "$DEPLOY_USER ALL=(ALL) NOPASSWD: /usr/bin/sha256sum, /usr/bin/rsync, /bin/chown, /bin/chmod, /usr/bin/docker" >> /etc/sudoers

# -------------------------
# smart_run.sh 可执行
# -------------------------
chmod +x "/home/$DEPLOY_USER/smart_run.sh"
chmod +x "/home/$DEPLOY_USER/smart_renew_cert.sh"
# -------------------------
# 配置 crontab（目标用户下）
# -------------------------
echo "[INFO] Setting crontab for smart_run.sh..."
sudo -u $DEPLOY_USER bash -c "(crontab -l 2>/dev/null; echo '0 3 * * * /home/$DEPLOY_USER/smart_run.sh >> /home/$DEPLOY_USER/renew_cert.log 2>&1') | crontab -"

echo "==== [DEPLOY] Deployment complete! ===="
echo "Next steps:"
echo "1. Switch to user: su - $DEPLOY_USER"
echo "2. Run smart_run.sh for initial sync"
