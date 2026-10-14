"""计划快照、三餐确认、偏离重算与达标统计。

与 `services/advice.py` 的分工：
  * advice.py 只做纯计算（读记录 → 出建议），不写库；
  * 本模块负责持久化（AdvicePlan / MealCheck / users.advice_notice_ack）、
    "计划 vs 实际"的偏离判定与达标统计，以及弹窗里三种重算策略。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select

from ..core.timing import circular_diff_minutes, parse_hhmm
from ..db import run_with_retry, write_session
from ..models import AdvicePlan, MealCheck, SleepRecord, User, utcnow
from . import advice as advice_service


# --------------------------------------------------------------------------- #
# 快照读写
# --------------------------------------------------------------------------- #
def save_plan(
    db,
    user_id: int,
    payload: dict,
    *,
    replans: int = 0,
    postponed_days: int = 0,
) -> int:
    """保存一份计划快照，并把该用户此前的活跃计划置为非活跃。"""

    def _write() -> int:
        with write_session() as session:
            for row in session.execute(
                select(AdvicePlan).where(
                    AdvicePlan.user_id == user_id, AdvicePlan.is_active.is_(True)
                )
            ).scalars():
                row.is_active = False
            plan = AdvicePlan(
                user_id=user_id,
                is_active=True,
                mode=payload.get("mode") or "slow",
                days=int(payload.get("days") or 1),
                start_date=payload["plan_meta"]["start_date"],
                target_date=payload["plan_meta"]["target_date"],
                target_sleep_time=payload["target"]["sleep_time"],
                target_wake_time=payload["target"]["wake_time"],
                engine=payload.get("engine") or advice_service.ENGINE,
                payload=json.dumps(payload, ensure_ascii=False, default=str),
                replans=replans,
                postponed_days=postponed_days,
            )
            session.add(plan)
            session.flush()
            return int(plan.id)

    return run_with_retry(_write, label="save_advice_plan")


def active_plan(db, user_id: int) -> AdvicePlan | None:
    return (
        db.execute(
            select(AdvicePlan)
            .where(AdvicePlan.user_id == user_id, AdvicePlan.is_active.is_(True))
            .order_by(AdvicePlan.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def plan_payload(plan: AdvicePlan | None) -> dict | None:
    if plan is None:
        return None
    try:
        return json.loads(plan.payload)
    except ValueError:            # pragma: no cover - 历史脏数据
        return None


def ack_notice(user_id: int, code: str) -> list[str]:
    """记录"我已知晓，不再提示"（账号级，跨设备生效）。"""

    def _write() -> list[str]:
        with write_session() as session:
            fresh = session.get(User, user_id)
            if fresh is None:
                return []
            current = {
                item.strip()
                for item in (fresh.advice_notice_ack or "").split(",")
                if item.strip()
            }
            current.add(code)
            fresh.advice_notice_ack = ",".join(sorted(current))[:64]
            session.flush()
            return sorted(current)

    return run_with_retry(_write, label="ack_advice_notice")


def checkin(user_id: int, *, check_date: str, meal: str, eaten: bool) -> None:
    """建议页"已吃 / 跳过"一键确认：写入 meal_checks，并同步当天睡眠记录。"""

    def _write() -> None:
        with write_session() as session:
            row = (
                session.execute(
                    select(MealCheck).where(
                        MealCheck.user_id == user_id,
                        MealCheck.check_date == check_date,
                        MealCheck.meal == meal,
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    MealCheck(
                        user_id=user_id, check_date=check_date, meal=meal, eaten=eaten
                    )
                )
            else:
                row.eaten = eaten
                row.updated_at = utcnow()
            record = (
                session.execute(
                    select(SleepRecord).where(
                        SleepRecord.user_id == user_id,
                        SleepRecord.record_date == check_date,
                    )
                )
                .scalars()
                .first()
            )
            if record is not None:
                setattr(record, f"meal_{meal}", eaten)
                record.updated_at = utcnow()
            session.flush()

    run_with_retry(_write, label="meal_checkin")


# --------------------------------------------------------------------------- #
# 偏离与达标
# --------------------------------------------------------------------------- #
def _planned_days(payload: dict) -> dict[str, dict]:
    return {day["date"]: day for day in (payload.get("plan") or [])}


def deviation(payload: dict, records: list[SleepRecord], today: date) -> dict:
    """最近一次记录与计划的偏离程度：≤30 分钟不动，30~90 静默重算，>90 询问。"""
    planned = _planned_days(payload)
    today_iso = today.isoformat()
    latest = None
    for record in sorted(records, key=lambda r: r.record_date, reverse=True):
        if record.record_date in planned and record.record_date <= today_iso:
            latest = record
            break

    planned_days = [day for day in (payload.get("plan") or []) if day["date"] <= today_iso]
    recorded = {
        record.record_date
        for record in records
        if record.record_date in planned and record.record_date <= today_iso
    }
    off_days = 0
    for the_date in sorted(recorded):
        day = planned[the_date]
        record = next(item for item in records if item.record_date == the_date)
        try:
            diff = max(
                abs(circular_diff_minutes(parse_hhmm(day["sleep_time"]), parse_hhmm(record.sleep_time))),
                abs(circular_diff_minutes(parse_hhmm(day["wake_time"]), parse_hhmm(record.wake_time))),
            )
        except Exception:      # noqa: BLE001 - 历史脏数据
            continue
        if diff > advice_service.DEVIATION_OK:
            off_days += 1

    remaining_days = max(0, len(payload.get("plan") or []) - len(planned_days))
    result = {
        "level": "none",
        "minutes": 0,
        "direction": None,
        "off_days": off_days,
        "days_elapsed": len(planned_days),
        "days_recorded": len(recorded),
        "remaining_days": remaining_days,
        "planned": None,
        "actual": None,
        "options": [],
    }
    if latest is None:
        return result

    day = planned[latest.record_date]
    diff_sleep = circular_diff_minutes(parse_hhmm(day["sleep_time"]), parse_hhmm(latest.sleep_time))
    diff_wake = circular_diff_minutes(parse_hhmm(day["wake_time"]), parse_hhmm(latest.wake_time))
    magnitude = max(abs(diff_sleep), abs(diff_wake))
    result.update(
        {
            "minutes": int(magnitude),
            "direction": "later" if (diff_sleep >= 0) else "earlier",
            "planned": {
                "date": latest.record_date,
                "sleep_time": day["sleep_time"],
                "wake_time": day["wake_time"],
            },
            "actual": {
                "date": latest.record_date,
                "sleep_time": latest.sleep_time,
                "wake_time": latest.wake_time,
            },
        }
    )
    if magnitude <= advice_service.DEVIATION_OK:
        result["level"] = "none"
        return result
    if magnitude <= advice_service.DEVIATION_ASK:
        result["level"] = "auto"
        return result

    # 缺多少天补多少天：按"日均不超过提示阈值"换算（120 分钟 → 2 天）
    extra_days = max(
        1,
        min(
            30,
            -(-int(magnitude) // advice_service.SHORT_PLAN_DAILY_THRESHOLD),
        ),
    )
    result["level"] = "ask"
    result["options"] = [
        {
            "strategy": "postpone",
            "recommended": True,
            "extra_days": extra_days,
            "target_date": (today + timedelta(days=remaining_days + extra_days)).isoformat(),
        },
        {"strategy": "recompute", "recommended": False, "extra_days": 0},
        {"strategy": "keep", "recommended": False, "extra_days": 0},
    ]
    return result


def adherence(payload: dict, records: list[SleepRecord], today: date) -> dict:
    """计划与实际对照：只看有记录的日子，未记录不算失败，也不中断连续天数。"""
    today_iso = today.isoformat()
    by_date = {record.record_date: record for record in records}
    planned = [day for day in (payload.get("plan") or []) if day["date"] <= today_iso]

    hit_dates: list[str] = []
    for day in planned:
        record = by_date.get(day["date"])
        if record is None:
            continue
        try:
            diff = max(
                abs(circular_diff_minutes(parse_hhmm(day["sleep_time"]), parse_hhmm(record.sleep_time))),
                abs(circular_diff_minutes(parse_hhmm(day["wake_time"]), parse_hhmm(record.wake_time))),
            )
        except Exception:      # noqa: BLE001
            continue
        if diff <= advice_service.HIT_TOLERANCE:
            hit_dates.append(day["date"])

    recorded_dates = sorted(
        day["date"] for day in planned if day["date"] in by_date
    )
    streak = 0
    for the_date in reversed(recorded_dates):
        if the_date in hit_dates:
            streak += 1
        else:
            break

    rate = round(len(hit_dates) / len(recorded_dates), 3) if recorded_dates else None
    return {
        "days_elapsed": len(planned),
        "days_recorded": len(recorded_dates),
        "hit_days": len(hit_dates),
        "hit_rate": rate,
        "current_streak": streak,
        "remaining_days": max(0, len(payload.get("plan") or []) - len(planned)),
        "tolerance_minutes": advice_service.HIT_TOLERANCE,
    }


# --------------------------------------------------------------------------- #
# 重算
# --------------------------------------------------------------------------- #
def _meals_enabled_from(payload: dict) -> dict[str, bool]:
    plan = payload.get("plan") or []
    if not plan:
        return {}
    return {
        key: bool(value.get("enabled", True))
        for key, value in (plan[0].get("meals") or {}).items()
    }


def replan(db, user: User, *, strategy: str, today: date | None = None) -> dict:
    """按弹窗选择的策略重算：postpone（顺延目标日期）/ recompute（保持日期）/ keep。"""
    today = today or date.today()
    plan = active_plan(db, user.id)
    payload = plan_payload(plan)
    if plan is None or payload is None:
        return {"strategy": strategy, "plan": None, "postponed_days": 0}

    if strategy == "keep":
        return {
            "strategy": "keep",
            "plan": payload,
            "plan_id": plan.id,
            "replans": plan.replans,
            "postponed_days": plan.postponed_days,
        }

    records = advice_service._recent_records(db, user.id)
    dev = deviation(payload, records, today)
    remaining_days = max(1, dev["remaining_days"] or payload.get("days") or 1)
    mode = payload.get("mode") or "slow"
    meals_enabled = _meals_enabled_from(payload)

    extra = 0
    if strategy == "postpone":
        extra = 0
        for option in dev["options"]:
            if option["strategy"] == "postpone":
                extra = int(option["extra_days"])
                break
        if extra <= 0:
            extra = 3

    # build_advice 永远从今天开始排，所以"保持原目标日期"需要 remaining+1 天，
    # 顺延 extra 天则需要 remaining+extra+1 天（新计划最后一天 = today+remaining+extra）。
    extra_days_total = extra
    days = max(1, min(60, remaining_days + extra_days_total + 1))
    fresh = advice_service.build_advice(
        db,
        user,
        mode=mode,
        days=days,
        use_target=payload.get("source") == "custom_target",
        meals_enabled=meals_enabled,
        today=today,
    )
    if strategy == "postpone" and fresh["steps"]["suggested_days"] > days:
        extra_days_total += fresh["steps"]["suggested_days"] - days
        days = max(1, min(90, fresh["steps"]["suggested_days"]))
        fresh = advice_service.build_advice(
            db,
            user,
            mode=mode,
            days=days,
            use_target=payload.get("source") == "custom_target",
            meals_enabled=meals_enabled,
            today=today,
        )

    replans = int(plan.replans) + 1
    postponed = int(plan.postponed_days) + (extra_days_total if strategy == "postpone" else 0)
    plan_id = save_plan(db, user.id, fresh, replans=replans, postponed_days=postponed)
    return {
        "strategy": strategy,
        "plan": fresh,
        "plan_id": plan_id,
        "replans": replans,
        "postponed_days": postponed,
        "deviation": dev,
    }


__all__ = [
    "save_plan",
    "active_plan",
    "plan_payload",
    "ack_notice",
    "checkin",
    "deviation",
    "adherence",
    "replan",
]
