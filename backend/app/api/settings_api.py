"""个性化设置接口：语言、主题、三餐开关、自定义目标作息。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core import logkit
from ..db import get_db, run_with_retry, write_session
from ..deps import RequestContext, client_meta, current_user
from ..errors import ApiError
from ..models import User
from ..schemas import (
    PreferenceUpdate,
    TargetScheduleUpdate,
    UserPublic,
)

router = APIRouter(tags=["settings"])


def _apply_updates(user_id: int, values: dict) -> None:
    def _update() -> None:
        with write_session() as session:
            fresh = session.get(User, user_id)
            if fresh is None:
                raise ApiError(404, "error.user_not_found", code="error.user_not_found")
            for key, value in values.items():
                setattr(fresh, key, value)
            session.flush()

    run_with_retry(_update, label="update_profile")


@router.get("/settings", response_model=UserPublic)
def read_settings(user: User = Depends(current_user)) -> User:
    """读取当前账号配置（语言、主题、三餐开关、目标作息）。"""
    return user


@router.put("/settings", response_model=UserPublic)
def update_settings(
    payload: PreferenceUpdate,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> User:
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not values:
        raise ApiError(400, "error.nothing_to_update", code="error.nothing_to_update")
    _apply_updates(user.id, values)
    if any(key in values for key in ("language", "theme")):
        logkit.log_event(
            "preference_update",
            f"user {user.username} updated preferences: {sorted(values)}",
            user_id=user.id,
            username=user.username,
            ip=ctx.ip,
            path=ctx.path,
        )
    db.expire_all()
    fresh = db.get(User, user.id)
    if fresh is None:                                     # pragma: no cover - 并发删除
        raise ApiError(404, "error.user_not_found", code="error.user_not_found")
    return fresh


@router.put("/settings/target", response_model=UserPublic)
def update_target_schedule(
    payload: TargetScheduleUpdate,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> User:
    """设置自定义目标作息；传 null 表示清除并恢复系统推荐。"""
    values = {"target_sleep_time": payload.target_sleep_time, "target_wake_time": payload.target_wake_time}
    if (values["target_sleep_time"] is None) ^ (values["target_wake_time"] is None):
        # 目标作息必须成对设置，否则无法计算收敛区间
        raise ApiError(400, "error.target_requires_both", code="error.target_requires_both")

    _apply_updates(user.id, values)
    logkit.log_event(
        "target_schedule_update",
        f"user {user.username} target schedule = "
        f"{values['target_sleep_time']} -> {values['target_wake_time']}",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    db.expire_all()
    fresh = db.get(User, user.id)
    if fresh is None:                                     # pragma: no cover - 并发删除
        raise ApiError(404, "error.user_not_found", code="error.user_not_found")
    return fresh


__all__ = ["router"]
