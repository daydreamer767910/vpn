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
    $(printf -- "-d %s " "${DOMAINLIST[@]}") \
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
    restart: unless-stopped
    command: >
      run -c /app/singbox/config.json
    volumes:
      - ${CERT_DST}:/app/cert:ro
      - ./singbox/server:/app/singbox
    ports:
      - "$SINGBOX_PORT_START-$SINGBOX_PORT_END:$SINGBOX_PORT_START-$SINGBOX_PORT_END/tcp"
      - "$SINGBOX_PORT_START-$SINGBOX_PORT_END:$SINGBOX_PORT_START-$SINGBOX_PORT_END/udp"
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
    #ports:
      #- "5000:5000"
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

REALITY_PRIVATE_KEY=$(echo "$KEYPAIR" | grep PrivateKey | awk '{print $2}')
REALITY_PUBLIC_KEY=$(echo "$KEYPAIR" | grep PublicKey | awk '{print $2}')
REALITY_SHORT_ID=$(openssl rand -hex 8)

echo "==== Reality Keys ===="
echo "Private: $REALITY_PRIVATE_KEY"
echo "Public : $REALITY_PUBLIC_KEY"
echo "short id: $REALITY_SHORT_ID"
echo "======================"


DNS_TEMPLATE="$TMPLT_DIR/dns.json"
TLS_TEMPLATE="$TMPLT_DIR/tls.json"
ROUTE_TEMPLATE="$TMPLT_DIR/route.json"
PROTOCOL_TEMPLATE="$TMPLT_DIR/protocols.json"
ENDPOINT_TEMPLATE="$TMPLT_DIR/endpoints.json"
cat > "$ENDPOINT_TEMPLATE" <<EOF
[
  {
    "address": [
      "172.16.0.2/32",
      "2606:4700:110:8b01:3e09:a4a3:83d4:c9e7/128"
    ],
    "listen_port": 0,
    "mtu": 1420,
    "peers": [
      {
        "address": "engage.cloudflareclient.com",
        "allowed_ips": [
          "0.0.0.0/0",
          "::/0"
        ],
        "port": 2408,
        "public_key": "bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=",
        "reserved": [
          253,
          77,
          206
        ]
      }
    ],
    "private_key": "NSENGZEwfRKQ/hxFd8FVV+/wVcO9576Xwyj5Y6YudKM=",
    "tag": "warp-2000",
    "type": "wireguard"
  },
  {
    "accept_routes": false,
    "auth_key": "$TS_AUTH_KEY",
    "control_url": "https://controlplane.tailscale.com",
    "domain_resolver": "local",
    "exit_node": "$TS_EXIT_NODE",
    "exit_node_allow_lan_access": true,
    "hostname": "$TS_HOSTNAME",
    "state_directory": "/app/singbox/.tailscale",
    "system_interface": false,
    "system_interface_mtu": 1280,
    "system_interface_name": "tailscale-tun",
    "tag": "tailscale-ep",
    "type": "tailscale"
  }
]
EOF
cat > "$PROTOCOL_TEMPLATE" <<EOF
{
  "direct":{
    "outbound":{ "tag": "direct", "type": "direct" }
  },
  "tun":{
    "inbound":{
      "type": "tun",
      "tag": "tun-in",
      "interface_name": "tun0",
      "address": [
        "172.18.0.1/30",
        "fdfe:dcba:9876::1/126"
      ],
      "mtu": 9000,
      "auto_route": true
    }
  },
  "vless-reality": {
    "inbound":{
      "type": "vless",
      "listen": "::",
      "listen_port": 8443,
      "tag": "\$tag-vless-in",
      "tls": "\$tls:tls-reality-in",
      "users": []
    },
    "outbound":{
      "type": "vless",
      "tag": "\$tag-vless-out",
      "server": "${DOMAINLIST[0]}",
      "server_port": 8443,
      "uuid": "1f767449-3d2c-43f9-972f-3bffc3fcfdf7",
      "flow": "xtls-rprx-vision",
      "tls": "\$tls:tls-reality-out"
    }
  },
  "tuic":{
	"inbound":{
		"type": "tuic",
		"congestion_control": "bbr",
		"listen": "::",
		"listen_port": 443,
		"tag": "\$tag-tuic-in",
		"tls": "\$tls:tls-in",
		"users": []
	},
	"outbound":{
		"type": "tuic",
		"tag": "\$tag-tuic-out",
		"server": "${DOMAINLIST[0]}",
		"server_port": 443,
		"uuid": "1f767449-3d2c-43f9-972f-3bffc3fcfdf7",
		"password": "123456",
		"congestion_control": "bbr",
		"tls": "\$tls:tls-out"
	}
  },
  "hysteria2":{
	"inbound":{
		"listen": "::",
		"listen_port": 8443,
		"masquerade": {
			"directory": "/file/download",
			"type": "file"
		},
		"obfs": {
			"password": "1NlXeWE6v0J3S",
			"type": "salamander"
		},
		"tag": "\$tag-hy2-in",
		"tls": "\$tls:tls-in",
		"type": "hysteria2",
		"users": []
	},
	"outbound":{
		"type": "hysteria2",
		"tag": "\$tag-hy2-out",
		"server": "${DOMAINLIST[0]}",
		"server_port": 8443,
		"obfs": {
			"type": "salamander",
			"password": "1NlXeWE6v0J3S"
		},
		"password": "123456",
		"tls": "\$tls:tls-out"
	}
  }
}
EOF
cat > "$ROUTE_TEMPLATE" <<EOF
{
  "route-server": {
    "rules": [
      {
        "action": "sniff"
      },
      {
        "action": "route",
        "outbound": "direct",
        "type": "logical",
        "mode": "or",
        "rules": [
          {
            "domain": [
              "$TS_HOSTNAME.$TS_DOMAIN_SUFFIX"
            ]
          },
          {
            "ip_cidr": [
              $TS_HOSTIP
            ]
          }
        ]
      },
      {
        "action": "route",
        "outbound": "tailscale-ep",
        "type": "logical",
        "mode": "or",
        "rules": [
          {
            "ip_cidr": [
              "100.64.0.0/10",
              "fd7a:115c:a1e0::/48"
            ]
          },
          {
            "domain_suffix": [
              "$TS_DOMAIN_SUFFIX"
            ]
          }
        ]
      },
      {
        "auth_user": [
          "tailscale"
        ],
        "action": "route",
        "outbound": "tailscale-ep"
      },
      {
        "auth_user": [
          "warp"
        ],
        "action": "route",
        "outbound": "warp-2000"
      }
    ],
    "rule_set": [],
    "default_domain_resolver": "local",
    "final": "direct"
  },
  "route-client": {
    "rule_set": [
      {
        "type": "remote",
        "tag": "geoip-cn",
        "format": "binary",
        "url": "https://github.com/SagerNet/sing-geoip/releases/latest/download/geoip.db",
        "download_detour": "direct"
      },
      {
        "type": "remote",
        "tag": "geosite-cn",
        "format": "binary",
        "url": "https://github.com/SagerNet/sing-geosite/releases/latest/download/geosite.db",
        "download_detour": "direct"
      }
    ],
    "rules": [
      {
        "rule_set": "geosite-cn",
        "outbound": "direct"
      },
      {
        "rule_set": "geoip-cn",
        "outbound": "direct"
      }
    ],
    "final": "auto-proxy"
  }
}
EOF
cat > "$TLS_TEMPLATE" <<EOF
{
  "tls-in": {
	"alpn": [
		"h3",
		"h2",
		"http/1.1"
	],
	"certificate_path": "/app/cert/cert.pem",
	"enabled": true,
	"key_path": "/app/cert/privkey.pem",
	"server_name": "${DOMAINLIST[0]}"
  },
  "tls-out": {
	"enabled": true,
	"server_name": "${DOMAINLIST[0]}",
	"insecure": false,
	"alpn": [
		"h3"
	]
  },
  "tls-reality-in": {
	"server_name": "$SNI",
	"enabled": true,
	"reality": {
		"enabled": true,
		"handshake": {
			"server": "$SNI",
			"server_port": 443
		},
		"private_key": "$REALITY_PRIVATE_KEY",
        "short_id": [ "$REALITY_SHORT_ID" ]
	}
  },
  "tls-reality-out": {
	"server_name": "$SNI",
	"enabled": true,
	"reality": {
		"enabled": true,
		"public_key": "$REALITY_PUBLIC_KEY",
		"short_id": "$REALITY_SHORT_ID"
	},
	"utls": {
		"enabled": true,
		"fingerprint": "chrome"
	}
  }
}
EOF
cat > "$DNS_TEMPLATE" <<EOF
{
  "dns-server": {
    "servers": [
      {
        "type": "local",
        "tag": "local"
      },
      {
        "type": "tailscale",
        "tag": "tailscale",
        "endpoint": "tailscale-ep"
      }
    ],
    "rules": [
      {
        "domain_suffix": [
          "$TS_DOMAIN_SUFFIX"
        ],
        "action": "route",
        "server": "tailscale"
      }
    ],
    "strategy": "$DNS_STRATEGY",
    "final": "local"
  },
  "dns-client": {
    "servers": [
      {
        "address": "223.5.5.5",
        "detour": "direct",
        "tag": "cn-dns"
      },
      {
        "address": "1.1.1.1",
        "detour": "auto-proxy",
        "tag": "proxy-dns"
      }
    ],
    "rules": [
      {
        "geosite": [
          "cn"
        ],
        "server": "cn-dns"
      },
      {
        "geosite": [
          "geolocation-!cn"
        ],
        "server": "proxy-dns"
      }
    ],
    "final": "proxy-dns",
    "strategy": "$DNS_STRATEGY",
    "independent_cache": true
  }
}
EOF

chown $DEPLOY_USER:$DEPLOY_USER $DNS_TEMPLATE
chown $DEPLOY_USER:$DEPLOY_USER $TLS_TEMPLATE
chown $DEPLOY_USER:$DEPLOY_USER $ROUTE_TEMPLATE
chown $DEPLOY_USER:$DEPLOY_USER $PROTOCOL_TEMPLATE
chown $DEPLOY_USER:$DEPLOY_USER $ENDPOINT_TEMPLATE
echo "[INFO] template generated at $TMPLT_DIR"

su - $DEPLOY_USER -c "docker compose up -d"
echo "==== [DEPLOY] Deployment complete! ===="
echo "Next steps:"
echo "Switch to user: su - $DEPLOY_USER"
echo "run python3 manage_nodes.py --add xxx to create nodes"
echo "run sudo python3 manage_nodes.py --firewall to allow the ports"
echo "run python3 manage_users.py --add xxx --extend days to create users"
