"""Django 的 ASGI 入口（云托管默认用 wsgi，这里保留给异步部署）。"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
