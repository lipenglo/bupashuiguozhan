"""Django 配置：敏感项全部来自环境变量 / .env，不硬编码。

本地开发：复制 .env.example 为 .env，按需填写。
云托管部署：在「服务设置 → 环境变量」里配置同名变量。
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    """极简 .env 加载器，避免多装 python-dotenv；已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


load_env(BASE_DIR / ".env")

DEBUG = env_bool("DJANGO_DEBUG", True)

_secret = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not _secret:
    if DEBUG:
        _secret = "dev-only-insecure-key-change-me"
    else:
        # 没配密钥也不让服务起不来，但每次重启会换 key（接口 token 存库，不受影响）
        _secret = secrets.token_urlsafe(50)
        logging.getLogger(__name__).warning(
            "DJANGO_SECRET_KEY 未配置，已生成临时密钥；正式环境请在环境变量里配置固定值"
        )
SECRET_KEY = _secret

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()
]

INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# 数据库
#   生产（DEBUG=0）：必须用 MySQL；或显式挂 CFS 后设 SQLITE_DIR
#   本地（DEBUG=1）：没配 MySQL 时用 SQLite，方便不起数据库直接调试
#
# 环境变量兼容两套写法：
#   自建 / 本地：MYSQL_HOST + MYSQL_PORT + MYSQL_DATABASE + MYSQL_USER
#   微信云托管：MYSQL_ADDRESS=host:port + MYSQL_USERNAME（+ MYSQL_DATABASE 可选）
# ---------------------------------------------------------------------------
_mysql_address = os.environ.get("MYSQL_ADDRESS", "").strip()
_mysql_host = os.environ.get("MYSQL_HOST", "").strip()
_mysql_port = os.environ.get("MYSQL_PORT", "").strip() or "3306"

if not _mysql_host and _mysql_address:
    _addr_host, _, _addr_port = _mysql_address.partition(":")
    _mysql_host = _addr_host.strip()
    if _addr_port.strip():
        _mysql_port = _addr_port.strip()

_mysql_database = os.environ.get("MYSQL_DATABASE", "").strip()
if not _mysql_database:
    # 云托管模板默认库名是 django_demo；本地默认 bupashuiguozhan
    _mysql_database = "django_demo" if _mysql_address else "bupashuiguozhan"

_mysql_user = (
    os.environ.get("MYSQL_USER", "").strip()
    or os.environ.get("MYSQL_USERNAME", "").strip()
    or "root"
)

if _mysql_host:
    import pymysql

    # Django 会检查驱动版本号，PyMySQL 需要伪装成 mysqlclient 1.4.6+
    pymysql.version_info = (1, 4, 6, "final", 0)
    pymysql.install_as_MySQLdb()

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": _mysql_database,
            "USER": _mysql_user,
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
            "HOST": _mysql_host,
            "PORT": _mysql_port,
            "OPTIONS": {"charset": "utf8mb4"},
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }

    # 微信云托管内置 MySQL 目前仍是 5.7.18，而 Django 4.2 默认要求 8.0.11+。
    # 本项目只用到 5.7 已支持的字段与 SQL（含 JSONField），因此把最低版本降回 5.7；
    # 云托管升级 MySQL 8 后可删除这段兼容代码。
    from django.db.backends.mysql.features import DatabaseFeatures as MySQLFeatures

    MySQLFeatures.minimum_database_version = (5, 7)
else:
    # 生产环境不允许悄悄落到容器临时盘上的 SQLite（重新部署就丢数据）。
    # 确实要用 CFS 持久化 SQLite 时，显式设置 SQLITE_DIR 作为白名单。
    if not DEBUG and not os.environ.get("SQLITE_DIR"):
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "生产环境必须配置 MYSQL_HOST 或 MYSQL_ADDRESS（推荐），"
            "或在挂载 CFS 后显式设置 SQLITE_DIR；不要使用容器临时磁盘上的 db.sqlite3。"
        )

    sqlite_dir = Path(os.environ.get("SQLITE_DIR", str(BASE_DIR))).expanduser()
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_dir / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 反向代理（云托管 / Nginx）
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

_csrf = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
if _csrf:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(",") if o.strip()]

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------------------
# 微信小程序 / 业务参数
# ---------------------------------------------------------------------------
WX_APPID = os.environ.get("WX_APPID", "").strip()
WX_SECRET = os.environ.get("WX_SECRET", "").strip()

# 没配 AppID/Secret 时的调试登录：默认只在 DEBUG 下开启。
# 生产环境想临时打开，显式设置 WX_MOCK_LOGIN=1（会提示前端「调试登录」）。
WX_MOCK_LOGIN = env_bool("WX_MOCK_LOGIN", DEBUG)
WX_DEV_OPENID = os.environ.get("WX_DEV_OPENID", "dev_openid_0001")

TOKEN_TTL_DAYS = env_int("TOKEN_TTL_DAYS", 30)
TRIGGER_COOLDOWN_S = env_int("TRIGGER_COOLDOWN_S", 60)
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
