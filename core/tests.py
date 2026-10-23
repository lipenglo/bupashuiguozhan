"""接口回归测试：python manage.py test

覆盖登录、鉴权、地址增删改查/排序、提醒距离同步、到站判定、行程存档、
记录查询、退出登录与跨用户隔离。
"""
from __future__ import annotations

import json

from django.test import Client, TestCase, override_settings

from .models import Place, Record, Token, TripLog, WxUser


@override_settings(WX_APPID="", WX_SECRET="", WX_MOCK_LOGIN=True)
class ApiTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    # ---------------- 辅助 ----------------
    def post(self, path: str, payload: dict, token: str = "") -> dict:
        headers = {"HTTP_X_TOKEN": token} if token else {}
        resp = self.client.post(
            path, data=json.dumps(payload), content_type="application/json", **headers
        )
        return {"status": resp.status_code, "body": resp.json()}

    def get(self, path: str, token: str = "") -> dict:
        headers = {"HTTP_X_TOKEN": token} if token else {}
        resp = self.client.get(path, **headers)
        return {"status": resp.status_code, "body": resp.json()}

    def patch(self, path: str, payload: dict, token: str = "") -> dict:
        headers = {"HTTP_X_TOKEN": token} if token else {}
        resp = self.client.patch(
            path, data=json.dumps(payload), content_type="application/json", **headers
        )
        return {"status": resp.status_code, "body": resp.json()}

    def delete(self, path: str, token: str = "") -> dict:
        headers = {"HTTP_X_TOKEN": token} if token else {}
        resp = self.client.delete(path, **headers)
        return {"status": resp.status_code, "body": resp.json()}

    def login(self) -> str:
        result = self.post("/api/login", {"code": "unit-test-code"})
        self.assertEqual(result["status"], 200, result)
        self.assertEqual(result["body"]["code"], 0, result)
        self.assertTrue(result["body"]["data"]["mock"])
        return result["body"]["data"]["token"]

    def add_place(self, token: str, name: str, lat: float, lng: float, **extra) -> dict:
        payload = {"name": name, "latitude": lat, "longitude": lng}
        payload.update(extra)
        result = self.post("/api/places", payload, token)
        self.assertEqual(result["body"]["code"], 0, result)
        return result["body"]["data"]

    # ---------------- 基础 ----------------
    def test_ping(self):
        result = self.get("/api/ping")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["code"], 0)
        self.assertTrue(result["body"]["data"]["pong"])
        self.assertTrue(result["body"]["data"]["db"])

    def test_login_requires_code(self):
        result = self.post("/api/login", {})
        self.assertEqual(result["status"], 400)
        self.assertNotEqual(result["body"]["code"], 0)

    def test_auth_required(self):
        result = self.get("/api/places")
        self.assertEqual(result["status"], 401)
        self.assertEqual(result["body"]["code"], 401)

    def test_login_creates_user_and_setting(self):
        token = self.login()
        self.assertTrue(Token.objects.filter(key=token).exists())
        user = WxUser.objects.get()
        self.assertTrue(hasattr(user, "setting"))

    @override_settings(WX_APPID="", WX_SECRET="", WX_MOCK_LOGIN=False)
    def test_mock_login_can_be_disabled(self):
        result = self.post("/api/login", {"code": "unit-test-code"})
        self.assertEqual(result["status"], 401)
        self.assertIn("WX_APPID", result["body"]["msg"])
        self.assertFalse(Token.objects.exists())

    def test_expired_token_rejected(self):
        token = self.login()
        Token.objects.filter(key=token).update(created_at="2020-01-01T00:00:00Z")
        result = self.get("/api/places", token)
        self.assertEqual(result["status"], 401)

    # ---------------- 地址 ----------------
    def test_place_crud_and_roles(self):
        token = self.login()
        first = self.add_place(token, "循礼门", 30.5830, 114.2830)
        # 只有一个地址时，它既是途经点也是终点（最后一个 = 终点）
        self.assertEqual(first["role"], Place.ROLE_END)
        second = self.add_place(token, "汉口站", 30.6180, 114.2540, address="发展大道")
        self.assertEqual(second["role"], Place.ROLE_END)
        self.assertEqual(Place.objects.get(pk=first["id"]).role, Place.ROLE_VIA)

        listing = self.get("/api/places", token)["body"]["data"]
        self.assertEqual([p["name"] for p in listing["places"]], ["循礼门", "汉口站"])
        self.assertEqual(listing["end"]["name"], "汉口站")
        self.assertEqual(len(listing["vias"]), 1)

        updated = self.patch(f"/api/places/{first['id']}", {"name": "循礼门站"}, token)
        self.assertEqual(updated["body"]["data"]["name"], "循礼门站")

        deleted = self.delete(f"/api/places/{second['id']}", token)
        self.assertEqual(deleted["body"]["data"]["deleted"], second["id"])
        listing = self.get("/api/places", token)["body"]["data"]
        self.assertEqual(listing["places"][0]["role"], Place.ROLE_END)

    def test_invalid_coordinates(self):
        token = self.login()
        result = self.post("/api/places", {"name": "坏点", "latitude": 999, "longitude": 0}, token)
        self.assertEqual(result["status"], 400)
        self.assertNotEqual(result["body"]["code"], 0)

    def test_reorder(self):
        token = self.login()
        a = self.add_place(token, "A", 30.1, 114.1)
        b = self.add_place(token, "B", 30.2, 114.2)
        c = self.add_place(token, "C", 30.3, 114.3)

        result = self.post("/api/places/reorder", {"ids": [c["id"], a["id"], b["id"]]}, token)
        self.assertEqual(result["body"]["data"]["ids"], [c["id"], a["id"], b["id"]])

        listing = self.get("/api/places", token)["body"]["data"]["places"]
        self.assertEqual([p["name"] for p in listing], ["C", "A", "B"])
        self.assertEqual([p["role"] for p in listing], ["via", "via", "end"])

        # 重复 id 不能把顺序写乱
        duplicated = self.post(
            "/api/places/reorder", {"ids": [b["id"], b["id"], a["id"]]}, token
        )["body"]["data"]["ids"]
        self.assertEqual(duplicated, [b["id"], a["id"], c["id"]])

    def test_place_detail_scoped_to_user(self):
        token = self.login()
        place = self.add_place(token, "我的站", 30.5, 114.5)
        # 另一个用户（不同 openid）不能访问
        with override_settings(WX_DEV_OPENID="other_openid"):
            other = self.login()
        result = self.get(f"/api/places/{place['id']}", other)
        self.assertEqual(result["status"], 404)

    # ---------------- 设置 ----------------
    def test_settings_and_radius_sync(self):
        token = self.login()
        place = self.add_place(token, "站", 30.5, 114.5)
        self.assertEqual(place["radius_m"], 500)  # 默认距离

        result = self.patch("/api/settings", {"distance_m": 100, "sound": False}, token)
        data = result["body"]["data"]
        self.assertEqual(data["distance_m"], 100)
        self.assertFalse(data["sound"])

        place = Place.objects.get(pk=place["id"])
        self.assertEqual(place.radius_m, 100)

    def test_trip_name(self):
        token = self.login()
        result = self.patch("/api/settings", {"trip_name": "上班通勤"}, token)
        self.assertEqual(result["body"]["data"]["trip_name"], "上班通勤")

    # ---------------- 到站判定 ----------------
    def test_position_triggers_once(self):
        token = self.login()
        self.patch("/api/settings", {"distance_m": 100}, token)
        self.add_place(token, "终点站", 30.6000, 114.3000)

        far = self.post("/api/position", {"latitude": 31.0, "longitude": 115.0}, token)
        self.assertFalse(far["body"]["data"]["triggered"])

        near = self.post("/api/position", {"latitude": 30.60005, "longitude": 114.30005}, token)
        self.assertTrue(near["body"]["data"]["triggered"], near)
        self.assertEqual(near["body"]["data"]["place"]["name"], "终点站")
        self.assertEqual(Record.objects.count(), 1)

        again = self.post("/api/position", {"latitude": 30.60005, "longitude": 114.30005}, token)
        self.assertFalse(again["body"]["data"]["triggered"])
        self.assertEqual(again["body"]["data"]["reason"], "recently_triggered")
        self.assertEqual(Record.objects.count(), 1)

    def test_position_ignores_inactive_place(self):
        token = self.login()
        place = self.add_place(token, "停用站", 30.6, 114.3)
        self.patch(f"/api/places/{place['id']}", {"is_active": False}, token)
        result = self.post("/api/position", {"latitude": 30.6, "longitude": 114.3}, token)
        self.assertFalse(result["body"]["data"]["triggered"])

    # ---------------- 记录 ----------------
    def test_trip_logs(self):
        token = self.login()
        created = self.post(
            "/api/trips",
            {
                "trip_name": "上班通勤",
                "place_names": ["循礼门", "江汉路", "汉口站"],
                "reached_names": ["循礼门", "江汉路"],
                "started_at": 1760000000000,
                "duration_s": 1800,
            },
            token,
        )
        data = created["body"]["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["reached"], 2)

        listing = self.get("/api/trips?limit=20", token)["body"]["data"]["trips"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["trip_name"], "上班通勤")
        self.assertEqual(TripLog.objects.count(), 1)

    def test_records_api(self):
        token = self.login()
        created = self.post("/api/records", {"place_name": "汉口站", "distance_m": 42}, token)
        self.assertEqual(created["body"]["code"], 0)
        listing = self.get("/api/records?limit=10", token)["body"]["data"]["records"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["place_name"], "汉口站")

    # ---------------- 退出 ----------------
    def test_logout_invalidates_token(self):
        token = self.login()
        self.assertEqual(self.post("/api/logout", {}, token)["body"]["code"], 0)
        self.assertEqual(self.get("/api/places", token)["status"], 401)
