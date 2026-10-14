"""登录防护：失败计数、账号锁定、登录审计。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from ..config import settings
from ..db import SessionLocal, run_with_retry, write_session
from ..errors import ApiError
from ..models import LoginAttempt, User, utcnow
from ..core import logkit

try:  # 兼容不同 starlette 版本的常量位置
    from fastapi import status as http_status

    HTTP_429 = http_status.HTTP_429_TOO_MANY_REQUESTS
    HTTP_403 = http_status.HTTP_403_FORBIDDEN
except Exception:  # pragma: no cover
    HTTP_429, HTTP_403 = 429, 403


def lock_remaining_seconds(user: User, now=None) -> int:
    now = now or utcnow()
    if user.locked_until and user.locked_until > now:
        return int((user.locked_until - now).total_seconds())
    return 0


def ensure_not_locked(user: User) -> None:
    remaining = lock_remaining_seconds(user)
    if remaining > 0:
        minutes = max(1, remaining // 60)
        raise ApiError(
            HTTP_429,
            "error.account_locked",
            code="error.account_locked",
            extra={"retry_after_seconds": remaining, "locked_minutes": minutes},
        )


def register_failure(
    user: User | None, *, username: str, ip: str, event: str = "login"
) -> tuple[int, int]:
    """记录一次登录失败，达到阈值则锁定账号。

    返回 ``(failures, locked_seconds)``：窗口内累计失败次数与本次触发的锁定时长
    （没触发锁定为 0），调用方据此给前端"还剩几次机会"的预警。

    即使账号不存在也要统计次数：否则响应体里带不带剩余次数就成了"用户名是否
    存在"的信号，前端提示也会时有时无。
    """
    now = utcnow()
    name = username[:32]

    def _write() -> tuple[int, int]:
        with write_session() as session:
            session.add(LoginAttempt(username=name, ip=ip, success=False, created_at=now))
            # 先落库再统计，让这次失败也算进窗口里
            session.flush()
            window_start = now - timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
            recent = session.execute(
                select(func.count(LoginAttempt.id)).where(
                    LoginAttempt.username == name,
                    LoginAttempt.success.is_(False),
                    LoginAttempt.created_at >= window_start,
                )
            ).scalar_one()
            failures = int(recent)
            locked_seconds = 0
            if user is not None:
                fresh = session.get(User, user.id)
                if fresh is not None:
                    fresh.failed_login_count = failures
                    if failures >= settings.LOGIN_MAX_FAILURES:
                        fresh.locked_until = now + timedelta(seconds=settings.LOGIN_LOCK_SECONDS)
                        fresh.failed_login_count = 0
                        locked_seconds = settings.LOGIN_LOCK_SECONDS
            session.flush()
            return failures, locked_seconds

    failures, locked_seconds = run_with_retry(_write, label="login_failure")
    if locked_seconds:
        logkit.log_event(
            "login_locked",
            f"account {username} locked for {locked_seconds}s after {settings.LOGIN_MAX_FAILURES} failures",
            level="WARNING",
            username=username,
            ip=ip,
        )
    else:
        logkit.log_event(
            "login_failed",
            f"{event} failed for {username} (failures={failures})",
            level="WARNING",
            username=username,
            ip=ip,
        )
    return failures, locked_seconds


def failure_warning(failures: int, locked_seconds: int = 0, *, known: bool = True) -> dict:
    """登录失败响应里的预警字段：还剩几次机会，以及刚触发的锁定信息。

    ``known=False``（用户名不存在 / 不是管理员账号）时不报剩余次数：那里没有
    账号可锁，硬给一个倒计时是误导。
    """
    extra: dict = {}
    if known or locked_seconds:
        extra["remaining_attempts"] = max(0, settings.LOGIN_MAX_FAILURES - failures)
        extra["max_attempts"] = settings.LOGIN_MAX_FAILURES
    if locked_seconds:
        extra["retry_after_seconds"] = locked_seconds
        extra["locked_minutes"] = max(1, locked_seconds // 60)
    return extra


def register_success(user: User, *, ip: str, scope: str = "user") -> None:
    now = utcnow()

    def _write() -> None:
        with write_session() as session:
            session.add(
                LoginAttempt(username=user.username, ip=ip, success=True, created_at=now)
            )
            fresh = session.get(User, user.id)
            if fresh is not None:
                fresh.failed_login_count = 0
                fresh.locked_until = None
                fresh.last_login_at = now
                fresh.last_login_ip = ip
            session.flush()

    run_with_retry(_write, label="login_success")
    logkit.log_event(
        "login_success",
        f"{scope} login success for {user.username}",
        user_id=user.id,
        username=user.username,
        ip=ip,
    )


def recent_failure_count(username: str) -> int:
    window_start = utcnow() - timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
    session = SessionLocal()
    try:
        return int(
            session.execute(
                select(func.count(LoginAttempt.id)).where(
                    LoginAttempt.username == username[:32],
                    LoginAttempt.success.is_(False),
                    LoginAttempt.created_at >= window_start,
                )
            ).scalar_one()
        )
    finally:
        session.close()
