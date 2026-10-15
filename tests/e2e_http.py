"""联网端到端测试：对真实启动的服务（默认 http://127.0.0.1:8011）跑完整业务流程。

覆盖：管理员多段登录 -> 建档 -> 用户登录 -> 睡眠记录增删改查 / 30 秒防重
      -> 作息建议（系统推荐 / 自定义目标优先）-> 偏好保存 -> 注销申请 -> 审核通过并清空数据
      -> 越权与权限校验 -> 日志与统计 -> 登录失败锁定 -> 注销会话

用法：
    backend/.venv/Scripts/python.exe tests/e2e_http.py [base_url]

管理员凭据：默认是内置 bootstrap 账号（本地与 CI 直接可用）。线上或任何加固过的环境
用环境变量覆盖，真实口令不进仓库：
    SSP_E2E_ADMIN_USERNAME=tyq-admin
    SSP_E2E_ADMIN_PARTS=口令1,口令2,口令3   （第 1 段是账号密码，其后按 part_index 顺序）
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")
ADMIN_USERNAME = os.environ.get("SSP_E2E_ADMIN_USERNAME", "admin")
ADMIN_PARTS = [
    part
    for part in os.environ.get("SSP_E2E_ADMIN_PARTS", "Admin@12345,Admin#67890,Admin$24680").split(",")
    if part
]

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")
    return bool(condition)


class Client:
    """带 cookie 会话的极简 HTTP 客户端。"""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict, str]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            BASE + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, _json(body), body
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            return error.code, _json(body), body

    def get(self, path: str) -> tuple[int, dict, str]:
        return self.call("GET", path)

    def post(self, path: str, payload: dict | None = None) -> tuple[int, dict, str]:
        return self.call("POST", path, payload or {})

    def put(self, path: str, payload: dict | None = None) -> tuple[int, dict, str]:
        return self.call("PUT", path, payload or {})


def _json(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def main() -> int:
    admin = Client()
    stamp = "e2e"

    print("\n[1] 健康检查 / 静态资源 / 管理员多段登录")
    status, body, _ = admin.get("/healthz")
    check("健康检查", status == 200 and body.get("status") == "ok", str(body))

    status, body, _ = admin.get("/api/auth/admin/parts")
    parts = int(body.get("parts") or 0)
    check("管理员密码段数接口", status == 200 and parts >= 2, str(body))

    status, body, _ = admin.post("/api/auth/admin/login", {"username": ADMIN_USERNAME, "passwords": ADMIN_PARTS[:1]})
    check("密码段不足被拒(401)", status == 401, f"{status} {body}")

    status, body, _ = admin.post(
        "/api/auth/admin/login", {"username": ADMIN_USERNAME, "passwords": ADMIN_PARTS, "remember": True}
    )
    check("管理员多段登录成功", status == 200 and body.get("is_admin") is True, f"{status} {body}")

    status, body, _ = admin.post("/api/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PARTS[0]})
    check("管理员不能用普通入口登录(403)", status == 403, f"{status} {body}")

    print("\n[2] 管理员建档")
    user_a = f"{stamp}_alice"
    user_b = f"{stamp}_bob"
    status, body, _ = admin.post("/api/admin/users", {"username": user_a, "password": "User@12345"})
    check("创建用户 alice", status == 201, f"{status} {body}")
    alice_id = body.get("id")

    status, body, _ = admin.post("/api/admin/users", {"username": user_a, "password": "User@12345"})
    check("同名用户被拒(409)", status == 409, f"{status} {body}")

    status, body, _ = admin.post("/api/admin/users", {"username": user_b, "password": "User@12345", "language": "en", "theme": "light"})
    check("创建用户 bob", status == 201, f"{status} {body}")
    bob_id = body.get("id")

    status, body, _ = admin.post(
        "/api/admin/users",
        {"username": f"{stamp}_carol", "password": "User@12345", "is_admin": True, "admin_passwords": ["Part2@12345"]},
    )
    check("新建管理员密码段不足被拒(400)", status == 400 and body.get("required_parts") == parts - 1, f"{status} {body}")

    status, body, _ = admin.post(
        "/api/admin/users",
        {
            "username": f"{stamp}_carol",
            "password": "User@12345",
            "is_admin": True,
            "admin_passwords": ["Part2@12345", "Part3@12345"],
        },
    )
    check("创建管理员账号(含多段密码)", status == 201, f"{status} {body}")
    carol_id = body.get("id")

    status, body, _ = admin.get(f"/api/admin/users?q={stamp}&page=1&page_size=10")
    names = [item.get("username") for item in body.get("items", [])]
    check("用户名搜索 + 分页", status == 200 and user_a in names and body.get("total", 0) >= 2, f"{status} {names}")

    print("\n[3] 普通用户登录与睡眠记录")
    alice = Client()
    status, body, _ = alice.post("/api/auth/login", {"username": user_a, "password": "User@12345", "remember": True})
    check("用户登录", status == 200 and body.get("username") == user_a, f"{status} {body}")

    status, body, _ = alice.post("/api/auth/login", {"username": user_a, "password": "wrong-pass"})
    check("错误密码被拒(401)", status == 401, f"{status} {body}")

    status, body, _ = alice.get("/api/records")
    check("初始记录为空", status == 200 and body.get("total") == 0, f"{status} {body}")

    # 格式校验先于 10 秒防重（429 优先于 422），因此以下两条要在首次写入之前验证
    status, body, _ = alice.post("/api/records", {"sleep_time": "25:00", "wake_time": "09:00", "record_date": "2025-10-02"})
    check("非法时间被拒(422)", status == 422, f"{status} {body}")

    status, body, _ = alice.post("/api/records", {"sleep_time": "23:00", "wake_time": "07:00", "record_date": "2099-01-01"})
    check("未来日期被拒(400)", status == 400 and body.get("code") == "error.date_in_future", f"{status} {body}")

    status, body, _ = alice.post(
        "/api/records",
        {
            "sleep_time": "01:30",
            "wake_time": "09:00",
            "meal_breakfast": True,
            "meal_lunch": True,
            "meal_dinner": False,
            "record_date": "2025-10-01",
            "note": "e2e",
        },
    )
    check("新增睡眠记录(201)", status == 201, f"{status} {body}")
    record_id = body.get("id")
    check("时长计算 450 分钟", body.get("duration_minutes") == 450, str(body))

    status, body, _ = alice.post("/api/records", {"sleep_time": "02:00", "wake_time": "09:00", "record_date": "2025-10-02"})
    check("30 秒内重复写入被拒(429)", status == 429 and "retry_after_seconds" in json.dumps(body), f"{status} {body}")

    status, body, _ = alice.get(f"/api/records/{record_id}")
    check("读取单条记录", status == 200 and body.get("id") == record_id, f"{status} {body}")

    status, body, _ = alice.get("/api/records/meta")
    check("记录元信息含保留策略", status == 200 and body.get("retention_months") == 6, f"{status} {body}")

    status, body, _ = alice.put(f"/api/records/{record_id}", {"wake_time": "08:30"})
    check(
        "修改记录(受 30 秒防重限制时可 429)",
        status in (200, 429) and (status == 429 or body.get("wake_time") == "08:30"),
        f"{status} {body}",
    )

    print("\n[4] 作息建议（adaptive-v2 引擎：步长曲线 / 时间图 / 快照 / 一键确认）")
    status, body, _ = alice.post("/api/advice", {"mode": "slow", "days": 3})
    check("缓慢模式返回 3 天", status == 200 and len(body.get("plan", [])) == 3, f"{status} {body}")
    check("方案来源=system（未设目标）", body.get("source") == "system", str(body.get("source")))
    check("每天含三餐时间", all("meals" in day for day in body.get("plan", [])), str(body)[:200])
    check("引擎为 adaptive-v2", body.get("engine") == "adaptive-v2", str(body.get("engine")))
    check(
        "返回步长策略（前移上限 45 / 后移上限 90）",
        body.get("steps", {}).get("cap_advance_minutes") == 45
        and body.get("steps", {}).get("cap_delay_minutes") == 90
        and body["steps"]["direction"] in ("advance", "delay"),
        str(body.get("steps"))[:200],
    )
    check(
        "每天带时间图（睡眠条 + 三餐条）",
        all(
            day.get("timeline", {}).get("items")
            and {item["kind"] for item in day["timeline"]["items"]} == {"sleep", "meal"}
            for day in body.get("plan", [])
        ),
        str(body.get("plan", [{}])[0].get("timeline"))[:200],
    )
    check(
        "三餐给出最佳时间与建议区间",
        all(
            day["meals"]["breakfast"]["best_time"]
            and day["meals"]["dinner"]["window_start"]
            and day["meals"]["breakfast"]["bar_minutes"] < day["meals"]["lunch"]["bar_minutes"]
            for day in body.get("plan", [])
        ),
        str(body.get("plan", [{}])[0].get("meals"))[:200],
    )
    check("今天那一天单独返回", bool(body.get("today", {}).get("date")), str(body.get("today", {}).get("date")))
    check(
        "说明与结构化提示都是 i18n 代码",
        all(str(code).startswith("advice.") for code in body.get("notes", []))
        and all(str(item["code"]).startswith("advice.") for item in body.get("notices", [])),
        str(body.get("notes"))[:120],
    )
    check(
        "计划快照已保存（含目标日期）",
        body.get("plan_meta", {}).get("saved") is True
        and bool(body["plan_meta"].get("target_date"))
        and body["plan_meta"].get("plan_id"),
        str(body.get("plan_meta")),
    )

    status, body, _ = alice.get("/api/advice/plan")
    check(
        "刷新后仍能取回同一份计划（跨设备一致）",
        status == 200 and body.get("has_plan") is True and body.get("today", {}).get("date"),
        f"{status} {str(body)[:160]}",
    )
    saved_target = body.get("plan_meta", {}).get("target_date")

    status, body, _ = alice.post("/api/advice/checkin", {"meal": "breakfast", "eaten": False})
    check(
        "一键确认“没吃早餐”写回当天记录",
        status == 200
        and body.get("ok") is True
        and body["meal_plan"]["recent"][-1]["meals"]["breakfast"]["state"] == "skipped",
        f"{status} {str(body.get('meal_plan'))[:160]}",
    )

    status, body, _ = alice.post("/api/advice/checkin", {"meal": "breakfast", "eaten": True})
    check(
        "再确认“已吃”可以改回来",
        status == 200 and body["meal_plan"]["recent"][-1]["meals"]["breakfast"]["state"] == "eaten",
        f"{status} {str(body.get('meal_plan'))[:160]}",
    )

    status, body, _ = alice.post("/api/advice/notice-ack", {"notice": "advice.notice.short_plan"})
    check(
        "“不再提示”记到账号上",
        status == 200 and "advice.notice.short_plan" in body.get("acked_notices", []),
        f"{status} {str(body)[:160]}",
    )

    status, body, _ = alice.post("/api/advice/replan", {"strategy": "keep"})
    check(
        "选择“先不改”不移动目标日期",
        status == 200 and body.get("plan_meta", {}).get("target_date") == saved_target,
        f"{status} {str(body.get('plan_meta'))}",
    )

    status, body, _ = alice.get("/api/advice/options")
    check(
        "选项接口暴露步长与偏离阈值",
        status == 200
        and body.get("engine") == "adaptive-v2"
        and body["steps"]["curve_minutes"] == [60, 45, 30, 15]
        and body["deviation"]["ask_minutes"] == 90,
        f"{status} {str(body.get('steps'))[:160]}",
    )

    status, body, _ = alice.post("/api/advice", {"mode": "fast", "days": 7})
    check("快速模式固定 1 天", status == 200 and len(body.get("plan", [])) == 1, f"{status} {body}")

    status, body, _ = alice.put("/api/settings/target", {"target_sleep_time": "23:00", "target_wake_time": "07:00"})
    check("设置自定义目标", status == 200 and body.get("target_sleep_time") == "23:00", f"{status} {body}")

    status, body, _ = alice.post("/api/advice", {"mode": "slow", "days": 5, "use_target": True})
    check("自定义目标优先(source=custom_target)", status == 200 and body.get("source") == "custom_target", f"{status} {body}")

    status, body, _ = alice.put("/api/settings/target", {"target_sleep_time": None, "target_wake_time": None})
    check("清空自定义目标", status == 200 and body.get("target_sleep_time") is None, f"{status} {body}")

    status, body, _ = alice.put("/api/settings", {"language": "en", "theme": "light"})
    check("偏好（语言/主题）保存", status == 200 and body.get("language") == "en" and body.get("theme") == "light", f"{status} {body}")

    print("\n[5] 越权与权限校验")
    bob = Client()
    bob.post("/api/auth/login", {"username": user_b, "password": "User@12345"})
    status, body, _ = bob.get(f"/api/records/{record_id}")
    check("越权读取他人记录 -> 404", status == 404, f"{status} {body}")
    status, body, _ = bob.put(f"/api/records/{record_id}", {"wake_time": "07:00"})
    check("越权修改他人记录 -> 404", status == 404, f"{status} {body}")
    status, body, _ = bob.get("/api/admin/users")
    check("普通用户访问管理接口 -> 403", status == 403, f"{status} {body}")
    anonymous = Client()
    status, body, _ = anonymous.get("/api/records")
    check("未登录访问记录 -> 401", status == 401, f"{status} {body}")

    print("\n[6] 管理员查看与删除用户记录")
    status, body, _ = admin.get(f"/api/admin/users/{alice_id}/records?page=1&page_size=10")
    check("管理员查看用户记录", status == 200 and body.get("total", 0) >= 1, f"{status} {body}")
    status, body, _ = admin.get(f"/api/admin/users/{alice_id}/records?page_size=1000")
    check("分页上限被拒(422)", status == 422, f"{status} {body}")
    status, body, _ = admin.get(f"/api/admin/users/{alice_id}")
    check("用户详情含记录数", status == 200 and body.get("record_count", 0) >= 1, f"{status} {body}")

    print("\n[7] 注销申请与审核（数据级联清除）")
    status, body, _ = alice.post("/api/account/deletion", {"reason": "e2e 测试注销"})
    check("提交注销申请(201)", status == 201, f"{status} {body}")
    status, body, _ = alice.post("/api/account/deletion", {"reason": "重复提交"})
    check("重复申请被拒(409)", status == 409, f"{status} {body}")
    status, body, _ = alice.get("/api/account/deletion")
    check("注销状态=pending", status == 200 and body.get("status") == "pending", f"{status} {body}")

    status, body, _ = admin.get("/api/admin/deletion-requests?status=pending&page=1&page_size=10")
    items = [row for row in body.get("items", []) if row.get("username") == user_a]
    check("管理员注销列表", status == 200 and bool(items), f"{status} {body}")
    request_id = items[0]["id"] if items else None

    status, body, _ = admin.post(f"/api/admin/deletion-requests/{request_id}", {"action": "approve", "note": "同意注销"})
    check("通过注销申请", status == 200, f"{status} {body}")
    status, body, _ = admin.get(f"/api/admin/users/{alice_id}")
    check("用户账号已删除", status == 404, f"{status} {body}")

    dead = Client()
    status, body, _ = dead.post("/api/auth/login", {"username": user_a, "password": "User@12345"})
    check("注销后无法登录", status == 401, f"{status} {body}")

    status, body, _ = admin.get("/api/admin/deletion-requests?status=approved&page=1&page_size=10")
    rows = [row for row in body.get("items", []) if row.get("username") == user_a]
    check(
        "审核记录保留且用户名快照可用",
        status == 200 and bool(rows) and rows[0].get("user_id") is None and rows[0].get("username") == user_a,
        f"{status} {rows}",
    )

    print("\n[8] 批量删除与日志统计")
    admin.post("/api/admin/users", {"username": f"{stamp}_carol2", "password": "User@12345"})
    status, body, _ = admin.get(f"/api/admin/users?q={stamp}_carol2")
    carol2 = (body.get("items") or [{}])[0].get("id")
    status, body, _ = admin.post(f"/api/admin/users/{carol2}/records/batch-delete", {"ids": []})
    check("空批量删除被拒(422/400)", status in (400, 422), f"{status} {body}")
    status, body, _ = admin.post(f"/api/admin/users/{carol2}/records/batch-delete", {"ids": [999999]})
    check("批量删除不存在的记录不报错", status in (200, 404), f"{status} {body}")

    status, body, _ = admin.get("/api/admin/logs?page=1&page_size=5&level=WARNING")
    check("日志分页 + 级别筛选 + 计数", status == 200 and "level_counts" in body and "items" in body, f"{status} {body}")
    status, body, _ = admin.get("/api/admin/logs?q=login&page=1&page_size=5")
    check("日志关键字搜索", status == 200, f"{status} {body}")

    status, body, _ = admin.get("/api/admin/stats")
    check("后台统计", status == 200 and body.get("users", 0) >= 1 and "retention_months" in body, f"{status} {body}")

    print("\n[9] 登录失败锁定")
    victim = Client()
    codes = []
    remaining = []
    locked_body = {}
    for _ in range(7):
        status, body, _ = victim.post("/api/auth/login", {"username": user_b, "password": "wrong-pass"})
        codes.append(status)
        if status == 401 and isinstance(body, dict) and "remaining_attempts" in body:
            remaining.append(body["remaining_attempts"])
            if body["remaining_attempts"] == 0:
                locked_body = body
    check("连续失败触发锁定(429)", 429 in codes, str(codes))
    # 5 次上限：4/3/2/1 次预警，第 5 次直接锁定（remaining=0 + 锁定剩余时间）
    check("失败响应带剩余次数预警", remaining == [4, 3, 2, 1, 0], str(remaining))
    check(
        "触发锁定那次带锁定剩余时间",
        locked_body.get("locked_minutes", 0) >= 1 and locked_body.get("retry_after_seconds", 0) > 0,
        str(locked_body),
    )
    status, body, _ = admin.post(f"/api/admin/users/{bob_id}/unlock")
    check("管理员解锁账号", status == 200, f"{status} {body}")
    status, body, _ = bob.post("/api/auth/login", {"username": user_b, "password": "User@12345"})
    check("解锁后可正常登录", status == 200, f"{status} {body}")

    # 成功登录把失败计数清零：连错两次（4/3）→ 成功登录 → 再错一次应又回到 4
    reset_victim = Client()
    reset_seen = []
    for _ in range(2):
        status, body, _ = reset_victim.post("/api/auth/login", {"username": user_b, "password": "wrong-pass"})
        reset_seen.append(body.get("remaining_attempts") if isinstance(body, dict) else None)
    status, body, _ = bob.post("/api/auth/login", {"username": user_b, "password": "User@12345"})
    relogin_status = status
    status, body, _ = reset_victim.post("/api/auth/login", {"username": user_b, "password": "wrong-pass"})
    check(
        "成功登录后失败计数清零",
        reset_seen == [4, 3] and relogin_status == 200 and isinstance(body, dict) and body.get("remaining_attempts") == 4,
        f"before={reset_seen} relogin={relogin_status} after={body}",
    )

    print("\n[10] 会话注销与清理")
    status, body, _ = bob.post("/api/auth/logout")
    check("用户注销会话", status == 200, f"{status} {body}")
    status, body, _ = bob.get("/api/auth/me")
    check("注销后会话失效(401)", status == 401, f"{status} {body}")
    status, body, _ = admin.post("/api/admin/cleanup")
    check("手动触发数据清理", status == 200 and isinstance(body.get("extra", {}).get("result"), dict), f"{status} {body}")
    status, body, _ = admin.post("/api/auth/admin/logout")
    check("管理员注销会话", status == 200, f"{status} {body}")
    status, body, _ = admin.get("/api/admin/stats")
    check("管理员注销后无法访问后台(401/403)", status in (401, 403), f"{status} {body}")

    # 清理测试账号，保持数据库干净
    cleanup = Client()
    cleanup.post("/api/auth/admin/login", {"username": ADMIN_USERNAME, "passwords": ADMIN_PARTS, "remember": True})
    for account_id in (bob_id, carol_id, carol2):
        if account_id:
            cleanup.call("DELETE", f"/api/admin/users/{account_id}")
    # 删除测试产生的注销审核记录与登录失败计数，避免在交付库中留下测试数据
    cleanup.post("/api/admin/cleanup")

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  - " + item)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
