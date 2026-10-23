from django.contrib import admin

from .models import Place, Record, Setting, Token, TripLog, WxUser


@admin.register(WxUser)
class WxUserAdmin(admin.ModelAdmin):
    list_display = ("id", "nickname", "openid", "created_at", "last_login")
    search_fields = ("openid", "nickname")
    readonly_fields = ("openid", "unionid", "created_at", "last_login")


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "name", "role", "sort", "radius_m", "is_active", "latitude", "longitude")
    list_filter = ("role", "is_active")
    search_fields = ("name", "address")
    ordering = ("user", "sort", "id")


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "trip_name", "distance_m", "vibrate", "sound", "updated_at")
    search_fields = ("user__openid", "user__nickname", "trip_name")


@admin.register(TripLog)
class TripLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "trip_name", "total", "reached", "duration_s", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__openid", "trip_name")
    readonly_fields = ("place_names", "reached_names", "created_at")

    @admin.display(description="地址数")
    def total(self, obj: TripLog) -> int:
        return len(obj.place_names or [])

    @admin.display(description="到达数")
    def reached(self, obj: TripLog) -> int:
        return len(obj.reached_names or [])


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "place_name", "distance_m", "created_at")
    list_filter = ("created_at",)
    search_fields = ("place_name", "user__openid")


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "key", "created_at")
    search_fields = ("user__openid", "key")
    readonly_fields = ("key",)


admin.site.site_header = "「不怕睡过站」后台"
admin.site.site_title = "不怕睡过站"
admin.site.index_title = "数据管理"
