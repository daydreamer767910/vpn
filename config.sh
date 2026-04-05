#!/bin/bash
# config.sh - 部署配置文件

# 最终用户
DEPLOY_USER="vpn"
USER_EMAIL="yourmail@gmail.com"

# 域名
DOMAIN="xxx.yyy.zzz"

# 时区
TIMEZONE="America/Los_Angeles"


# Reality TLS 配置
SNI="www.microsoft.com"

# DNS (One of prefer_ipv4 prefer_ipv6 ipv4_only ipv6_only.)
DNS_STRATEGY="prefer_ipv4"
DOMAIN_LOCAL_LIST=(".cn"
          "baidu.com"
          "qq.com"
          "weixin.qq.com"
          "weixinbridge.com"
          "servicewechat.com"
          "163.com"
          "jd.com"
          "taobao.com"
          "tmall.com"
          "pinduoduo.com"
          "1688.com"
          "alipay.com"
          "bilibili.com"
          "iqiyi.com"
          "youku.com"
          "douyin.com")
# endpoint
WG_HOSTIPS='"10.0.0.1/32","fd42:42:42::1/128"'
WG_SUBNET='"10.0.0.0/24","fd42:42:42::/64"'
WG_PORT=51820

# 证书目录
CERT_SRC="/etc/letsencrypt/live/$DOMAIN"
CERT_DST="/home/$DEPLOY_USER/cert"

# Docker 容器名
NGINX_CONTAINER="nginx"
SINGBOX_CONTAINER="singbox-server"

# 日志文件
LOG_FILE="/home/$DEPLOY_USER/log/smart_run.log"

# 定期检查证书
CRON_SCHEDULE="0 3 * * *"
