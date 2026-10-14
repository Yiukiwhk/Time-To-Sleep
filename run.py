"""作息安排网站 —— 一键启动脚本（Windows / Linux 通用）。

用法：
    python run.py                 # 默认 0.0.0.0:8000（服务器上用 IP 直接访问）
    python run.py --port 8011
    python run.py --reload        # 开发模式，代码热重载
    python run.py --no-browser    # 不自动打开浏览器

依赖：requirements.txt（FastAPI + SQLAlchemy + bcrypt + uvicorn）。
首次启动会自动建库、写入内置管理员账号、启动 6 个月数据清理定时任务。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

REQUIRED = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "bcrypt")


def check_dependencies() -> list[str]:
    """返回缺失的依赖名列表。"""
    import importlib.util

    missing = []
    for name in REQUIRED:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="作息安排网站启动器")
    parser.add_argument("--host", default=None, help="监听地址，默认取 SSP_HOST 或 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认取 SSP_PORT 或 8000")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码改动自动重载")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    missing = check_dependencies()
    if missing:
        print("缺少依赖：" + ", ".join(missing))
        print("请先安装依赖后重试：")
        print(f'  "{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"')
        print("没有 pip 环境时可用离线安装器：")
        print(f'  "{sys.executable}" "{ROOT / "scripts" / "bootstrap_deps.py"}"')
        return 2

    host = args.host or os.environ.get("SSP_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("SSP_PORT", "8000"))
    os.environ.setdefault("SSP_HOST", host)
    os.environ["SSP_PORT"] = str(port)

    import uvicorn

    from app.config import settings  # noqa: E402  确保目录/配置在启动前就绪

    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    user_url = f"http://{shown}:{port}/user/"
    admin_url = f"http://{shown}:{port}/admin/"

    print("=" * 64)
    print("作息安排网站  Sleep Schedule Planner")
    print(f"  用户端：{user_url}")
    print(f"  后台端：{admin_url}")
    print(f"  数据库：{settings.DB_PATH}")
    print(f"  日志  ：{settings.LOG_FILE}")
    print("=" * 64)

    if not args.no_browser and not args.reload:
        def _open() -> None:
            time.sleep(1.2)
            try:
                webbrowser.open(user_url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=args.reload,
        reload_dirs=[str(BACKEND)] if args.reload else None,
        log_level=os.environ.get("SSP_LOG_LEVEL", "info").lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
