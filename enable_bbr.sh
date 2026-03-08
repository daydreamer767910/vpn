#!/bin/bash
# 一键开启 BBR
# 适用 Debian / Ubuntu 系统
# 运行前请用 root 或 sudo

set -e

echo "==== 检查内核版本 ===="
uname -r

echo "==== 加载 BBR 模块 ===="
sudo modprobe tcp_bbr

echo "==== 设置默认队列调度器为 fq ===="
sudo sysctl -w net.core.default_qdisc=fq

echo "==== 设置 TCP 拥塞算法为 bbr ===="
sudo sysctl -w net.ipv4.tcp_congestion_control=bbr

echo "==== 写入 /etc/sysctl.conf 永久生效 ===="
grep -q "net.core.default_qdisc=fq" /etc/sysctl.conf || echo "net.core.default_qdisc=fq" | sudo tee -a /etc/sysctl.conf
grep -q "net.ipv4.tcp_congestion_control=bbr" /etc/sysctl.conf || echo "net.ipv4.tcp_congestion_control=bbr" | sudo tee -a /etc/sysctl.conf

echo "==== 应用 sysctl 配置 ===="
sudo sysctl -p

echo "==== 验证 BBR ===="
sysctl net.ipv4.tcp_congestion_control
lsmod | grep bbr

echo "==== 完成 ===="
echo "BBR 已开启并永久生效"