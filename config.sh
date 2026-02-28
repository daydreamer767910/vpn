#!/bin/bash
# config.sh - 部署配置文件

# 最终用户
DEPLOY_USER="vpn"
USER_EMAIL="daydreamer767910@gmail.com"

# 域名列表
DOMAINLIST=("mass2000.duckdns.org" "dd2000.duckdns.org")

# 证书源目录（smart_run.sh 会使用）
CERT_SRC="/etc/letsencrypt/live/${DOMAINLIST[0]}"
NGINX_CERT_DST="/home/$DEPLOY_USER/Nginx/certbot"
SINGBOX_CERT_DST="/home/$DEPLOY_USER/singbox/server"

# Docker 容器名
NGINX_CONTAINER="nginx"
SINGBOX_CONTAINER="singbox-server"

# 日志文件
LOG_FILE="/home/$DEPLOY_USER/smart_run.log"

