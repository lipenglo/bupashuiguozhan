from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def index(request):
    """根路径给个可读的服务信息，方便部署后用浏览器确认服务活着。"""
    return JsonResponse(
        {
            "service": "不怕睡过站 · 后端",
            "version": "1.0.0",
            "api": "/api/ping",
            "admin": "/admin/",
        }
    )


urlpatterns = [
    path("", index),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
