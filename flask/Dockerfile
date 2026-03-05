# 使用官方 Python 镜像
FROM python:3.12-slim

WORKDIR /app

# 安装 Flask
RUN pip install --no-cache-dir flask

# 复制 Flask 服务代码和发布目录
COPY app.py /app/

# 暴露端口
EXPOSE 5000

# 启动 Flask
CMD ["python", "app.py"]