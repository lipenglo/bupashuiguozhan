"""REST 接口（纯 Django，无 DRF）。

统一响应：
    成功 {"code": 0, "msg": "ok", "data": ...}
    失败 {"code": <非0>, "msg": "...", "data": null}

鉴权：请求头 X-Token: <token>（登录接口返回）。

路由一览见 core/urls.py；接口说明见 docs/接口文档.md。
"""
from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from functools import wraps
from math import asin, cos, radians, sin, sqrt
from typing import Any, Callable, Optional

from django.conf import settings
from django.db import connection, transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Place, Record, Setting, Token, TripLog, WxUser

log = logging.getLogger(__name__)

RADIUS_MIN, RADIUS_MAX = 50, 3000


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def ok(data: Any = None, msg: str = "ok") -> JsonResponse:
    return JsonResponse({"code": 0, "msg": msg, "data": data})


def err(msg: str, code: int = 1, status: int = 200) -> JsonResponse:
    return JsonResponse({"code": code, "msg": msg, "data": None}, status=status)


def read_json(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def current_token(request: HttpRequest) -> Optional[Token]:
    key = (request.headers.get("X-Token") or "").strip()
    if not key:
        return None
    token = Token.objects.select_related("user").filter(key=key).first()
    if token is None:
        return None
    expired = timezone.now() - timedelta(days=settings.TOKEN_TTL_DAYS)
    if token.created_at < expired:
        token.delete()
        return None
    return token


def current_user(request: HttpRequest) -> Optional[WxUser]:
    token = current_token(request)
    return token.user if token else None


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs):
        user = current_user(request)
        if user is None:
            return err("未登录或登录已过期", code=401, status=401)
        request.wx_user = user  # type: ignore[attr-defined]
        return view(request, *args, **kwargs)

    return wrapper


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点球面距离（米）。"""
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    d_lat = p2 - p1
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(p1) * cos(p2) * sin(d_lng / 2) ** 2
    return 2 * r * asin(min(1.0, sqrt(a)))


def normalize_roles(user: WxUser) -> None:
    """按顺序自动定角色：最后一个 = 终点，其余都算途经点。"""
    places = list(Place.objects.filter(user=user).order_by("sort", "id"))
    if not places:
        return
    last = len(places) - 1
    changed = []
    for idx, place in enumerate(places):
        role = Place.ROLE_END if idx == last else Place.ROLE_VIA
        if place.role != role:
            place.role = role
            changed.append(place)
    if changed:
        Place.objects.bulk_update(changed, ["role"])


def place_dict(p: Place) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "address": p.address,
        "latitude": p.latitude,
        "longitude": p.longitude,
        "role": p.role,
        "role_label": p.get_role_display(),
        "radius_m": p.radius_m,
        "is_active": p.is_active,
        "reuse": p.reuse,
        "sort": p.sort,
    }


def setting_dict(s: Setting) -> dict:
    return {
        "trip_name": s.trip_name,
        "distance_m": s.distance_m,
        "vibrate": s.vibrate,
        "sound": s.sound,
    }


def trip_log_dict(t: TripLog) -> dict:
    names = t.place_names or []
    reached = t.reached_names or []
    return {
        "id": t.id,
        "trip_name": t.trip_name,
        "place_names": names,
        "reached_names": reached,
        "total": len(names),
        "reached": len(reached),
        "duration_s": t.duration_s,
        "started_at": timezone.localtime(t.started_at).strftime("%Y-%m-%d %H:%M")
        if t.started_at
        else "",
        "created_at": timezone.localtime(t.created_at).strftime("%Y-%m-%d %H:%M"),
    }


def record_dict(r: Record) -> dict:
    return {
        "id": r.id,
        "place_name": r.place_name,
        "distance_m": r.distance_m,
        "created_at": timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_place(user: WxUser, pk: int) -> Optional[Place]:
    return Place.objects.filter(user=user, pk=pk).first()


def next_sort(user: WxUser) -> int:
    last = Place.objects.filter(user=user).order_by("-sort").values_list("sort", flat=True).first()
    return (last or 0) + 1


def validate_coords(latitude: Any, longitude: Any) -> tuple[Optional[float], Optional[float], str]:
    lat, lng = as_float(latitude), as_float(longitude)
    if lat is None or lng is None:
        return None, None, "经纬度不合法"
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None, "经纬度超出范围"
    return lat, lng, ""


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["GET"])
def ping(request: HttpRequest) -> JsonResponse:
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # pragma: no cover - 只有数据库故障时触发
        db_ok = False
        log.exception("数据库健康检查失败")
    return ok(
        {
            "pong": True,
            "service": "bupashuiguozhan",
            "version": settings.APP_VERSION,
            "db": db_ok,
            "server_time": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


# ---------------------------------------------------------------------------
# 微信登录
# ---------------------------------------------------------------------------
def wx_code2session(code: str) -> dict:
    """用 code 换 openid；没配 AppID/Secret 且允许调试时走 mock。"""
    appid, secret = settings.WX_APPID, settings.WX_SECRET
    if not appid or not secret:
        if settings.WX_MOCK_LOGIN:
            log.warning("WX_APPID/WX_SECRET 未配置，使用调试登录（所有调试端共用一个账号）")
            return {"openid": settings.WX_DEV_OPENID, "mock": True}
        return {"error": "服务端未配置 WX_APPID / WX_SECRET"}

    query = urllib.parse.urlencode(
        {"appid": appid, "secret": secret, "js_code": code, "grant_type": "authorization_code"}
    )
    url = f"https://api.weixin.qq.com/sns/jscode2session?{query}"
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.exception("code2session 请求失败")
        return {"error": f"网络异常：{exc}"}

    if data.get("errcode"):
        log.warning("code2session 返回错误：%s %s", data.get("errcode"), data.get("errmsg"))
        return {"error": f"{data.get('errcode')}: {data.get('errmsg')}"}
    if not data.get("openid"):
        return {"error": "微信未返回 openid"}
    return data


@csrf_exempt
@require_http_methods(["POST"])
def login(request: HttpRequest) -> JsonResponse:
    payload = read_json(request)
    code = str(payload.get("code", "")).strip()
    if not code:
        return err("缺少 code", status=400)

    session = wx_code2session(code)
    if "openid" not in session:
        return err(session.get("error", "登录失败"), code=401, status=401)

    user, created = WxUser.objects.get_or_create(
        openid=session["openid"], defaults={"unionid": session.get("unionid", "")}
    )
    if not created and session.get("unionid") and user.unionid != session["unionid"]:
        user.unionid = session["unionid"]
        user.save(update_fields=["unionid"])
    Setting.objects.get_or_create(user=user)

    # 允许多端同时在线（开发者工具 + 真机是同一个微信账号）
    token = Token.objects.create(key=secrets.token_hex(24), user=user)

    # 清理过期 token，避免无限增长
    expired = timezone.now() - timedelta(days=settings.TOKEN_TTL_DAYS)
    Token.objects.filter(user=user, created_at__lt=expired).delete()

    return ok(
        {
            "token": token.key,
            "user": {"id": user.id, "nickname": user.nickname, "avatar": user.avatar},
            "mock": bool(session.get("mock")),
            "expires_in": settings.TOKEN_TTL_DAYS * 24 * 3600,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def logout(request: HttpRequest) -> JsonResponse:
    token = current_token(request)
    if token:
        token.delete()
    return ok({"logout": True})


@csrf_exempt
@require_http_methods(["POST", "PUT", "PATCH"])
@login_required
def update_profile(request: HttpRequest) -> JsonResponse:
    """保存用户主动授权填的昵称 / 头像（可选，不授权也能用）。"""
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    payload = read_json(request)

    if "nickname" in payload:
        user.nickname = str(payload.get("nickname") or "").strip()[:64]
    if "avatar" in payload:
        user.avatar = str(payload.get("avatar") or "").strip()[:512]
    user.save(update_fields=["nickname", "avatar"])
    return ok({"nickname": user.nickname, "avatar": user.avatar})


# ---------------------------------------------------------------------------
# 行程地点：途经点 / 终点
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def places_list(request: HttpRequest) -> JsonResponse:
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    items = [place_dict(p) for p in Place.objects.filter(user=user).order_by("sort", "id")]
    return ok(
        {
            "places": items,
            "vias": [i for i in items if i["role"] == Place.ROLE_VIA],
            "end": next((i for i in items if i["role"] == Place.ROLE_END), None),
        }
    )


@login_required
@require_http_methods(["POST"])
def place_create(request: HttpRequest) -> JsonResponse:
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    payload = read_json(request)

    name = str(payload.get("name") or "").strip()[:64]
    if not name:
        return err("名称不能为空", status=400)

    latitude, longitude, message = validate_coords(payload.get("latitude"), payload.get("longitude"))
    if latitude is None or longitude is None:
        return err(message, status=400)

    setting, _ = Setting.objects.get_or_create(user=user)
    radius = as_int(payload.get("radius_m"))
    radius = clamp(radius if radius is not None else setting.distance_m, RADIUS_MIN, RADIUS_MAX)

    sort = as_int(payload.get("sort"))
    place = Place.objects.create(
        user=user,
        name=name,
        address=str(payload.get("address") or "")[:255],
        latitude=latitude,
        longitude=longitude,
        sort=sort if sort and sort > 0 else next_sort(user),
        radius_m=radius,
        reuse=to_bool(payload.get("reuse"), True),
        is_active=to_bool(payload.get("is_active"), True),
    )
    normalize_roles(user)
    place.refresh_from_db()
    return ok(place_dict(place))


@csrf_exempt
def places_root(request: HttpRequest) -> JsonResponse:
    """GET = 列表，POST = 新增（合成一个入口，避免 405）。"""
    if request.method == "GET":
        return places_list(request)
    if request.method == "POST":
        return place_create(request)
    return err("方法不允许", code=405, status=405)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def reorder_places(request: HttpRequest) -> JsonResponse:
    """整表重排：ids 按新顺序给出，一次写完 sort 并重算角色。"""
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    raw = read_json(request).get("ids") or []
    if not isinstance(raw, list):
        return err("ids 不合法", status=400)

    try:
        ids = [int(i) for i in raw]
    except (TypeError, ValueError):
        return err("ids 不合法", status=400)

    # 去重但保留传入顺序，避免重复 id 把 sort 写乱
    seen: set[int] = set()
    unique_ids: list[int] = []
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)
    ids = unique_ids

    with transaction.atomic():
        all_places = list(Place.objects.select_for_update().filter(user=user).order_by("sort", "id"))
        by_id = {p.id: p for p in all_places}
        given = [pid for pid in ids if pid in by_id]
        if not given:
            return err("ids 不合法", status=400)

        # 没传到的（旧数据 / 其他入口）保持原相对顺序排在后面
        given_set = set(given)
        final_ids = given + [p.id for p in all_places if p.id not in given_set]

        changed = []
        for idx, pid in enumerate(final_ids, start=1):
            place = by_id[pid]
            if place.sort != idx:
                place.sort = idx
                changed.append(place)
        if changed:
            Place.objects.bulk_update(changed, ["sort"])
        normalize_roles(user)
    return ok({"ids": final_ids})


@csrf_exempt
@require_http_methods(["GET", "PATCH", "PUT", "DELETE"])
@login_required
def place_detail(request: HttpRequest, pk: int) -> JsonResponse:
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    place = get_place(user, pk)
    if place is None:
        return err("地址不存在", code=404, status=404)

    if request.method == "GET":
        return ok(place_dict(place))

    if request.method == "DELETE":
        place.delete()
        normalize_roles(user)
        return ok({"deleted": pk})

    payload = read_json(request)

    if "name" in payload:
        name = str(payload.get("name") or "").strip()[:64]
        if not name:
            return err("名称不能为空", status=400)
        place.name = name

    if "radius_m" in payload:
        radius = as_int(payload.get("radius_m"))
        if radius is None:
            return err("触发半径不合法", status=400)
        place.radius_m = clamp(radius, RADIUS_MIN, RADIUS_MAX)

    if "sort" in payload:
        sort = as_int(payload.get("sort"))
        if sort is None:
            return err("排序不合法", status=400)
        place.sort = max(0, sort)

    if "is_active" in payload:
        place.is_active = to_bool(payload.get("is_active"), place.is_active)
    if "reuse" in payload:
        place.reuse = to_bool(payload.get("reuse"), place.reuse)
    if "address" in payload:
        place.address = str(payload.get("address") or "")[:255]

    if "latitude" in payload or "longitude" in payload:
        latitude, longitude, message = validate_coords(
            payload.get("latitude", place.latitude), payload.get("longitude", place.longitude)
        )
        if latitude is None or longitude is None:
            return err(message, status=400)
        place.latitude, place.longitude = latitude, longitude

    place.save()
    normalize_roles(user)
    place.refresh_from_db()
    return ok(place_dict(place))


# ---------------------------------------------------------------------------
# 提醒设置
# ---------------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH"])
@login_required
def user_setting(request: HttpRequest) -> JsonResponse:
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    setting, _ = Setting.objects.get_or_create(user=user)

    if request.method == "GET":
        return ok(setting_dict(setting))

    payload = read_json(request)

    if "trip_name" in payload:
        setting.trip_name = str(payload.get("trip_name") or "").strip()[:32]
    if "distance_m" in payload:
        distance = as_int(payload.get("distance_m"))
        if distance is None:
            return err("提醒距离不合法", status=400)
        setting.distance_m = clamp(distance, RADIUS_MIN, RADIUS_MAX)
    if "vibrate" in payload:
        setting.vibrate = to_bool(payload.get("vibrate"), setting.vibrate)
    if "sound" in payload:
        setting.sound = to_bool(payload.get("sound"), setting.sound)
    setting.save()

    # 提醒距离是全局设置：改完立刻同步到已有地址，避免新旧半径不一致
    if "distance_m" in payload:
        Place.objects.filter(user=user).update(radius_m=setting.distance_m)

    return ok(setting_dict(setting))


# ---------------------------------------------------------------------------
# 到站记录 & 行程存档
# ---------------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["GET", "POST"])
@login_required
def records(request: HttpRequest) -> JsonResponse:
    user: WxUser = request.wx_user  # type: ignore[attr-defined]

    if request.method == "POST":
        payload = read_json(request)
        name = str(payload.get("place_name") or "").strip()[:64]
        if not name:
            return err("place_name 不能为空", status=400)

        latitude = as_float(payload.get("latitude"))
        longitude = as_float(payload.get("longitude"))
        # place_id 只接受当前用户的地址，避免越权关联
        place = get_place(user, as_int(payload.get("place_id")) or 0)
        record = Record.objects.create(
            user=user,
            place=place,
            place_name=name,
            distance_m=max(0, as_int(payload.get("distance_m")) or 0),
            latitude=latitude,
            longitude=longitude,
        )
        return ok(record_dict(record))

    limit = clamp(as_int(request.GET.get("limit")) or 50, 1, 100)
    qs = Record.objects.filter(user=user)[:limit]
    return ok({"records": [record_dict(r) for r in qs]})


@csrf_exempt
@require_http_methods(["GET", "POST"])
@login_required
def trip_logs(request: HttpRequest) -> JsonResponse:
    """行程结束后存档：POST 保存本次行程，GET 回看历史。"""
    user: WxUser = request.wx_user  # type: ignore[attr-defined]

    if request.method == "POST":
        payload = read_json(request)
        raw_places = payload.get("place_names")
        raw_reached = payload.get("reached_names")
        place_names = (
            [str(x)[:64] for x in raw_places[:50]] if isinstance(raw_places, list) else []
        )
        reached_names = (
            [str(x)[:64] for x in raw_reached[:50]] if isinstance(raw_reached, list) else []
        )
        started_ms = payload.get("started_at") or 0
        started_at = None
        if started_ms:
            try:
                started_at = timezone.make_aware(datetime.fromtimestamp(float(started_ms) / 1000))
            except (TypeError, ValueError, OSError, OverflowError):
                started_at = None

        trip = TripLog.objects.create(
            user=user,
            trip_name=str(payload.get("trip_name") or "")[:32],
            place_names=place_names,
            reached_names=reached_names,
            started_at=started_at,
            duration_s=max(0, as_int(payload.get("duration_s")) or 0),
        )
        return ok(trip_log_dict(trip))

    limit = clamp(as_int(request.GET.get("limit")) or 20, 1, 50)
    qs = TripLog.objects.filter(user=user)[:limit]
    return ok({"trips": [trip_log_dict(t) for t in qs]})


# ---------------------------------------------------------------------------
# 位置上报：服务端判定是否进入某个地址的半径
# ---------------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
@login_required
def report_position(request: HttpRequest) -> JsonResponse:
    """小程序上报当前坐标。

    返回 triggered=True 时：小程序震动提醒 + 记一条 Record；
    后续可在这里调用订阅消息接口，实现锁屏也能收到的推送。
    """
    user: WxUser = request.wx_user  # type: ignore[attr-defined]
    payload = read_json(request)
    latitude, longitude, message = validate_coords(payload.get("latitude"), payload.get("longitude"))
    if latitude is None or longitude is None:
        return err(message, status=400)

    targets = list(Place.objects.filter(user=user, is_active=True).order_by("sort", "id"))
    nearest: Optional[tuple[Place, float]] = None
    for place in targets:
        distance = haversine_m(latitude, longitude, place.latitude, place.longitude)
        if distance <= place.radius_m and (nearest is None or distance < nearest[1]):
            nearest = (place, distance)

    if nearest is None:
        return ok({"triggered": False})

    place, distance = nearest
    cooldown_from = timezone.now() - timedelta(seconds=settings.TRIGGER_COOLDOWN_S)
    recent = Record.objects.filter(
        user=user, place=place, created_at__gte=cooldown_from
    ).exists()
    if recent:
        return ok(
            {
                "triggered": False,
                "reason": "recently_triggered",
                "place": place_dict(place),
                "distance_m": int(distance),
            }
        )

    record = Record.objects.create(
        user=user,
        place=place,
        place_name=place.name,
        distance_m=int(distance),
        latitude=latitude,
        longitude=longitude,
    )
    return ok(
        {
            "triggered": True,
            "reason": "arrived",
            "distance_m": int(distance),
            "place": place_dict(place),
            "record": record_dict(record),
        }
    )
