#!/bin/bash
# deploy.sh - 自动化部署脚本
# Root 执行，创建最终用户并初始化环境
# 处理仓库根目录本身是 vpn 的情况

set -e

# -------------------------
# 加载配置
# -------------------------
source "./config.sh"

echo "==== [DEPLOY] Starting deployment ===="

# -------------------------
# 安装必要工具
# -------------------------
apt update
apt install -y certbot git curl ufw apt-transport-https ca-certificates gnupg lsb-release software-properties-common

# 安装 Docker（安全版）
# -------------------------
echo "==== [INFO] Checking Docker installation ===="
if command -v docker &>/dev/null; then
    echo "[INFO] Docker is already installed: $(docker --version)"
    echo "[INFO] Skipping Docker installation to avoid overwriting custom settings."
else
    echo "[INFO] Docker not found. Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    echo "[INFO] Docker installation completed: $(docker --version)"
fi

# ================================
# 申请证书
# ================================
echo "====Obtaining Let's Encrypt certificates..."

certbot certonly --standalone \
    $(printf -- "-d %s " $DOMAINS) \
    --non-interactive \
    --agree-tos \
    -m "$USER_EMAIL"

echo "Certificates obtained."

# -------------------------
# 创建最终用户
# -------------------------
# 1. 创建新用户（如果不存在）
if ! id "$DEPLOY_USER" &>/dev/null; then
    adduser --gecos "" "$DEPLOY_USER"
	# 准备用户目录
	mkdir -p /home/$DEPLOY_USER
	chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER
fi

# 2. 将新用户加入 docker 用户组
usermod -aG docker "$DEPLOY_USER"

# 3. 给该用户 sudo 权限（可选）
usermod -aG sudo "$DEPLOY_USER"

# 4. 设置 SSH 公钥登录
mkdir -p /home/"$DEPLOY_USER"/.ssh
cp ~/.ssh/authorized_keys /home/"$DEPLOY_USER"/.ssh/
chown -R "$DEPLOY_USER":"$DEPLOY_USER" /home/"$DEPLOY_USER"/.ssh
chmod 700 /home/"$DEPLOY_USER"/.ssh
chmod 600 /home/"$DEPLOY_USER"/.ssh/authorized_keys

# -------------------------

# -------------------------
# 移动仓库内容到用户目录
# -------------------------
echo "[INFO] Copying repository contents to /home/$DEPLOY_USER..."

# 仓库里根目录是 vpn，移动其内容而不是整个 vpn 文件夹
cp -a . /home/$DEPLOY_USER/
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
