"""FastAPI 依赖：会话鉴权、管理员鉴权、请求上下文。

权限说明：
  * 普通用户会话与管理后台会话使用不同的 Cookie（scope 隔离），
    普通用户 Cookie 无法访问 /api/admin/*；
  * 管理员接口还会再次校验 user.is_admin，双重保险。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .config import settings
from .core.security import client_ip, client_ua
from .db import get_db
from .errors import ApiError
from .models import User
from .services import sessions


@dataclass
class RequestContext:
    ip: str
    ua: str
    path: str
    method: str


def request_context(request: Request) -> RequestContext:
    return RequestContext(
        ip=client_ip(request),
        ua=client_ua(request),
        path=request.url.path,
        method=request.method,
    )


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user, _ = sessions.resolve_session(request, sessions.SCOPE_USER)
    if user is None:
        return None
    return db.get(User, user.id)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user, _ = sessions.resolve_session(request, sessions.SCOPE_USER)
    if user is None:
        raise ApiError(401, "error.not_authenticated", code="error.not_authenticated")
    fresh = db.get(User, user.id)
    if fresh is None or not fresh.is_active:
        raise ApiError(401, "error.not_authenticated", code="error.not_authenticated")
    return fresh


def current_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user, _ = sessions.resolve_session(request, sessions.SCOPE_ADMIN)
    if user is None or not user.is_admin:
        raise ApiError(403, "error.admin_only", code="error.admin_only")
    fresh = db.get(User, user.id)
    if fresh is None or not fresh.is_active or not fresh.is_admin:
        raise ApiError(403, "error.admin_only", code="error.admin_only")
    return fresh


def client_meta(ctx: RequestContext = Depends(request_context)) -> RequestContext:
    return ctx


__all__ = [
    "RequestContext",
    "client_meta",
    "current_admin",
    "current_user",
    "optional_user",
    "request_context",
    "settings",
]
