"""数据模型：微信用户、登录态、行程地点、提醒设置、行程记录。

设计要点：
- 一个 openid 一个用户；登录态独立成表，方便多端在线与失效清理。
- 地址按 sort 排序，最后一个自动是终点，其余都是途经点（没有「起点」概念）。
- 行程结束写 TripLog 存档；到站提醒写 Record 用于统计与防抖。
"""
from django.db import models


class WxUser(models.Model):
    """一个微信 openid 对应一个用户。"""

    openid = models.CharField("openid", max_length=64, unique=True, db_index=True)
    unionid = models.CharField("unionid", max_length=64, blank=True, default="")
    nickname = models.CharField("昵称", max_length=64, blank=True, default="")
    avatar = models.CharField("头像", max_length=512, blank=True, default="")
    created_at = models.DateTimeField("注册时间", auto_now_add=True)
    last_login = models.DateTimeField("最近登录", auto_now=True)

    class Meta:
        verbose_name = "微信用户"
        verbose_name_plural = "微信用户"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.nickname or '匿名'} ({self.openid[:10]}…)"


class Token(models.Model):
    """登录态：小程序把 key 放在请求头 X-Token 里。"""

    key = models.CharField("token", max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(WxUser, on_delete=models.CASCADE, related_name="tokens")
    created_at = models.DateTimeField("签发时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "登录态"
        verbose_name_plural = "登录态"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} · {self.key[:8]}…"


class Place(models.Model):
    """行程中的一个地址：除最后一个外都是途经点，最后一个是终点。"""

    ROLE_START = "start"  # 仅兼容历史数据，新数据不再产生
    ROLE_VIA = "via"
    ROLE_END = "end"
    ROLE_CHOICES = [
        (ROLE_VIA, "途经点"),
        (ROLE_END, "终点"),
    ]

    user = models.ForeignKey(WxUser, on_delete=models.CASCADE, related_name="places")
    name = models.CharField("名称", max_length=64)
    address = models.CharField("地址", max_length=255, blank=True, default="")
    latitude = models.FloatField("纬度")
    longitude = models.FloatField("经度")
    role = models.CharField("类型", max_length=8, choices=ROLE_CHOICES, default=ROLE_VIA)
    sort = models.PositiveIntegerField("排序", default=0)
    radius_m = models.PositiveIntegerField("触发半径(米)", default=300)
    is_active = models.BooleanField("参与提醒", default=True)
    reuse = models.BooleanField("重复使用", default=True)
    use_count = models.PositiveIntegerField("使用次数", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "行程地址"
        verbose_name_plural = "行程地址"
        ordering = ["sort", "id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.latitude:.5f},{self.longitude:.5f})"


class Setting(models.Model):
    """提醒设置，一个用户一条。"""

    user = models.OneToOneField(WxUser, on_delete=models.CASCADE, related_name="setting")
    trip_name = models.CharField("行程名称", max_length=32, blank=True, default="")
    distance_m = models.PositiveIntegerField("提前提醒距离(米)", default=500)
    vibrate = models.BooleanField("震动", default=True)
    sound = models.BooleanField("声音", default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "提醒设置"
        verbose_name_plural = "提醒设置"

    def __str__(self) -> str:
        return f"{self.user} · {self.distance_m}m"


class TripLog(models.Model):
    """一次行程结束后的存档（终点到达或手动停止时写入）。"""

    user = models.ForeignKey(WxUser, on_delete=models.CASCADE, related_name="trip_logs")
    trip_name = models.CharField("行程名称", max_length=32, blank=True, default="")
    place_names = models.JSONField("行程里的地址", default=list, blank=True)
    reached_names = models.JSONField("实际到达的地址", default=list, blank=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    duration_s = models.PositiveIntegerField("用时(秒)", default=0)
    created_at = models.DateTimeField("结束时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "行程记录"
        verbose_name_plural = "行程记录"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} 的行程 {self.trip_name or '未命名'} @ {self.created_at:%m-%d %H:%M}"


class Record(models.Model):
    """一次到站提醒的记录（防抖 + 统计用）。"""

    user = models.ForeignKey(WxUser, on_delete=models.CASCADE, related_name="records")
    place = models.ForeignKey(
        "Place", null=True, blank=True, on_delete=models.SET_NULL, related_name="records"
    )
    place_name = models.CharField("目的地", max_length=64)
    distance_m = models.PositiveIntegerField("提醒距离(米)")
    latitude = models.FloatField("纬度", null=True, blank=True)
    longitude = models.FloatField("经度", null=True, blank=True)
    created_at = models.DateTimeField("时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "到站记录"
        verbose_name_plural = "到站记录"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} → {self.place_name} @ {self.created_at:%m-%d %H:%M}"
