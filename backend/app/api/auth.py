"""认证接口：登录、登出、当前用户、修改密码；管理员多重密码登录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, run_with_retry, write_session
from ..deps import RequestContext, client_meta, current_admin, current_user
from ..errors import ApiError
from ..models import User
from ..schemas import (
    AdminLoginRequest,
    ChangePasswordRequest,
    LoginRequest,
    MessageOut,
    UserPublic,
)
from ..core import logkit
from ..core.security import hash_password, verify_password
from ..services import auth_service, sessions

router = APIRouter(tags=["auth"])


def _find_user(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


@router.post("/auth/login", response_model=UserPublic)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(client_meta),
) -> User:
    """普通用户登录（账号密码 + 失败次数限制 + 30 天 Cookie）。"""
    user = _find_user(db, payload.username)
    if user is None:
        auth_service.register_failure(None, username=payload.username, ip=ctx.ip)
        raise ApiError(401, "error.invalid_credentials", code="error.invalid_credentials")

    auth_service.ensure_not_locked(user)
    if not user.is_active:
        raise ApiError(403, "error.account_disabled", code="error.account_disabled")

    if user.is_admin:
        # 管理员必须走多重密码入口（/api/auth/admin/login），不能只凭单段密码登录
        raise ApiError(403, "error.use_admin_login", code="error.use_admin_login")

    if not verify_password(payload.password, user.password_hash):
        auth_service.register_failure(user, username=user.username, ip=ctx.ip)
        raise ApiError(401, "error.invalid_credentials", code="error.invalid_credentials")

    auth_service.register_success(user, ip=ctx.ip, scope="user")
    # 普通用户会话与管理后台会话隔离
    sessions.revoke_session(request, sessions.SCOPE_USER)
    sessions.create_session(
        user, remember=payload.remember, scope=sessions.SCOPE_USER, request=request, response=response
    )
    db.refresh(user)
    return user


@router.post("/auth/admin/login", response_model=UserPublic)
def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(client_meta),
) -> User:
    """管理员多重密码登录：所有密码段必须全部校验通过。"""
    user = _find_user(db, payload.username)
    if user is None or not user.is_admin:
        auth_service.register_failure(None, username=payload.username, ip=ctx.ip, event="admin login")
        raise ApiError(401, "error.invalid_credentials", code="error.invalid_credentials")

    auth_service.ensure_not_locked(user)
    if not user.is_active:
        raise ApiError(403, "error.account_disabled", code="error.account_disabled")

    from ..models import AdminCredential

    parts = list(
        db.execute(
            select(AdminCredential)
            .where(AdminCredential.user_id == user.id)
            .order_by(AdminCredential.part_index)
        )
        .scalars()
        .all()
    )
    expected_parts = len(parts)        # 第 1 段为账号密码，其余为额外密码段
    if len(payload.passwords) < expected_parts:
        auth_service.register_failure(user, username=user.username, ip=ctx.ip, event="admin login")
        raise ApiError(
            401,
            "error.admin_password_parts",
            code="error.admin_password_parts",
            extra={"required_parts": expected_parts},
        )

    ok = verify_password(payload.passwords[0], user.password_hash)
    for index, credential in enumerate(parts):
        supplied = payload.passwords[index + 1] if index + 1 < len(payload.passwords) else ""
        # 全部密码段都要校验，避免通过响应时间推断出错在哪一段
        if not verify_password(supplied, credential.part_hash):
            ok = False

    if not ok:
        auth_service.register_failure(user, username=user.username, ip=ctx.ip, event="admin login")
        raise ApiError(401, "error.invalid_credentials", code="error.invalid_credentials")

    auth_service.register_success(user, ip=ctx.ip, scope="admin")
    sessions.create_session(
        user, remember=payload.remember, scope=sessions.SCOPE_ADMIN, request=request, response=response
    )
    logkit.log_event(
        "admin_login",
        f"admin {user.username} signed in to console",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    db.refresh(user)
    return user


@router.post("/auth/logout", response_model=MessageOut)
def logout(
    request: Request,
    response: Response,
    ctx: RequestContext = Depends(client_meta),
) -> MessageOut:
    sessions.revoke_session(request, sessions.SCOPE_USER)
    sessions.clear_session_cookie(response, sessions.SCOPE_USER)
    logkit.log_event("logout", "user signed out", ip=ctx.ip, path=ctx.path)
    return MessageOut(message="auth.logged_out")


@router.post("/auth/admin/logout", response_model=MessageOut)
def admin_logout(
    request: Request,
    response: Response,
    ctx: RequestContext = Depends(client_meta),
) -> MessageOut:
    sessions.revoke_session(request, sessions.SCOPE_ADMIN)
    sessions.clear_session_cookie(response, sessions.SCOPE_ADMIN)
    logkit.log_event("admin_logout", "admin signed out", ip=ctx.ip, path=ctx.path)
    return MessageOut(message="auth.logged_out")


@router.get("/auth/me", response_model=UserPublic)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.get("/auth/admin/me", response_model=UserPublic)
def admin_me(user: User = Depends(current_admin)) -> User:
    return user


@router.get("/auth/admin/parts")
def admin_parts() -> dict:
    """告知前端管理员登录需要几段密码（不泄露账号信息）。"""
    return {"parts": settings.ADMIN_PASSWORD_PARTS}


@router.post("/auth/password", response_model=MessageOut)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
) -> MessageOut:
    """修改自己的密码：校验原密码，改完清空所有会话并重新登录。"""
    if not verify_password(payload.current_password, user.password_hash):
        raise ApiError(400, "error.wrong_current_password", code="error.wrong_current_password")
    if verify_password(payload.new_password, user.password_hash):
        raise ApiError(400, "error.password_unchanged", code="error.password_unchanged")

    new_hash = hash_password(payload.new_password)

    def _update() -> None:
        with write_session() as session:
            fresh = session.get(User, user.id)
            if fresh is None:
                raise ApiError(404, "error.user_not_found", code="error.user_not_found")
            fresh.password_hash = new_hash
            session.flush()

    run_with_retry(_update, label="change_password")
    sessions.revoke_all_for_user(user.id)
    sessions.clear_session_cookie(response, sessions.SCOPE_USER)
    logkit.log_event(
        "password_changed",
        f"user {user.username} changed password",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="auth.password_changed")
