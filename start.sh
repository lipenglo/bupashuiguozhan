#!/bin/sh
# 微信云托管启动脚本：迁移（带重试）→ 收集静态文件 → gunicorn
set -e

PORT="${PORT:-80}"

echo "[start] 数据库迁移"
n=1
while [ "$n" -le 5 ]; do
  if python manage.py migrate --noinput; then
    break
  fi
  echo "[start] 迁移失败（第 $n 次），5 秒后重试…"
  n=$((n + 1))
  sleep 5
done
if [ "$n" -gt 5 ]; then
  echo "[start] 数据库始终连不上，退出"
  exit 1
fi

echo "[start] 收集静态文件"
python manage.py collectstatic --noinput || true

echo "[start] 启动 gunicorn :$PORT"
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:$PORT" \
  --workers "${WORKERS:-2}" \
  --threads 4 \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
