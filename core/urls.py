from django.urls import path

from . import views

urlpatterns = [
    # 健康检查
    path("ping", views.ping),
    # 登录态
    path("login", views.login),
    path("logout", views.logout),
    path("profile", views.update_profile),
    # 行程地址（GET 列表 / POST 新增）
    path("places", views.places_root),
    path("places/reorder", views.reorder_places),
    path("places/<int:pk>", views.place_detail),
    # 提醒设置
    path("settings", views.user_setting),
    # 到站记录 & 行程存档
    path("records", views.records),
    path("trips", views.trip_logs),
    # 位置上报（服务端判距 + 防抖）
    path("position", views.report_position),
]
