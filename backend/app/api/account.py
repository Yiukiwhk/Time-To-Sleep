"""账号自助接口：提交 / 查看 / 撤销注销申请。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import logkit
from ..db import get_db, run_with_retry, write_session
from ..deps import RequestContext, client_meta, current_user
from ..errors import ApiError
from ..models import DeletionRequest, User, utcnow
from ..schemas import DeletionRequestCreate, DeletionRequestOut, MessageOut

router = APIRouter(tags=["account"])


def _latest_request(db: Session, user_id: int) -> DeletionRequest | None:
    return db.execute(
        select(DeletionRequest)
        .where(DeletionRequest.user_id == user_id)
        .order_by(DeletionRequest.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _serialize(request: DeletionRequest, handled_by_username: str | None = None) -> DeletionRequestOut:
    payload = DeletionRequestOut.model_validate(request)
    if payload.username is None and request.user_id is not None:
        payload.username = handled_by_username
    payload.handled_by_username = handled_by_username
    return payload


@router.get("/account/deletion")
def deletion_status(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """查看自己最近的注销申请状态（没有则返回 none）。"""
    request = _latest_request(db, user.id)
    if request is None:
        return {"status": "none", "request": None, "pending": False}
    handled_by_name = None
    if request.handled_by:
        admin = db.get(User, request.handled_by)
        handled_by_name = admin.username if admin is not None else None
    return {
        "status": request.status,
        "pending": request.status == DeletionRequest.STATUS_PENDING,
        "request": _serialize(request, handled_by_name).model_dump(mode="json"),
    }


@router.post("/account/deletion", response_model=DeletionRequestOut, status_code=201)
def submit_deletion_request(
    payload: DeletionRequestCreate,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> DeletionRequestOut:
    """提交账号注销申请，等待管理员审核。"""
    if user.is_admin:
        raise ApiError(400, "error.admin_cannot_deactivate", code="error.admin_cannot_deactivate")
    existing = _latest_request(db, user.id)
    if existing is not None and existing.status == DeletionRequest.STATUS_PENDING:
        raise ApiError(409, "error.deletion_pending", code="error.deletion_pending")

    def _write() -> int:
        with write_session() as session:
            request = DeletionRequest(
                user_id=user.id,
                username=user.username,
                status=DeletionRequest.STATUS_PENDING,
                reason=payload.reason,
                requested_at=utcnow(),
            )
            session.add(request)
            session.flush()
            return request.id

    request_id = run_with_retry(_write, label="create_deletion_request")
    logkit.log_event(
        "deletion_requested",
        f"user {user.username} requested account deletion (#{request_id})",
        level="WARNING",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    created = db.get(DeletionRequest, request_id)
    if created is None:                                   # pragma: no cover - 并发删除
        raise ApiError(500, "error.server", code="error.server")
    return _serialize(created)


@router.delete("/account/deletion", response_model=MessageOut)
def cancel_deletion_request(
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> MessageOut:
    """撤销尚未审核的注销申请（记录状态置为 rejected，保留审计痕迹）。"""
    pending = db.execute(
        select(DeletionRequest)
        .where(
            DeletionRequest.user_id == user.id,
            DeletionRequest.status == DeletionRequest.STATUS_PENDING,
        )
        .order_by(DeletionRequest.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if pending is None:
        raise ApiError(404, "error.deletion_not_found", code="error.deletion_not_found")

    def _cancel() -> None:
        with write_session() as session:
            fresh = session.get(DeletionRequest, pending.id)
            if fresh is None:                              # pragma: no cover - 并发删除
                return
            fresh.status = DeletionRequest.STATUS_REJECTED
            fresh.handled_at = utcnow()
            fresh.handle_note = "cancelled_by_user"
            session.flush()

    run_with_retry(_cancel, label="cancel_deletion_request")
    logkit.log_event(
        "deletion_cancelled",
        f"user {user.username} cancelled deletion request #{pending.id}",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="account.deletion_cancelled")


__all__ = ["router"]
