# 使用官方 Python 镜像
FROM python:3.12-slim

WORKDIR /app

# 安装 Flask
RUN pip install flask gunicorn

# 复制 Flask 服务代码和发布目录
COPY app.py /app/

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:app"]