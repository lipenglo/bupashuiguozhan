# 后端（Django）

Python 3.11+ / Django 5。生产用 MySQL（微信云托管推荐）；本地调试未配置 `MYSQL_HOST` 时用 SQLite。生产（`DJANGO_DEBUG=0`）如果既没有 `MYSQL_HOST` 也没有显式 `SQLITE_DIR`，启动会直接报错，避免数据写进容器临时盘。

## 本地安装

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # 填 WX_APPID / WX_SECRET，本地可留空走调试登录
python3 manage.py migrate
python3 manage.py runserver 0.0.0.0:8000
```

验证：

```bash
curl http://127.0.0.1:8000/api/ping
```

## 测试

```bash
python3 manage.py test
```

覆盖登录、鉴权、地址 CRUD / 排序、设置同步、到站判定、行程存档、记录、退出与跨用户隔离。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DJANGO_DEBUG` | `1` | 生产设 `0` |
| `DJANGO_SECRET_KEY` | 开发默认值 | 生产必须配固定随机值 |
| `DJANGO_ALLOWED_HOSTS` | `*` | 逗号分隔 |
| `WX_APPID` / `WX_SECRET` | 空 | 为空且允许调试时走 mock 登录 |
| `WX_MOCK_LOGIN` | 跟随 DEBUG | 生产必须 `0` |
| `WX_DEV_OPENID` | `dev_openid_0001` | 调试登录共用账号 |
| `TOKEN_TTL_DAYS` | `30` | 登录态有效期 |
| `TRIGGER_COOLDOWN_S` | `60` | 到站记录冷却 |
| `MYSQL_HOST` 等 | 空 | 生产必填；配置后使用 MySQL |
| `SQLITE_DIR` | `server/` | 仅本地调试；云托管 CFS 备用方案，生产建议直接用 MySQL |

## 管理后台

```bash
python3 manage.py createsuperuser
```

访问 `/admin/`，可查看用户、地址、设置、记录、行程与登录态。
静态文件由 WhiteNoise 提供，云托管不需要额外 Nginx。

## 云托管

```bash
bash ../tools/build_server_zip.sh   # 在仓库根目录执行
```

上传 `dist/bupashuiguozhan-server.zip`，端口 `80`，环境变量见 [../docs/云托管部署.md](../docs/云托管部署.md)。

本地用 Docker 验证：

```bash
docker build -t bupashuiguozhan-server .
docker run --rm -p 8080:80 -e DJANGO_DEBUG=0 -e DJANGO_ALLOWED_HOSTS='*' bupashuiguozhan-server
```
