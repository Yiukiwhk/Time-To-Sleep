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


def register_failure(user: User | None, *, username: str, ip: str, event: str = "login") -> None:
    """记录一次登录失败，达到阈值则锁定账号。"""
    now = utcnow()

    def _write() -> tuple[int, int]:
        with write_session() as session:
            session.add(LoginAttempt(username=username[:32], ip=ip, success=False, created_at=now))
            locked_seconds = 0
            failures = 0
            if user is not None:
                fresh = session.get(User, user.id)
                if fresh is not None:
                    window_start = now - timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
                    recent = session.execute(
                        select(func.count(LoginAttempt.id)).where(
                            LoginAttempt.username == username[:32],
                            LoginAttempt.success.is_(False),
                            LoginAttempt.created_at >= window_start,
                        )
                    ).scalar_one()
                    fresh.failed_login_count = int(recent)
                    failures = int(recent)
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
