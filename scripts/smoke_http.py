"""端到端 UI/静态资源冒烟：检查前端页面与关键静态文件是否能被后端正确托管。

前置：先启动服务（默认 http://127.0.0.1:8011）
用法：
    backend/.venv/Scripts/python.exe scripts/smoke_http.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011"

CHECKS: list[tuple[str, str, str]] = [
    # (path, 期望 content-type 关键字, 期望响应体里必须出现的内容)
    ("/user/", "text/html", "/user/js/main.js"),
    ("/admin/", "text/html", "/admin/js/app.js"),
    ("/", "text/html", "/user/"),
    ("/user/js/main.js", "javascript", "renderRoute"),
    ("/user/js/pages/records.js", "javascript", "openRecordForm"),
    ("/user/js/pages/advice.js", "javascript", "adviceApi"),
    ("/user/js/pages/settings.js", "javascript", "settingsApi"),
    ("/admin/js/app.js", "javascript", "adminApi"),
    ("/admin/js/state.js", "javascript", "renderTable"),
    ("/admin/js/pages/users.js", "javascript", "renderUsersPage"),
    ("/admin/js/pages/userDetail.js", "javascript", "userRecords"),
    ("/admin/js/pages/requests.js", "javascript", "deletionRequests"),
    ("/admin/js/pages/logs.js", "javascript", "renderLogsPage"),
    ("/shared/i18n.js", "javascript", "DICTS"),
    ("/shared/api.js", "javascript", "settingsApi"),
    ("/shared/ui.js", "javascript", "openModal"),
    ("/shared/timepicker.js", "javascript", "createTimePicker"),
    ("/shared/base.css", "css", "--accent"),
    ("/user/css/app.css", "css", ".app-shell"),
    ("/admin/css/admin.css", "css", ".admin-shell"),
    ("/user/favicon.svg", "svg", "svg"),
    ("/healthz", "json", "ok"),
]


def fetch(path: str) -> tuple[int, str, str]:
    request = urllib.request.Request(BASE + path, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("content-type", ""), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("content-type", ""), error.read().decode("utf-8", "replace")


def main() -> int:
    print(f"base: {BASE}")
    failures = 0
    for path, expect_type, expect_body in CHECKS:
        try:
            status, content_type, body = fetch(path)
        except Exception as error:  # noqa: BLE001
            print(f"FAIL {path}: {type(error).__name__}: {error}")
            failures += 1
            continue
        problems = []
        if status != 200:
            problems.append(f"status={status}")
        if expect_type.lower() not in content_type.lower():
            problems.append(f"content-type={content_type!r}")
        if expect_body not in body:
            problems.append(f"missing {expect_body!r}")
        if problems:
            print(f"FAIL {path}: {'; '.join(problems)}")
            failures += 1
        else:
            print(f"ok   {path} ({len(body)} bytes)")

    # 未登录访问受保护接口必须 401 JSON
    status, content_type, body = fetch("/api/auth/me")
    if status != 401 or "json" not in content_type.lower():
        print(f"FAIL /api/auth/me expected 401 JSON, got {status} {content_type!r}")
        failures += 1
    else:
        print(f"ok   /api/auth/me -> 401 {json.loads(body).get('detail')}")

    # 未知 API 路径必须 404 JSON（不能返回 HTML 兜底页）
    status, content_type, body = fetch("/api/nope")
    if status != 404 or "json" not in content_type.lower():
        print(f"FAIL /api/nope expected 404 JSON, got {status} {content_type!r}")
        failures += 1
    else:
        print("ok   /api/nope -> 404 JSON")

    # 未知路径由兜底路由返回用户端入口（hash 路由只在 # 之后变化）
    status, content_type, body = fetch("/nope")
    if status != 200 or "/user/js/main.js" not in body:
        print(f"FAIL /nope fallback -> {status} {content_type!r}")
        failures += 1
    else:
        print("ok   /nope -> user index fallback")

    # /user 与 /admin 挂载点内的未知文件必须 404，不能返回 HTML
    for path in ("/user/nothing.css", "/admin/nothing.css"):
        status, content_type, body = fetch(path)
        if status != 404:
            print(f"FAIL {path} expected 404, got {status}")
            failures += 1
        else:
            print(f"ok   {path} -> 404")

    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
