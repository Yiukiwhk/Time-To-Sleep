"""Cookie 会话服务：创建、校验、销毁（有效期 30 天，支持记住登录）。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request, Response
from sqlalchemy import delete, select

from ..config import settings
from ..db import SessionLocal, run_with_retry, write_session
from ..models import SessionToken, User, utcnow
from ..core.security import (
    client_ip,
    client_ua,
    new_session_token,
    session_expiry,
    token_hash,
)

SCOPE_USER = "user"
SCOPE_ADMIN = "admin"


def _cookie_name(scope: str) -> str:
    return settings.ADMIN_COOKIE_NAME if scope == SCOPE_ADMIN else settings.SESSION_COOKIE_NAME


def create_session(
    user: User,
    *,
    remember: bool,
    scope: str,
    request: Request,
    response: Response,
) -> str:
    """创建会话并写入 Cookie。"""
    token = new_session_token()
    now = utcnow()
    expires_at = session_expiry(remember, now)

    def _write() -> None:
        with write_session() as session:
            session.add(
                SessionToken(
                    user_id=user.id,
                    token_hash=token_hash(token),
                    scope=scope,
                    remember=bool(remember),
                    ip=client_ip(request),
                    user_agent=client_ua(request),
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            session.flush()

    run_with_retry(_write, label="create_session")
    set_session_cookie(response, token, scope=scope, remember=remember, expires_at=expires_at)
    return token


def set_session_cookie(
    response: Response,
    token: str,
    *,
    scope: str,
    remember: bool,
    expires_at: datetime,
) -> None:
    max_age = max(0, int((expires_at - utcnow()).total_seconds()))
    # 库里统一存 naive UTC；Starlette 的 usegmt 要求带时区，这里显式补上 UTC
    if expires_at.tzinfo is None:
        cookie_expires: datetime = expires_at.replace(tzinfo=timezone.utc)
    else:
        cookie_expires = expires_at.astimezone(timezone.utc)
    response.set_cookie(
        key=_cookie_name(scope),
        value=token,
        max_age=max_age,
        expires=cookie_expires,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,   # 需求：HTTP + IP 直连部署，故不启用 Secure
    )


def clear_session_cookie(response: Response, scope: str) -> None:
    response.delete_cookie(key=_cookie_name(scope), path="/", httponly=True, samesite="lax")


def resolve_session(request: Request, scope: str) -> tuple[User | None, SessionToken | None]:
    """校验 Cookie 会话，返回 (用户, 会话记录)。过期会话顺手清理。"""
    token = request.cookies.get(_cookie_name(scope))
    if not token or len(token) > 256:
        return None, None
    digest = token_hash(token)
    session = SessionLocal()
    try:
        row = session.execute(
            select(SessionToken).where(
                SessionToken.token_hash == digest, SessionToken.scope == scope
            )
        ).scalar_one_or_none()
        if row is None:
            return None, None
        if row.expires_at <= utcnow():
            _purge_token(row.id)
            return None, None
        user = session.get(User, row.user_id)
        if user is None or not user.is_active:
            _purge_token(row.id)
            return None, None
        return user, row
    finally:
        session.close()


def revoke_session(request: Request, scope: str) -> None:
    token = request.cookies.get(_cookie_name(scope))
    if not token:
        return
    digest = token_hash(token)

    def _delete() -> None:
        with write_session() as session:
            session.execute(delete(SessionToken).where(SessionToken.token_hash == digest))
            session.flush()

    run_with_retry(_delete, label="revoke_session")


def revoke_all_for_user(user_id: int, *, scope: str | None = None) -> int:
    """清理某用户的全部会话（改密码、注销账号时调用）。"""

    def _delete() -> int:
        with write_session() as session:
            statement = delete(SessionToken).where(SessionToken.user_id == user_id)
            if scope:
                statement = statement.where(SessionToken.scope == scope)
            result = session.execute(statement)
            session.flush()
            return result.rowcount or 0

    return run_with_retry(_delete, label="revoke_all")


def purge_expired_sessions() -> int:
    """清理过期会话记录。"""

    def _delete() -> int:
        with write_session() as session:
            result = session.execute(delete(SessionToken).where(SessionToken.expires_at <= utcnow()))
            session.flush()
            return result.rowcount or 0

    return run_with_retry(_delete, label="purge_sessions")


def _purge_token(session_id: int) -> None:
    def _delete() -> None:
        with write_session() as session:
            session.execute(delete(SessionToken).where(SessionToken.id == session_id))
            session.flush()

    run_with_retry(_delete, label="purge_token")
