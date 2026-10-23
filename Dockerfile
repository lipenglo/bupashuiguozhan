# 微信云托管：Django + gunicorn
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=0 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chmod +x start.sh && mkdir -p staticfiles

EXPOSE 80

# 云托管默认抓 80 端口的 HTTP 服务
CMD ["./start.sh"]
