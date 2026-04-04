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
# 设置时区和时间同步
# ================================
TIMEZONE="${TIMEZONE:-Asia/Shanghai}"

if command -v timedatectl >/dev/null 2>&1; then
    echo "==== Setting timezone to $TIMEZONE ===="
    timedatectl set-timezone "$TIMEZONE"
else
    echo "timedatectl not found, skipping timezone setup."
fi

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
SSH_KEY_SRC="$HOME/.ssh/authorized_keys"
SSH_DIR="/home/$DEPLOY_USER/.ssh"

mkdir -p "$SSH_DIR"

if [ -f "$SSH_KEY_SRC" ]; then
    cp "$SSH_KEY_SRC" "$SSH_DIR/"
    chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    chmod 600 "$SSH_DIR/authorized_keys"
    echo "SSH key copied to $DEPLOY_USER"
else
    echo "Warning: $SSH_KEY_SRC does not exist. Skipping SSH key setup."
fi

# -------------------------
# 移动仓库内容到用户目录
# -------------------------
echo "[INFO] Copying repository contents to /home/$DEPLOY_USER..."

LOG_DIR="$(dirname "$LOG_FILE")"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# rsync 时排除用户数据和配置目录
rsync -a \
    --exclude='singbox/users.json' \
    --exclude='singbox/client/users/' \
    --exclude='deploy_all.sh' \
    --exclude='template' \
    --exclude='*.md' \
    --exclude='.git' \
    ./ /home/$DEPLOY_USER/
#rsync -a --exclude='deploy_all.sh' --exclude='.git' --exclude='*.md' ./ /home/$DEPLOY_USER/
chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER


# ================================
# 申请证书, 成功后再复制
# ================================

echo "==== Obtaining Let's Encrypt certificates..."

if certbot certonly --standalone \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    -m "$USER_EMAIL"
then
    echo "==== Certificate obtained successfully."

    echo "==== Copying certificates..."

    mkdir -p "$CERT_DST"

    rsync -a --copy-links "$CERT_SRC"/ "$CERT_DST"/

    # 修改权限
    chown -R $DEPLOY_USER:$DEPLOY_USER "$CERT_DST"

    echo "==== Certificates copied successfully."
else
    echo "!!!! Certificate obtain failed. Skipping copy step."
fi

echo "[INFO] Generating docker-compose.yml..."
# 获取 DEPLOY_USER 的 UID 和 GID
DEPLOY_UID=$(id -u $DEPLOY_USER)
DEPLOY_GID=$(id -g $DEPLOY_USER)
cat > /home/$DEPLOY_USER/docker-compose.yml <<EOF
services:
  nginx:
    image: nginx:latest
    container_name: ${NGINX_CONTAINER}
    networks:
      lan:
         ipv4_address: 172.19.0.2
    restart: unless-stopped
    environment:
      - TZ=${TIMEZONE}
    ports:
      - "443:443/tcp"
    volumes:
      - /home/$DEPLOY_USER/Nginx/conf/nginx.conf:/etc/nginx/nginx.conf:ro
      - ${CERT_DST}:/etc/nginx/certbot:ro
    depends_on:
      - sing-box

  sing-box:
    image: ghcr.io/sagernet/sing-box:latest
    environment:
      - TZ=${TIMEZONE}
    container_name: ${SINGBOX_CONTAINER}
    user: "${DEPLOY_UID}:${DEPLOY_GID}"
    restart: unless-stopped
    command: run -c /app/singbox/config.json
    volumes:
      - ${CERT_DST}:/app/cert:ro
      - ./singbox/server:/app/singbox
    dns:
      - 1.1.1.1
      - 8.8.8.8
    networks:
      lan:
        ipv4_address: 172.19.0.3

  flask-server:
    build: ./flask
    container_name: flask-server
    networks:
      lan:
         ipv4_address: 172.19.0.4
    volumes:
      - ./singbox:/app/singbox:ro
    restart: unless-stopped

networks:
  lan:
    driver: bridge
    ipam:
      driver: default
      config:
        - subnet: 172.19.0.0/16
          gateway: 172.19.0.1
EOF

chown $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER/docker-compose.yml
echo "[INFO] docker-compose.yml generated."
# -------------------------
# 配置 sudo NOPASSWD 给最终用户
# -------------------------
echo "[INFO] Configuring sudoers..."
#echo "Defaults:$DEPLOY_USER !use_pty" >> /etc/sudoers
#echo "$DEPLOY_USER ALL=(ALL) NOPASSWD: /usr/bin/sha256sum, /usr/bin/rsync, /bin/chown, /bin/chmod, /usr/bin/docker" >> /etc/sudoers
echo "Defaults:$DEPLOY_USER !use_pty" > /etc/sudoers.d/$DEPLOY_USER
echo "$DEPLOY_USER ALL=(ALL) NOPASSWD: /usr/bin/sha256sum, /usr/bin/rsync, /bin/chown, /bin/chmod, /usr/bin/docker" >> /etc/sudoers.d/$DEPLOY_USER
chmod 440 /etc/sudoers.d/$DEPLOY_USER

# -------------------------
# smart_run.sh 可执行
# -------------------------
chmod +x "/home/$DEPLOY_USER/smart_run.sh"
# -------------------------
# 配置 crontab（目标用户下）
# -------------------------
echo "[INFO] Setting crontab for smart_run.sh..."
#sudo -u $DEPLOY_USER bash -c "(crontab -l 2>/dev/null; echo '0 3 * * * /home/$DEPLOY_USER/smart_run.sh >> /home/$DEPLOY_USER/renew_cert.log 2>&1') | crontab -"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 3 * * *}"
CRON_JOB="$CRON_SCHEDULE /home/$DEPLOY_USER/smart_run.sh"
sudo -u $DEPLOY_USER bash -c "(crontab -l 2>/dev/null | grep -v 'smart_run.sh'; echo '$CRON_JOB') | crontab -"
# -------------------------
SINGBOX_DIR="/home/$DEPLOY_USER/singbox"
TMPLT_DIR="/home/$DEPLOY_USER/template"
CLIENT_USERS_DIR="$SINGBOX_DIR/client/users"

mkdir -p "$SINGBOX_DIR/server" "$SINGBOX_DIR/client" "$CLIENT_USERS_DIR" "$TMPLT_DIR"
chown $DEPLOY_USER:$DEPLOY_USER $TMPLT_DIR
chown $DEPLOY_USER:$DEPLOY_USER $SINGBOX_DIR
chown $DEPLOY_USER:$DEPLOY_USER $SINGBOX_DIR/server
chown $DEPLOY_USER:$DEPLOY_USER $SINGBOX_DIR/client
chown $DEPLOY_USER:$DEPLOY_USER $CLIENT_USERS_DIR


echo "[INFO] Generating Reality keypair via Docker..."

KEYPAIR=$(docker run --rm ghcr.io/sagernet/sing-box generate reality-keypair)
export REALITY_PRIVATE_KEY=$(echo "$KEYPAIR" | grep PrivateKey | awk '{print $2}')
export REALITY_PUBLIC_KEY=$(echo "$KEYPAIR" | grep PublicKey | awk '{print $2}')
export REALITY_SHORT_ID=$(openssl rand -hex 8)
echo "==== Reality Keys ===="
echo "Private: $REALITY_PRIVATE_KEY"
echo "Public : $REALITY_PUBLIC_KEY"
echo "short id: $REALITY_SHORT_ID"
echo "======================"

KEYPAIR=$(docker run --rm ghcr.io/sagernet/sing-box generate wg-keypair)
export WG_SRV_PRIVATE_KEY=$(echo "$KEYPAIR" | grep PrivateKey | awk '{print $2}')
export WG_SRV_PUBLIC_KEY=$(echo "$KEYPAIR" | grep PublicKey | awk '{print $2}')
echo "==== Wireguard Host Keys ===="
echo "Private: $WG_SRV_PRIVATE_KEY"
echo "Public : $WG_SRV_PUBLIC_KEY"
echo "======================"
KEYPAIR=$(docker run --rm ghcr.io/sagernet/sing-box generate wg-keypair)
export WG_CLNT_PRIVATE_KEY=$(echo "$KEYPAIR" | grep PrivateKey | awk '{print $2}')
export WG_CLNT_PUBLIC_KEY=$(echo "$KEYPAIR" | grep PublicKey | awk '{print $2}')
echo "==== Wireguard Peer Keys ===="
echo "Private: $WG_CLNT_PRIVATE_KEY"
echo "Public : $WG_CLNT_PUBLIC_KEY"
echo "======================"
export DOMAIN_LOCAL_STR=$(printf '"%s",\n' "${DOMAIN_LOCAL_LIST[@]}" | sed '$ s/,$//')
export SNI
export DNS_STRATEGY
export WG_HOSTIPS
export WG_SUBNET
export WG_PORT
export DOMAIN

for t in template/*.json; do
    filename=$(basename "$t")
    envsubst < "$t" | sed 's|__DOLLAR__|$|g' > "$TMPLT_DIR/$filename"
    chown $DEPLOY_USER:$DEPLOY_USER $TMPLT_DIR/$filename
done

echo "[INFO] template generated at $TMPLT_DIR"
su - $DEPLOY_USER -c "python3 manage_users.py --add admin"
su - $DEPLOY_USER -c "docker compose up -d"
su - $DEPLOY_USER -c "python3 manage_nodes.py --add $TS_HOSTNAME"
echo "==== [DEPLOY] Deployment complete! ===="
echo "Next steps:"
echo "Switch to user: su - $DEPLOY_USER"
echo "run python3 manage_nodes.py --add xxx to create nodes"
echo "run sudo python3 manage_nodes.py --firewall to allow the ports"
echo "run python3 manage_users.py --add xxx --extend days to create users"
