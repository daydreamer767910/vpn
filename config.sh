#!/bin/bash
# config.sh - 部署配置文件

# 最终用户
DEPLOY_USER="vpn"
USER_EMAIL="xxxxxxxx@gmail.com"

# 时区
TIMEZONE="America/Los_Angeles"


# Sing-box 各协议监听端口
SINGBOX_PORT_VLESS=8443
SINGBOX_PORT_TUIC=443
SINGBOX_PORT_HYSTERIA2=8443
# Reality TLS 配置
SNI="www.microsoft.com"
REALITY_PUBLIC_KEY=""
REALITY_PRIVATE_KEY=""
REALITY_SHORT_ID="0abd24"
# DNS
DNS_STRATEGY="prefer_ipv4"

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
