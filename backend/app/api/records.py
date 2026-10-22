"""睡眠记录接口（用户端）。

要点：
  * 所有查询强制带 user_id 过滤，杜绝越权访问他人数据；
  * 写入（新增/修改）受 10 秒防重复提交限制；
  * 列表一律分页，禁止一次性加载全部数据。
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.timing import (
    derive_meal_date,
    derive_wake_date,
    parse_hhmm,
    parse_iso_date,
    span_minutes,
)
from ..db import get_db, run_with_retry, write_session
from ..deps import RequestContext, client_meta, current_user
from ..errors import ApiError
from ..models import SleepRecord, User, utcnow
from ..schemas import (
    IdListRequest,
    MessageOut,
    SleepRecordCreate,
    SleepRecordListOut,
    SleepRecordOut,
    SleepRecordUpdate,
)
from ..services import retention
from ..core import logkit
from .common import page_offset, paginate_params, total_pages

router = APIRouter(tags=["records"])

MAX_PAST_DAYS = 3650          # 允许补录的时间上限（约 10 年）
MAX_FUTURE_DAYS = 1           # 允许 1 天时差


def _cooldown_remaining(user: User) -> int:
    if user.last_write_at is None:
        return 0
    elapsed = (utcnow() - user.last_write_at).total_seconds()
    remaining = settings.WRITE_COOLDOWN_SECONDS - elapsed
    return int(remaining) + 1 if remaining > 0 else 0


def _ensure_writable(user: User) -> None:
    """10 秒内不允许重复提交写入请求。"""
    remaining = _cooldown_remaining(user)
    if remaining > 0:
        raise ApiError(
            429,
            "error.write_too_fast",
            code="error.write_too_fast",
            extra={
                "retry_after_seconds": remaining,
                "cooldown_seconds": settings.WRITE_COOLDOWN_SECONDS,
            },
        )


def _touch_write(user_id: int) -> None:
    """记录写入时间（在写入成功之后调用）。"""

    def _update() -> None:
        with write_session() as session:
            fresh = session.get(User, user_id)
            if fresh is not None:
                fresh.last_write_at = utcnow()
            session.flush()

    run_with_retry(_update, label="touch_write")


def _check_record_date(value: str | None) -> str:
    text = value or date.today().isoformat()
    try:
        parsed = parse_iso_date(text)
    except ValueError as exc:
        raise ApiError(400, "error.invalid_date", code="error.invalid_date") from exc
    today = date.today()
    if parsed > today + timedelta(days=MAX_FUTURE_DAYS):
        raise ApiError(400, "error.date_in_future", code="error.date_in_future")
    if parsed < today - timedelta(days=MAX_PAST_DAYS):
        raise ApiError(400, "error.date_too_old", code="error.date_too_old")
    return parsed.isoformat()


def _check_wake_date(
    record_date: str, wake_date: str | None, sleep_time: str, wake_time: str
) -> str:
    """起床日历日：缺省时按老规则推导；显式给出时校验范围。

    规则与前端一致：起床日只能是入睡日当天（午睡、凌晨睡早上起）或次日（正常跨夜），
    再远就说明填错了。时长必须为正，否则同一天的"起床早于入睡"会被静默存成负数。
    """
    if not wake_date:
        text = derive_wake_date(record_date, sleep_time, wake_time)
    else:
        text = _check_record_date(wake_date)
        gap = (parse_iso_date(text) - parse_iso_date(record_date)).days
        if gap < 0 or gap > 1:
            raise ApiError(400, "error.invalid_date_range", code="error.invalid_date_range")
    if span_minutes(record_date, sleep_time, text, wake_time) <= 0:
        raise ApiError(400, "error.invalid_time_range", code="error.invalid_time_range")
    return text


def _default_meal_date(record_date: str, wake_date: str | None) -> str:
    """三餐记录的默认日期：入睡日；入睡与醒来同一天（跨零点入睡）则前一天。

    为什么是前一天：那一晚的三餐其实都在前一天吃完了（晚餐吃完才睡、早餐是醒来之后
    新的一天的），所以"进入睡眠那天"的三餐要往前算一天。
    """
    # 与建议服务共用同一套默认规则：跨零点入睡 → 三餐算在前一天
    return derive_meal_date(record_date, wake_date)


def _check_meal_date(record_date: str, wake_date: str | None, meal_date: str | None) -> str:
    """三餐日期：缺省时按默认规则补；显式给出时只允许"入睡日"或"入睡日的前一天"。"""
    if not meal_date:
        return _default_meal_date(record_date, wake_date)
    text = _check_record_date(meal_date)
    gap = (parse_iso_date(record_date) - parse_iso_date(text)).days
    if gap < 0 or gap > 1:
        raise ApiError(400, "error.invalid_meal_date", code="error.invalid_meal_date")
    return text


def serialize_record(record: SleepRecord, *, username: str | None = None) -> SleepRecordOut:
    try:
        duration = span_minutes(
            record.record_date, record.sleep_time, record.wake_date, record.wake_time
        )
    except Exception:  # noqa: BLE001 - 历史脏数据不应导致 500
        duration = 0
    payload = SleepRecordOut.model_validate(record)
    payload.duration_minutes = max(0, duration)
    payload.username = username
    if not payload.meal_date:
        # 迁移前直接写库的老行可能没有这一列：按同一套默认规则补出来，别让前端拿到 null。
        try:
            payload.meal_date = _default_meal_date(record.record_date, record.wake_date)
        except Exception:  # noqa: BLE001 - 日期脏数据不该导致 500
            payload.meal_date = record.record_date
    return payload


@router.get("/records", response_model=SleepRecordListOut)
def list_my_records(
    start_date: str | None = Query(default=None, max_length=10),
    end_date: str | None = Query(default=None, max_length=10),
    pagination: tuple[int, int] = Depends(paginate_params),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SleepRecordListOut:
    page, page_size = pagination
    conditions = [SleepRecord.user_id == user.id]
    if start_date:
        conditions.append(SleepRecord.record_date >= _check_record_date(start_date))
    if end_date:
        conditions.append(SleepRecord.record_date <= _check_record_date(end_date))

    total = int(
        db.execute(select(func.count(SleepRecord.id)).where(*conditions)).scalar_one()
    )
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
        items=[serialize_record(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages(total, page_size),
    )


@router.get("/records/meta")
def my_records_meta(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """录入页需要的元信息：可录入日期范围、保留策略、写入冷却剩余时间。"""
    total = int(
        db.execute(
            select(func.count(SleepRecord.id)).where(SleepRecord.user_id == user.id)
        ).scalar_one()
    )
    latest = db.execute(
        select(SleepRecord.record_date)
        .where(SleepRecord.user_id == user.id)
        .order_by(SleepRecord.record_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "total": total,
        "latest_record_date": latest,
        "retention_months": settings.RETENTION_MONTHS,
        "retention_cutoff": retention.retention_cutoff(),
        "write_cooldown_seconds": settings.WRITE_COOLDOWN_SECONDS,
        "cooldown_remaining_seconds": _cooldown_remaining(user),
        "today": date.today().isoformat(),
        "max_future_days": MAX_FUTURE_DAYS,
    }


@router.get("/records/{record_id}", response_model=SleepRecordOut)
def get_my_record(
    record_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SleepRecordOut:
    record = db.get(SleepRecord, record_id)
    if record is None or record.user_id != user.id:      # 越权一律按不存在处理
        raise ApiError(404, "error.record_not_found", code="error.record_not_found")
    return serialize_record(record)


@router.post("/records", response_model=SleepRecordOut, status_code=201)
def create_my_record(
    payload: SleepRecordCreate,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> SleepRecordOut:
    _ensure_writable(user)
    record_date = _check_record_date(payload.record_date)
    wake_date = _check_wake_date(
        record_date, payload.wake_date, payload.sleep_time, payload.wake_time
    )
    meal_date = _check_meal_date(record_date, wake_date, payload.meal_date)

    def _write() -> int:
        with write_session() as session:
            record = SleepRecord(
                user_id=user.id,
                sleep_time=payload.sleep_time,
                wake_time=payload.wake_time,
                meal_breakfast=payload.meal_breakfast,
                meal_lunch=payload.meal_lunch,
                meal_dinner=payload.meal_dinner,
                record_date=record_date,
                wake_date=wake_date,
                meal_date=meal_date,
                note=payload.note,
            )
            session.add(record)
            session.flush()
            return record.id

    record_id = run_with_retry(_write, label="create_record")
    _touch_write(user.id)
    logkit.log_event(
        "record_create",
        f"user {user.username} created record #{record_id} for {record_date} -> {wake_date} (meals {meal_date})",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    created = db.get(SleepRecord, record_id)
    if created is None:                                  # pragma: no cover - 并发删除
        raise ApiError(500, "error.server", code="error.server")
    return serialize_record(created)


@router.put("/records/{record_id}", response_model=SleepRecordOut)
def update_my_record(
    record_id: int,
    payload: SleepRecordUpdate,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
    db: Session = Depends(get_db),
) -> SleepRecordOut:
    record = db.get(SleepRecord, record_id)
    if record is None or record.user_id != user.id:
        raise ApiError(404, "error.record_not_found", code="error.record_not_found")

    _ensure_writable(user)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ApiError(400, "error.nothing_to_update", code="error.nothing_to_update")
    if "record_date" in data:
        data["record_date"] = _check_record_date(data["record_date"])
    # 显式传 null 表示"清空该选填项"；必填时间不允许改成空
    for required in ("sleep_time", "wake_time"):
        if required in data and data[required] is None:
            raise ApiError(400, "error.invalid_time", code="error.invalid_time")
    # 校验要落在"改完之后"的那份数据上：只改时刻也会影响时长，两个日期与时刻必须自洽。
    effective_date = data.get("record_date") or record.record_date
    effective_sleep = data.get("sleep_time") or record.sleep_time
    effective_wake = data.get("wake_time") or record.wake_time
    wake_input = data["wake_date"] if "wake_date" in data else record.wake_date
    effective_wake_date = _check_wake_date(
        effective_date, wake_input, effective_sleep, effective_wake
    )
    if effective_wake_date != record.wake_date:
        data["wake_date"] = effective_wake_date
    # 三餐日期：显式改了就校验；没改但改了那两个日期，就跟表单一样重算默认值；
    # 其他字段的编辑不该悄悄把用户挑过的三餐日期改掉。
    if "meal_date" in data:
        meal_input = data["meal_date"]
        effective_meal_date = (
            _check_meal_date(effective_date, effective_wake_date, meal_input)
            if meal_input
            else _default_meal_date(effective_date, effective_wake_date)
        )
    elif "record_date" in data or "wake_date" in data:
        effective_meal_date = _default_meal_date(effective_date, effective_wake_date)
    else:
        effective_meal_date = record.meal_date or _default_meal_date(
            effective_date, effective_wake_date
        )
    if effective_meal_date != record.meal_date:
        data["meal_date"] = effective_meal_date

    def _update() -> None:
        with write_session() as session:
            fresh = session.get(SleepRecord, record_id)
            if fresh is None or fresh.user_id != user.id:
                raise ApiError(404, "error.record_not_found", code="error.record_not_found")
            for key, value in data.items():
                setattr(fresh, key, value)
            session.flush()

    run_with_retry(_update, label="update_record")
    _touch_write(user.id)
    logkit.log_event(
        "record_update",
        f"user {user.username} updated record #{record_id}",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    db.expire_all()
    updated = db.get(SleepRecord, record_id)
    if updated is None:                                  # pragma: no cover - 并发删除
        raise ApiError(404, "error.record_not_found", code="error.record_not_found")
    return serialize_record(updated)


@router.delete("/records/{record_id}", response_model=MessageOut)
def delete_my_record(
    record_id: int,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
) -> MessageOut:
    def _delete() -> int:
        with write_session() as session:
            result = session.execute(
                delete(SleepRecord).where(
                    SleepRecord.id == record_id, SleepRecord.user_id == user.id
                )
            )
            session.flush()
            return result.rowcount or 0

    removed = run_with_retry(_delete, label="delete_record")
    if not removed:
        raise ApiError(404, "error.record_not_found", code="error.record_not_found")
    logkit.log_event(
        "record_delete",
        f"user {user.username} deleted record #{record_id}",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="record.deleted", extra={"deleted": removed})


@router.post("/records/batch-delete", response_model=MessageOut)
def batch_delete_my_records(
    payload: IdListRequest,
    user: User = Depends(current_user),
    ctx: RequestContext = Depends(client_meta),
) -> MessageOut:
    """批量删除自己的记录（前端复选框批量操作）。"""
    if not payload.ids:
        raise ApiError(400, "error.nothing_selected", code="error.nothing_selected")

    def _delete() -> int:
        with write_session() as session:
            result = session.execute(
                delete(SleepRecord).where(
                    SleepRecord.user_id == user.id, SleepRecord.id.in_(payload.ids)
                )
            )
            session.flush()
            return result.rowcount or 0

    removed = run_with_retry(_delete, label="batch_delete_records")
    logkit.log_event(
        "record_batch_delete",
        f"user {user.username} batch deleted {removed} records",
        user_id=user.id,
        username=user.username,
        ip=ctx.ip,
        path=ctx.path,
    )
    return MessageOut(message="record.deleted", extra={"deleted": removed})


__all__ = ["router", "serialize_record"]
