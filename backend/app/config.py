"""应用配置。

所有可调参数集中在这里，可通过环境变量覆盖（前缀 SSP_）。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_ROOT = BASE_DIR.parent                              # sleep-schedule/


def _env(name: str, default: str) -> str:
    value = os.environ.get(f"SSP_{name}")
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """运行期配置对象（单例 settings）。"""

    APP_NAME: str = "Sleep Schedule Planner"

    # ---------- 服务 ----------
    HOST: str = _env("HOST", "0.0.0.0")
    PORT: int = _env_int("PORT", 8000)

    # ---------- 路径 ----------
    DATA_DIR: Path = Path(_env("DATA_DIR", str(PROJECT_ROOT / "data")))
    LOG_DIR: Path = Path(_env("LOG_DIR", str(PROJECT_ROOT / "logs")))
    FRONTEND_DIR: Path = Path(_env("FRONTEND_DIR", str(PROJECT_ROOT / "frontend")))
    DB_PATH: Path = Path(_env("DB_PATH", str(PROJECT_ROOT / "data" / "app.db")))
    LOG_FILE: Path = Path(_env("LOG_FILE", str(PROJECT_ROOT / "logs" / "app.log")))

    # ---------- 数据库并发 ----------
    # SQLite 多人并发提交：等待重试策略
    DB_BUSY_TIMEOUT_MS: int = _env_int("DB_BUSY_TIMEOUT_MS", 8000)
    DB_WRITE_RETRIES: int = _env_int("DB_WRITE_RETRIES", 6)
    DB_RETRY_BASE_DELAY: float = float(_env("DB_RETRY_BASE_DELAY", "0.08"))
    DB_RETRY_MAX_DELAY: float = float(_env("DB_RETRY_MAX_DELAY", "1.0"))

    # ---------- 会话 ----------
    SESSION_COOKIE_NAME: str = _env("SESSION_COOKIE_NAME", "ssp_session")
    ADMIN_COOKIE_NAME: str = _env("ADMIN_COOKIE_NAME", "ssp_admin_session")
    SESSION_TTL_SECONDS: int = _env_int("SESSION_TTL_SECONDS", 30 * 24 * 3600)       # 30 天
    SESSION_TTL_SHORT_SECONDS: int = _env_int("SESSION_TTL_SHORT_SECONDS", 12 * 3600)  # 未勾选“记住登录”

    # ---------- 登录防爆破 ----------
    LOGIN_MAX_FAILURES: int = _env_int("LOGIN_MAX_FAILURES", 5)
    LOGIN_LOCK_SECONDS: int = _env_int("LOGIN_LOCK_SECONDS", 15 * 60)
    LOGIN_FAILURE_WINDOW_SECONDS: int = _env_int("LOGIN_FAILURE_WINDOW_SECONDS", 15 * 60)

    # ---------- 写入节流 ----------
    WRITE_COOLDOWN_SECONDS: int = _env_int("WRITE_COOLDOWN_SECONDS", 30)

    # ---------- 数据保留 ----------
    RETENTION_MONTHS: int = _env_int("RETENTION_MONTHS", 6)
    CLEANUP_INTERVAL_SECONDS: int = _env_int("CLEANUP_INTERVAL_SECONDS", 6 * 3600)

    # ---------- 分页 ----------
    DEFAULT_PAGE_SIZE: int = _env_int("DEFAULT_PAGE_SIZE", 20)
    MAX_PAGE_SIZE: int = _env_int("MAX_PAGE_SIZE", 100)

    # ---------- 输入长度上限（前后端双重校验，禁止超长文本）----------
    MAX_USERNAME_LEN: int = _env_int("MAX_USERNAME_LEN", 32)
    MIN_USERNAME_LEN: int = _env_int("MIN_USERNAME_LEN", 3)
    MIN_PASSWORD_LEN: int = _env_int("MIN_PASSWORD_LEN", 8)
    MAX_PASSWORD_LEN: int = _env_int("MAX_PASSWORD_LEN", 72)       # bcrypt 上限
    MAX_NOTE_LEN: int = _env_int("MAX_NOTE_LEN", 200)

    # ---------- 管理员多重密码 ----------
    # 多段密码登录：默认 3 段（可用 SSP_ADMIN_PASSWORD_PARTS 调整）
    ADMIN_PASSWORD_PARTS: int = _env_int("ADMIN_PASSWORD_PARTS", 3)

    # ---------- CORS ----------
    # 仅允许指定前端来源访问 API（同源托管时前端就来自这些地址）
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in _env(
            "CORS_ORIGINS",
            "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if o.strip()
    ]
    CORS_ORIGIN_REGEX: str = _env(
        "CORS_ORIGIN_REGEX",
        r"^http://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$",
    )

    # ---------- 初始管理员（首次启动引导）----------
    BOOTSTRAP_ADMIN_USERNAME: str = _env("BOOTSTRAP_ADMIN_USERNAME", "admin")
    BOOTSTRAP_ADMIN_PASSWORDS: list[str] = [
        p for p in _env("BOOTSTRAP_ADMIN_PASSWORDS", "Admin@12345,Admin#67890,Admin$24680").split(",")
    ]

    # ---------- 日志 ----------
    LOG_LEVEL: str = _env("LOG_LEVEL", "INFO").upper()
    LOG_RETENTION_ROWS: int = _env_int("LOG_RETENTION_ROWS", 20000)

    DEBUG: bool = _env_bool("DEBUG", False)


settings = Settings()
