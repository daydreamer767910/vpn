#!/bin/bash
# config.sh - 部署配置文件

# 最终用户
DEPLOY_USER="vpn"
USER_EMAIL="xxxxxxxx@gmail.com"

# 时区
TIMEZONE="America/Los_Angeles"


# Sing-box 各协议监听端口
SINGBOX_PORT_START=10000
SINGBOX_PORT_END=10006

# Reality TLS 配置
SNI="www.microsoft.com"

# DNS
DNS_STRATEGY="prefer_ipv4"
# endpoint
TS_AUTH_KEY="tskey-auth-kWohtATBST11CNTRL-dc2CsT1CargmmW9yVPgPrg8RDjA5ZJFQ6"
TS_HOSTNAME="dd2001"
TS_HOSTIP='"100.89.79.85","fd7a:115c:a1e0::1d35:4f55"'
TS_EXIT_NODE="100.68.141.58"
TS_DOMAIN_SUFFIX="tail4e565.ts.net"


# 域名列表
DOMAINLIST=("xxx.yyy.zzz" "aaa.bbb.ccc")

# 证书目录
CERT_SRC="/etc/letsencrypt/live/${DOMAINLIST[0]}"
CERT_DST="/home/$DEPLOY_USER/cert"

# Docker 容器名
NGINX_CONTAINER="nginx"
SINGBOX_CONTAINER="singbox-server"

# 日志文件
LOG_FILE="/home/$DEPLOY_USER/log/smart_run.log"

# 定期检查证书
CRON_SCHEDULE="0 3 * * *"
