"""后端冒烟测试：导入检查 + 关键流程（登录/录入/建议/管理员）打通。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# 使用独立的测试数据目录，避免污染正式库
TEST_ROOT = Path(__file__).resolve().parent / "_tmp"
if TEST_ROOT.exists():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
TEST_ROOT.mkdir(parents=True, exist_ok=True)
(TEST_ROOT / "data").mkdir(parents=True, exist_ok=True)
(TEST_ROOT / "logs").mkdir(parents=True, exist_ok=True)
(TEST_ROOT / "frontend").mkdir(parents=True, exist_ok=True)

os.environ["SSP_DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["SSP_LOG_DIR"] = str(TEST_ROOT / "logs")
os.environ["SSP_DB_PATH"] = str(TEST_ROOT / "data" / "test.db")
os.environ["SSP_LOG_FILE"] = str(TEST_ROOT / "logs" / "test.log")
os.environ["SSP_FRONTEND_DIR"] = str(TEST_ROOT / "frontend")
os.environ["SSP_DEBUG"] = "true"
os.environ["SSP_WRITE_COOLDOWN_SECONDS"] = "30"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


def main() -> int:
    with TestClient(app) as client:
        print("\n[1] 健康检查与登录")
        r = client.get("/healthz")
        check("healthz", r.status_code == 200 and r.json().get("status") == "ok", r.text[:200])

        r = client.get("/api/auth/admin/parts")
        check("admin/parts", r.status_code == 200 and r.json().get("parts") == 3, r.text[:200])

        r = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@12345"})
        check("普通登录拒绝管理员", r.status_code == 403, r.text[:200])

        r = client.post(
            "/api/auth/admin/login",
            json={"username": "admin", "passwords": ["Admin@12345", "Admin#67890"], "remember": False},
        )
        check("管理员密码段数不足被拒", r.status_code == 401, r.text[:200])

        r = client.post(
            "/api/auth/admin/login",
            json={
                "username": "admin",
                "passwords": ["Admin@12345", "Admin#67890", "Admin$24680"],
                "remember": True,
            },
        )
        check("管理员多段登录成功", r.status_code == 200, r.text[:300])
        admin_client = client

        print("\n[2] 管理员创建用户")
        r = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "User@12345", "language": "zh", "theme": "dark"},
        )
        check("创建用户", r.status_code == 201, r.text[:300])
        alice_id = r.json().get("id") if r.status_code == 201 else None

        r = admin_client.post("/api/admin/users", json={"username": "alice", "password": "User@12345"})
        check("重复用户名被拒", r.status_code == 409, r.text[:200])

        r = admin_client.get("/api/admin/users", params={"q": "ali", "page": 1, "page_size": 10})
        found = [item.get("username") for item in r.json().get("items", [])] if r.status_code == 200 else []
        check(
            "用户搜索分页",
            r.status_code == 200 and r.json().get("total", 0) == 1 and found == ["alice"],
            r.text[:300],
        )

        print("\n[3] 普通用户数据流")
        user = TestClient(app)
        r = user.post("/api/auth/login", json={"username": "alice", "password": "User@12345", "remember": True})
        check("用户登录", r.status_code == 200, r.text[:300])

        r = user.get("/api/records")
        check("空记录列表", r.status_code == 200 and r.json().get("total") == 0, r.text[:200])

        r = user.post(
            "/api/records",
            json={
                "sleep_time": "01:30",
                "wake_time": "09:00",
                "latest_wake_time": "08:00",
                "earliest_sleep_time": "23:00",
                "meal_breakfast": True,
                "meal_lunch": True,
                "meal_dinner": False,
                "note": "测试记录",
            },
        )
        check("新增睡眠记录", r.status_code == 201, r.text[:400])
        record_id = r.json().get("id") if r.status_code == 201 else None
        check("时长计算(1:30->9:00 = 450)", r.json().get("duration_minutes") == 450, r.text[:200])

        r = user.post("/api/records", json={"sleep_time": "25:00", "wake_time": "09:00"})
        check("非法时间被拒", r.status_code == 422, r.text[:200])

        r = user.post("/api/records", json={"sleep_time": "02:00", "wake_time": "09:00"})
        check("30秒防重复提交", r.status_code == 429, r.text[:200])

        # 防重复提交（30 秒节流）已在上一步验证；这里关掉节流以便继续测试写入接口
        from app.config import settings as _settings

        _settings.WRITE_COOLDOWN_SECONDS = 0

        r = user.put(f"/api/records/{record_id}", json={"wake_time": "08:30"})
        check("修改记录", r.status_code == 200 and r.json().get("wake_time") == "08:30", r.text[:300])

        print("\n[4] 作息建议(含自定义目标)")
        r = user.post("/api/advice", json={"mode": "slow", "days": 3, "use_target": True})
        check("建议接口(系统推荐)", r.status_code == 200, r.text[:300])
        body = r.json()
        check("缓慢模式返回3天", len(body.get("plan", [])) == 3, r.text[:300])
        check("建议含三餐", all("meals" in day for day in body.get("plan", [])), r.text[:200])
        check("来源=system", body.get("source") == "system", body.get("source"))

        r = user.put("/api/settings/target", json={"target_sleep_time": "23:00", "target_wake_time": "07:00"})
        check("设置自定义目标", r.status_code == 200, r.text[:300])

        r = user.post("/api/advice", json={"mode": "slow", "days": 5, "use_target": True})
        check("建议接口(自定义目标优先)", r.status_code == 200 and r.json().get("source") == "custom_target", r.text[:300])

        r = user.put("/api/settings", json={"language": "en", "theme": "light"})
        check("偏好保存", r.status_code == 200 and r.json().get("language") == "en", r.text[:300])

        print("\n[5] 越权与权限校验")
        admin_client.post("/api/admin/users", json={"username": "bob", "password": "User@12345"})
        bob = TestClient(app)
        bob.post("/api/auth/login", json={"username": "bob", "password": "User@12345"})
        r = bob.get(f"/api/records/{record_id}")
        check("越权读取他人记录被拒", r.status_code == 404, r.text[:200])
        r = bob.put(f"/api/records/{record_id}", json={"wake_time": "07:00"})
        check("越权修改他人记录被拒", r.status_code == 404, r.text[:200])
        r = bob.get("/api/admin/users")
        check("普通用户访问管理接口被拒", r.status_code == 403, r.text[:200])
        r = user.get("/api/admin/users")
        check("用户端访问管理接口被拒", r.status_code == 403, r.text[:200])

        print("\n[6] 管理员查看/删除用户记录")
        r = admin_client.get(f"/api/admin/users/{alice_id}/records", params={"page": 1, "page_size": 10})
        check("管理员查看用户记录", r.status_code == 200 and r.json().get("total") >= 1, r.text[:300])
        r = admin_client.delete(f"/api/admin/users/{alice_id}/records/{record_id}")
        check("管理员单条删除记录", r.status_code == 200, r.text[:200])

        print("\n[7] 注销申请与审核")
        user2 = TestClient(app)
        user2.post("/api/auth/login", json={"username": "alice", "password": "User@12345"})
        r = user2.post("/api/account/deletion", json={"reason": "不再使用"})
        check("提交注销申请", r.status_code == 201, r.text[:300])
        r = user2.get("/api/account/deletion")
        check("查询注销状态=pending", r.status_code == 200 and r.json().get("status") == "pending", r.text[:300])
        r = admin_client.get("/api/admin/deletion-requests", params={"status": "pending"})
        check("管理员注销列表", r.status_code == 200 and r.json().get("total") >= 1, r.text[:300])
        request_id = r.json()["items"][0]["id"]

        r = admin_client.post(f"/api/admin/deletion-requests/{request_id}", json={"action": "approve", "note": "同意"})
        check("通过注销", r.status_code == 200, r.text[:400])
        check("用户已被删除", admin_client.get(f"/api/admin/users/{alice_id}").status_code == 404)

        dead = TestClient(app)
        r = dead.post("/api/auth/login", json={"username": "alice", "password": "User@12345"})
        check("注销后无法登录", r.status_code == 401, r.text[:200])

        r = admin_client.get("/api/admin/deletion-requests", params={"status": "approved"})
        rows = r.json().get("items", [])
        check(
            "审核记录保留且带用户名快照",
            any(row.get("username") == "alice" and row.get("user_id") is None for row in rows),
            r.text[:400],
        )

        print("\n[8] 日志与统计")
        r = admin_client.get("/api/admin/logs", params={"page": 1, "page_size": 5, "level": "WARNING"})
        check("日志分页+级别筛选", r.status_code == 200 and "level_counts" in r.json(), r.text[:300])
        r = admin_client.get("/api/admin/logs", params={"page_size": 1000})
        check("分页上限被拒绝", r.status_code == 422, r.text[:300])
        r = admin_client.get("/api/admin/stats")
        check("后台统计", r.status_code == 200, r.text[:300])

        print("\n[9] 登录失败锁定")
        victim = TestClient(app)
        codes = []
        remaining = []
        for _ in range(7):
            rr = victim.post("/api/auth/login", json={"username": "bob", "password": "wrong-pass-1"})
            codes.append(rr.status_code)
            if rr.status_code == 401 and "remaining_attempts" in rr.json():
                remaining.append(rr.json()["remaining_attempts"])
        check("连续失败触发锁定(429)", 429 in codes, str(codes))
        check("失败响应带剩余次数预警", remaining == [4, 3, 2, 1, 0], str(remaining))
        rr = victim.post("/api/auth/login", json={"username": "no-such-user", "password": "wrong-pass-1"})
        check(
            "陌生用户名不报剩余次数",
            rr.status_code == 401 and "remaining_attempts" not in rr.json(),
            rr.text[:200],
        )

        print("\n[10] 注销会话")
        r = user2.get("/api/auth/me")
        check("注销后会话失效", r.status_code == 401, r.text[:200])

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for item in FAIL:
            print("  - " + item)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
