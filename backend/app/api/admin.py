"""管理员后台接口。

权限：全部路由都依赖 current_admin（独立管理员 Cookie + is_admin 双重校验）。
硬性约束（来自需求）：
  * 管理员可查看任意用户睡眠记录，但不可修改；只能删除（单条 / 批量）；
  * 管理员不能修改后端代码，接口只提供数据操作；
  * 所有列表必须分页 + 支持搜索 + 支持批量操作；
  * 审核通过注销申请后，清除该用户的一切数据与账号。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..core import logkit
from ..core.security import hash_password
from ..db import get_db, run_with_retry, write_session
from ..deps import RequestContext, client_meta, current_admin
from ..errors import ApiError
from ..models import (
    AdminCredential,
    DeletionRequest,
    LoginAttempt,
    SessionToken,
    SleepRecord,
    SystemLog,
    User,
    utcnow,
)
from ..schemas import (
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserListOut,
    AdminUserOut,
    AdminUserUpdate,
    DeletionDecision,
    DeletionRequestListOut,
    DeletionRequestOut,
    IdListRequest,
    MessageOut,
    SleepRecordListOut,
    SystemLogListOut,
)
from ..services import retention
from .common import page_offset, paginate_params, total_pages
from .records import serialize_record

router = APIRouter(prefix="/admin", tags=["admin"])

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式（仍走 ORM 参数绑定，无注入风险）。"""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _user_out(user: User, record_count: int = 0) -> AdminUserOut:
    payload = AdminUserOut.model_validate(user)
    payload.record_count = record_count
    payload.locked = bool(user.locked_until and user.locked_until > utcnow())
    return payload


def _record_counts(db: Session, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(SleepRecord.user_id, func.count(SleepRecord.id))
        .where(SleepRecord.user_id.in_(user_ids))
        .group_by(SleepRecord.user_id)
    ).all()
    return {int(user_id): int(count) for user_id, count in rows}


def _handled_by_names(db: Session, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    users = db.execute(select(User.id, User.username).where(User.id.in_(ids))).all()
    return {int(user_id): str(username) for user_id, username in users}


def _deletion_out(request: DeletionRequest, username: str | None, handled_by: str | None) -> DeletionRequestOut:
    payload = DeletionRequestOut.model_validate(request)
    payload.username = username
    payload.handled_by_username = handled_by
    return payload


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "error.user_not_found", code="error.user_not_found")
    return user


def _ensure_username_free(db: Session, username: str, *, exclude_id: int | None = None) -> None:
    statement = select(User.id).where(User.username == username)
    if exclude_id is not None:
        statement = statement.where(User.id != exclude_id)
    if db.execute(statement.limit(1)).scalar_one_or_none() is not None:
        raise ApiError(409, "error.username_taken", code="error.username_taken")


# --------------------------------------------------------------------------- #
# 用户管理
# --------------------------------------------------------------------------- #
@router.get("/users", response_model=AdminUserListOut)
def list_users(
    q: str | None = Query(default=None, max_length=32),
    is_admin: bool | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    pagination: tuple[int, int] = Depends(paginate_params),
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> AdminUserListOut:
    """用户列表：分页 + 按用户名搜索 + 状态筛选。"""
    page, page_size = pagination
    conditions = []
    if q and q.strip():
        conditions.append(User.username.like(_like(q.strip()), escape="\\"))
    if is_admin is not None:
        conditions.append(User.is_admin.is_(bool(is_admin)))
    if is_active is not None:
        conditions.append(User.is_active.is_(bool(is_active)))

    total = int(db.execute(select(func.count(User.id)).where(*conditions)).scalar_one())
    rows = list(
        db.execute(
            select(User)
            .where(*conditions)
            .order_by(User.id.desc())
            .offset(page_offset(page, page_size))
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    counts = _record_counts(db, [row.id for row in rows])
    return AdminUserListOut(
        items=[_user_out(row, counts.get(row.id, 0)) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages(total, page_size),
    )


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_user(
    user_id: int,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    user = _get_user_or_404(db, user_id)
    counts = _record_counts(db, [user.id])
    return _user_out(user, counts.get(user.id, 0))


@router.post("/users", response_model=AdminUserOut, status_code=201)
def create_user(
    payload: AdminUserCreate,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    """创建账号并设置初始密码（系统不支持自助注册）。"""
    _ensure_username_free(db, payload.username)
    admin_passwords = [p for p in payload.admin_passwords if p]
    if payload.is_admin:
        expected = max(0, settings.ADMIN_PASSWORD_PARTS - 1)
        if expected and len(admin_passwords) != expected:
            raise ApiError(
                400,
                "error.admin_password_parts",
                code="error.admin_password_parts",
                extra={"required_parts": expected},
            )
    elif admin_passwords:
        raise ApiError(400, "error.admin_parts_not_allowed", code="error.admin_parts_not_allowed")

    password_hash = hash_password(payload.password)
    part_hashes = [hash_password(item) for item in admin_passwords]

    def _write() -> int:
        with write_session() as session:
            user = User(
                username=payload.username,
                password_hash=password_hash,
                is_admin=bool(payload.is_admin),
                is_active=True,
                language=payload.language,
                theme=payload.theme,
            )
            session.add(user)
            session.flush()
            for index, part_hash in enumerate(part_hashes, start=1):
                session.add(
                    AdminCredential(user_id=user.id, part_index=index, part_hash=part_hash)
                )
            session.flush()
            return user.id

    user_id = run_with_retry(_write, label="admin_create_user")
    logkit.log_event(
        "admin_user_create",
        f"admin {admin.username} created user {payload.username} (id={user_id}, admin={bool(payload.is_admin)})",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    created = _get_user_or_404(db, user_id)
    return _user_out(created, 0)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    """启用 / 停用账号，或调整其语言主题默认值。"""
    target = _get_user_or_404(db, user_id)
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise ApiError(400, "error.nothing_to_update", code="error.nothing_to_update")
    if values.get("is_active") is False:
        if target.id == admin.id:
            raise ApiError(400, "error.cannot_disable_self", code="error.cannot_disable_self")
        if target.is_admin:
            remaining = int(
                db.execute(
                    select(func.count(User.id)).where(
                        User.is_admin.is_(True), User.is_active.is_(True), User.id != target.id
                    )
                ).scalar_one()
            )
            if remaining == 0:
                raise ApiError(
                    400, "error.last_admin", code="error.last_admin"
                )

    def _update() -> None:
        with write_session() as session:
            fresh = session.get(User, user_id)
            if fresh is None:
                raise ApiError(404, "error.user_not_found", code="error.user_not_found")
            for key, value in values.items():
                setattr(fresh, key, value)
            if values.get("is_active") is False:
                # 停用后立即失效其全部会话
                session.execute(
                    delete(SessionToken).where(SessionToken.user_id == user_id)
                )
            session.flush()

    run_with_retry(_update, label="admin_update_user")
    logkit.log_event(
        "admin_user_update",
        f"admin {admin.username} updated user {target.username}: {sorted(values)}",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    db.expire_all()
    fresh = _get_user_or_404(db, user_id)
    counts = _record_counts(db, [user_id])
    return _user_out(fresh, counts.get(user_id, 0))


@router.post("/users/{user_id}/password", response_model=MessageOut)
def reset_user_password(
    user_id: int,
    payload: AdminPasswordReset,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """重置用户密码并强制其重新登录。"""
    target = _get_user_or_404(db, user_id)
    new_hash = hash_password(payload.new_password)

    def _update() -> None:
        with write_session() as session:
            fresh = session.get(User, user_id)
            if fresh is None:
                raise ApiError(404, "error.user_not_found", code="error.user_not_found")
            fresh.password_hash = new_hash
            fresh.failed_login_count = 0
            fresh.locked_until = None
            session.execute(delete(SessionToken).where(SessionToken.user_id == user_id))
            session.flush()

    run_with_retry(_update, label="admin_reset_password")
    logkit.log_event(
        "admin_password_reset",
        f"admin {admin.username} reset password of {target.username}",
        level="WARNING",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="admin.password_reset_done")


@router.post("/users/{user_id}/unlock", response_model=MessageOut)
def unlock_user(
    user_id: int,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """解除因连续登录失败导致的账号锁定。"""
    target = _get_user_or_404(db, user_id)

    def _update() -> None:
        with write_session() as session:
            fresh = session.get(User, user_id)
            if fresh is None:
                raise ApiError(404, "error.user_not_found", code="error.user_not_found")
            fresh.failed_login_count = 0
            fresh.locked_until = None
            session.flush()

    run_with_retry(_update, label="admin_unlock_user")
    logkit.log_event(
        "admin_user_unlock",
        f"admin {admin.username} unlocked {target.username}",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="admin.user_unlocked")


@router.delete("/users/{user_id}", response_model=MessageOut)
def delete_user(
    user_id: int,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """删除账号：级联清除其睡眠记录、会话、注销申请；日志保留但解除关联。"""
    target = _get_user_or_404(db, user_id)
    if target.id == admin.id:
        raise ApiError(400, "error.cannot_delete_self", code="error.cannot_delete_self")
    if target.is_admin:
        remaining = int(
            db.execute(
                select(func.count(User.id)).where(User.is_admin.is_(True), User.id != target.id)
            ).scalar_one()
        )
        if remaining == 0:
            raise ApiError(400, "error.last_admin", code="error.last_admin")

    username = target.username

    def _delete() -> None:
        with write_session() as session:
            # 显式清理，兼容未启用外键级联的环境
            session.execute(delete(SleepRecord).where(SleepRecord.user_id == user_id))
            session.execute(delete(SessionToken).where(SessionToken.user_id == user_id))
            session.execute(delete(AdminCredential).where(AdminCredential.user_id == user_id))
            session.execute(delete(DeletionRequest).where(DeletionRequest.user_id == user_id))
            session.execute(delete(LoginAttempt).where(LoginAttempt.username == username))
            fresh = session.get(User, user_id)
            if fresh is not None:
                session.delete(fresh)
            session.flush()

    run_with_retry(_delete, label="admin_delete_user")
    logkit.log_event(
        "admin_user_delete",
        f"admin {admin.username} deleted user {username} (id={user_id})",
        level="WARNING",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="admin.user_deleted")


# --------------------------------------------------------------------------- #
# 用户睡眠记录（只读 + 删除，不可修改）
# --------------------------------------------------------------------------- #
@router.get("/users/{user_id}/records", response_model=SleepRecordListOut)
def list_user_records(
    user_id: int,
    start_date: str | None = Query(default=None, max_length=10),
    end_date: str | None = Query(default=None, max_length=10),
    pagination: tuple[int, int] = Depends(paginate_params),
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> SleepRecordListOut:
    user = _get_user_or_404(db, user_id)
    page, page_size = pagination
    conditions = [SleepRecord.user_id == user_id]
    if start_date:
        conditions.append(SleepRecord.record_date >= start_date)
    if end_date:
        conditions.append(SleepRecord.record_date <= end_date)

    total = int(db.execute(select(func.count(SleepRecord.id)).where(*conditions)).scalar_one())
    rows = (
        db.execute(
            select(SleepRecord)
            .where(*conditions)
            .order_by(SleepRecord.record_date.desc(), SleepRecord.id.desc())
            .offset(page_offset(page, page_size))
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return SleepRecordListOut(
        items=[serialize_record(row, username=user.username) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages(total, page_size),
    )


@router.delete("/users/{user_id}/records/{record_id}", response_model=MessageOut)
def delete_user_record(
    user_id: int,
    record_id: int,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """删除某用户的单条睡眠记录（管理员不能修改内容）。"""
    user = _get_user_or_404(db, user_id)

    def _delete() -> int:
        with write_session() as session:
            result = session.execute(
                delete(SleepRecord).where(
                    SleepRecord.id == record_id, SleepRecord.user_id == user_id
                )
            )
            session.flush()
            return result.rowcount or 0

    removed = run_with_retry(_delete, label="admin_delete_record")
    if not removed:
        raise ApiError(404, "error.record_not_found", code="error.record_not_found")
    logkit.log_event(
        "admin_record_delete",
        f"admin {admin.username} deleted record #{record_id} of {user.username}",
        level="WARNING",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="record.deleted", extra={"deleted": removed})


@router.post("/users/{user_id}/records/batch-delete", response_model=MessageOut)
def batch_delete_user_records(
    user_id: int,
    payload: IdListRequest,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """批量删除某用户的睡眠记录（用户详情页复选框批量操作）。"""
    user = _get_user_or_404(db, user_id)
    if not payload.ids:
        raise ApiError(400, "error.nothing_selected", code="error.nothing_selected")

    def _delete() -> int:
        with write_session() as session:
            result = session.execute(
                delete(SleepRecord).where(
                    SleepRecord.user_id == user_id, SleepRecord.id.in_(payload.ids)
                )
            )
            session.flush()
            return result.rowcount or 0

    removed = run_with_retry(_delete, label="admin_batch_delete_records")
    logkit.log_event(
        "admin_record_batch_delete",
        f"admin {admin.username} batch deleted {removed} records of {user.username}",
        level="WARNING",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="record.deleted", extra={"deleted": removed})


# --------------------------------------------------------------------------- #
# 注销审核
# --------------------------------------------------------------------------- #
@router.get("/deletion-requests", response_model=DeletionRequestListOut)
def list_deletion_requests(
    status: str | None = Query(default=None, max_length=16),
    username: str | None = Query(default=None, max_length=32),
    pagination: tuple[int, int] = Depends(paginate_params),
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> DeletionRequestListOut:
    page, page_size = pagination
    conditions = []
    if status in (DeletionRequest.STATUS_PENDING, DeletionRequest.STATUS_APPROVED, DeletionRequest.STATUS_REJECTED):
        conditions.append(DeletionRequest.status == status)
    elif status == "all":
        pass
    else:
        conditions.append(DeletionRequest.status == DeletionRequest.STATUS_PENDING)
    if username and username.strip():
        matched = select(User.id).where(User.username.like(_like(username.strip()), escape="\\"))
        conditions.append(DeletionRequest.user_id.in_(matched))

    total = int(
        db.execute(select(func.count(DeletionRequest.id)).where(*conditions)).scalar_one()
    )
    rows = list(
        db.execute(
            select(DeletionRequest)
            .where(*conditions)
            .order_by(DeletionRequest.status.asc(), DeletionRequest.id.desc())
            .offset(page_offset(page, page_size))
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    names = _handled_by_names(db, [row.user_id for row in rows if row.user_id])
    handless = _handled_by_names(db, [row.handled_by for row in rows if row.handled_by])
    return DeletionRequestListOut(
        items=[
            _deletion_out(
                row,
                # 优先用行上的用户名快照：审核通过后账号已被真实删除，回查 users 表拿不到名字
                row.username or names.get(row.user_id or 0),
                handless.get(row.handled_by or 0),
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages(total, page_size),
    )


@router.post("/deletion-requests/{request_id}", response_model=MessageOut)
def decide_deletion_request(
    request_id: int,
    payload: DeletionDecision,
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """审核注销申请：通过则清除该用户全部数据与账号，拒绝则仅记录。"""
    request = db.get(DeletionRequest, request_id)
    if request is None:
        raise ApiError(404, "error.deletion_not_found", code="error.deletion_not_found")
    if request.status != DeletionRequest.STATUS_PENDING:
        raise ApiError(409, "error.deletion_already_handled", code="error.deletion_already_handled")
    if request.user_id == admin.id:
        raise ApiError(400, "error.cannot_delete_self", code="error.cannot_delete_self")

    user = db.get(User, request.user_id)
    username = user.username if user is not None else f"#{request.user_id}"
    approve = payload.action == "approve"

    def _handle() -> None:
        with write_session() as session:
            fresh = session.get(DeletionRequest, request_id)
            if fresh is None or fresh.status != DeletionRequest.STATUS_PENDING:
                raise ApiError(409, "error.deletion_already_handled", code="error.deletion_already_handled")
            fresh.status = (
                DeletionRequest.STATUS_APPROVED if approve else DeletionRequest.STATUS_REJECTED
            )
            fresh.handled_at = utcnow()
            fresh.handled_by = admin.id
            fresh.handle_note = payload.note
            if approve:
                # 审计记录保留为 approved，但由于申请人的账号会被真实删除，
                # 这里把 user_id 置空并留下用户名快照，避免外键把审核记录一起带走。
                target_id = fresh.user_id
                target = session.get(User, target_id) if target_id else None
                target_name = target.username if target is not None else username
                fresh.username = target_name
                fresh.user_id = None
                session.flush()
                if target_id:
                    session.execute(delete(SleepRecord).where(SleepRecord.user_id == target_id))
                    session.execute(delete(SessionToken).where(SessionToken.user_id == target_id))
                    session.execute(
                        delete(AdminCredential).where(AdminCredential.user_id == target_id)
                    )
                    session.execute(delete(LoginAttempt).where(LoginAttempt.username == target_name))
                    session.execute(
                        delete(DeletionRequest).where(
                            DeletionRequest.user_id == target_id,
                            DeletionRequest.id != request_id,
                        )
                    )
                if target is not None:
                    session.delete(target)
            session.flush()

    run_with_retry(_handle, label="decide_deletion_request")
    logkit.log_event(
        "deletion_approved" if approve else "deletion_rejected",
        f"admin {admin.username} {'approved' if approve else 'rejected'} deletion of {username}",
        level="WARNING",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(
        message="admin.deletion_approved" if approve else "admin.deletion_rejected"
    )


# --------------------------------------------------------------------------- #
# 系统日志
# --------------------------------------------------------------------------- #
@router.get("/logs", response_model=SystemLogListOut)
def list_logs(
    level: str | None = Query(default=None, max_length=10),
    event: str | None = Query(default=None, max_length=48),
    q: str | None = Query(default=None, max_length=64),
    user_id: int | None = Query(default=None, ge=1),
    pagination: tuple[int, int] = Depends(paginate_params),
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> SystemLogListOut:
    """系统日志：分页 + 按级别/事件/关键字/用户筛选。"""
    page, page_size = pagination
    conditions = []
    if level and level.upper() in LOG_LEVELS:
        conditions.append(SystemLog.level == level.upper())
    if event and event.strip():
        conditions.append(SystemLog.event == event.strip())
    if user_id is not None:
        conditions.append(SystemLog.user_id == user_id)
    if q and q.strip():
        pattern = _like(q.strip())
        conditions.append(
            or_(
                SystemLog.message.like(pattern, escape="\\"),
                SystemLog.username.like(pattern, escape="\\"),
                SystemLog.ip.like(pattern, escape="\\"),
            )
        )

    total = int(db.execute(select(func.count(SystemLog.id)).where(*conditions)).scalar_one())
    rows = (
        db.execute(
            select(SystemLog)
            .where(*conditions)
            .order_by(SystemLog.id.desc())
            .offset(page_offset(page, page_size))
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    counts = {
        str(name): int(count)
        for name, count in db.execute(
            select(SystemLog.level, func.count(SystemLog.id)).group_by(SystemLog.level)
        ).all()
    }
    return SystemLogListOut(
        items=list(rows),
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages(total, page_size),
        level_counts=counts,
    )


@router.get("/stats")
def admin_stats(admin: User = Depends(current_admin), db: Session = Depends(get_db)) -> dict:
    """后台首页概览数字（需求明确不要图表，只给计数）。"""
    user_count = int(db.execute(select(func.count(User.id))).scalar_one())
    record_count = int(db.execute(select(func.count(SleepRecord.id))).scalar_one())
    pending = int(
        db.execute(
            select(func.count(DeletionRequest.id)).where(
                DeletionRequest.status == DeletionRequest.STATUS_PENDING
            )
        ).scalar_one()
    )
    locked = int(
        db.execute(
            select(func.count(User.id)).where(User.locked_until.is_not(None), User.locked_until > utcnow())
        ).scalar_one()
    )
    return {
        "users": user_count,
        "records": record_count,
        "pending_deletions": pending,
        "locked_users": locked,
        "admin": {"id": admin.id, "username": admin.username},
        "retention_months": settings.RETENTION_MONTHS,
    }


@router.post("/cleanup", response_model=MessageOut)
def run_cleanup_now(
    admin: User = Depends(current_admin),
    ctx: RequestContext = Depends(client_meta),
) -> MessageOut:
    """手动触发一次保留策略清理（6 个月前的记录、过期会话、旧日志）。"""
    result = retention.run_cleanup()
    logkit.log_event(
        "admin_cleanup",
        f"admin {admin.username} triggered cleanup: {result}",
        user_id=admin.id,
        username=admin.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="admin.cleanup_done", extra={"result": result})


__all__ = ["router"]
