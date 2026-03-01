# journal/Dockerfile
FROM daydreamer767910/journal:latest

# 设置时区
ENV TZ=America/Los_Angeles

# 安装 tzdata 并设置系统时区
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 保留原启动命令
CMD ["./journal"]