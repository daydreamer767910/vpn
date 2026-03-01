# journal/Dockerfile
FROM daydreamer767910/journal:latest

# 设置时区
ENV TZ=America/Los_Angeles

# 安装 tzdata 并设置系统时区
RUN apk add --no-cache tzdata \
    && cp /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

# 保留原启动命令
CMD ["./journal"]