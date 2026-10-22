"""作息建议接口（用户端）。

引擎见 services/advice.py（adaptive-v2）：三餐自适应窗口 + 先快后缓的相位步长 +
偏离重算。计划快照 / 三餐确认 / 达标统计见 services/advice_plan.py。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..schemas import (
    AdviceCheckinRequest,
    AdviceNoticeAckRequest,
    AdviceReplanRequest,
    AdviceRequest,
    AdviceResponse,
)
from ..services import advice as advice_service
from ..services import advice_plan as plan_service
from .records import _check_record_date

router = APIRouter(tags=["advice"])


def _meal_plan_state(db, user: User, today: date) -> dict:
    """今天 / 昨天 / 明天早餐的三餐状态 + 最近 7 天计数（前端据此写「为什么提前」）。"""
    history = advice_service.meal_history(db, user.id, today=today)
    snapshot = advice_service.meal_plan_snapshot(history, today=today)
    return {
        **snapshot,
        "days": history["days"],
        "summary": history["summary"],
        "history": history["summary"],
        "recent": history["days"],
    }


def _plan_summary(plan) -> dict | None:
    if plan is None:
        return None
    return {
        "plan_id": plan.id,
        "mode": plan.mode,
        "days": plan.days,
        "start_date": plan.start_date,
        "target_date": plan.target_date,
        "target_sleep_time": plan.target_sleep_time,
        "target_wake_time": plan.target_wake_time,
        "replans": plan.replans,
        "postponed_days": plan.postponed_days,
        "engine": plan.engine,
    }


def _state(db, user: User, *, today: date, with_auto: bool = True) -> dict:
    """当前计划状态：快照 + 今日卡片 + 偏离 + 达标统计（必要时静默重算）。"""
    plan = plan_service.active_plan(db, user.id)
    payload = plan_service.plan_payload(plan)
    if payload is None:
        return {
            "has_plan": False,
            "plan": None,
            "today": None,
            "timeline": None,
            "deviation": None,
            "adherence": None,
            "meal_plan": _meal_plan_state(db, user, today),
            "plan_meta": None,
            "auto_recomputed": False,
            "acked_notices": sorted(advice_service._acked_notices(user)),
            "engine": advice_service.ENGINE,
        }

    records = advice_service._recent_records(db, user.id)
    dev = plan_service.deviation(payload, records, today)
    auto_recomputed = False
    # 30~90 分钟偏离：静默重算（同一天只自动重算一次，避免每次刷新都重写快照）
    if with_auto and dev["level"] == "auto" and plan.start_date != today.isoformat():
        result = plan_service.replan(db, user, strategy="recompute", today=today)
        if result.get("plan"):
            payload = result["plan"]
            plan = plan_service.active_plan(db, user.id)
            auto_recomputed = True
            dev = plan_service.deviation(payload, records, today)

    # 三餐记录是「今天的实时事实」：读取时按它重算今天与明天两行（不改写快照）
    history = advice_service.meal_history(db, user.id, today=today)
    payload = advice_service.apply_meal_overlay(payload, history, today=today)
    meal_plan = _meal_plan_state(db, user, today)
    day = advice_service.day_for(payload.get("plan") or [], today)
    return {
        "has_plan": True,
        "plan": payload,
        "today": day,
        "timeline": (day or {}).get("timeline"),
        "deviation": dev,
        "adherence": plan_service.adherence(payload, records, today),
        "meal_plan": meal_plan,
        "plan_meta": {
            **(payload.get("plan_meta") or {}),
            "saved": True,
            "plan_id": plan.id if plan is not None else None,
            "replans": plan.replans if plan is not None else 0,
            "postponed_days": plan.postponed_days if plan is not None else 0,
        },
        "auto_recomputed": auto_recomputed,
        "acked_notices": sorted(advice_service._acked_notices(user)),
        "engine": payload.get("engine") or advice_service.ENGINE,
    }


@router.post("/advice", response_model=AdviceResponse)
def generate_advice(
    payload: AdviceRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """生成未来 N 天作息调整建议（fast=1 天快速调整，slow=多天缓慢调整）。

    自定义目标作息优先级高于系统推荐；`save=True` 时把这次结果存为"当前计划"，
    用户下次打开建议页看到的是同一份计划（跨设备一致）。
    """
    meals_enabled = {
        "breakfast": payload.meal_breakfast,
        "lunch": payload.meal_lunch,
        "dinner": payload.meal_dinner,
    }
    today = date.today()
    result = advice_service.build_advice(
        db,
        user,
        mode=payload.mode,
        days=payload.days,
        use_target=payload.use_target,
        meals_enabled=meals_enabled,
        today=today,
    )
    if payload.save:
        plan_id = plan_service.save_plan(db, user.id, result)
        result["plan_meta"]["saved"] = True
        result["plan_meta"]["plan_id"] = plan_id
        db.expire_all()
    return result


@router.get("/advice/plan")
def current_plan(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """读取当前计划快照 + 今日目标 + 偏离与达标情况。"""
    db.expire_all()
    return _state(db, user, today=date.today())


@router.post("/advice/checkin")
def advice_checkin(
    payload: AdviceCheckinRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """建议页三餐卡片的"已吃 / 跳过 / 不想吃"一键确认（写回当天记录）。"""
    today = date.today()
    the_date = _check_record_date(payload.date) if payload.date else today.isoformat()
    # "不想吃"只对早餐有意义（前端也只在早餐卡片给这个按钮），这里再兜一层：
    # 其它两餐即使传了 True 也按普通的"没吃"处理，免得悄悄改变午餐/晚餐的语义
    refused = bool(payload.refused) and payload.meal == "breakfast"
    plan_service.checkin(
        user.id,
        check_date=the_date,
        meal=payload.meal,
        eaten=payload.eaten,
        refused=refused,
    )
    db.expire_all()
    # 打卡之后"今天"这一天可能整体变了（不想吃会把午餐与晚餐一起提前），
    # 所以顺手把重算过的今天与时间图一起回给前端：前端只重画，不用再拉一次整页
    state = _state(db, user, today=today, with_auto=False)
    return {
        "ok": True,
        "date": the_date,
        "meal": payload.meal,
        "eaten": payload.eaten,
        "refused": refused,
        "meal_plan": state["meal_plan"],
        "today": state["today"],
        "timeline": state["timeline"],
    }


@router.post("/advice/notice-ack")
def advice_notice_ack(
    payload: AdviceNoticeAckRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """「我已知晓，不再提示」：账号级记忆，跨设备生效。"""
    acked = plan_service.ack_notice(user.id, payload.notice)
    db.expire_all()
    return {"ok": True, "acked_notices": acked}


@router.post("/advice/replan")
def advice_replan(
    payload: AdviceReplanRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """偏离过大时的三种处理：顺延目标日期 / 保持日期重算步长 / 继续原计划。"""
    today = date.today()
    result = plan_service.replan(db, user, strategy=payload.strategy, today=today)
    db.expire_all()
    state = _state(db, user, today=today, with_auto=False)
    state["strategy"] = result.get("strategy")
    state["saved"] = payload.strategy != "keep"
    return state


@router.get("/advice/options")
def advice_options(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """前端渲染选项用的元信息（不需要生成完整建议）。"""
    today = date.today()
    db.expire_all()
    return {
        "modes": [
            {"value": "fast", "label": "advice.mode.fast", "days": 1},
            {"value": "slow", "label": "advice.mode.slow", "days": 7},
        ],
        "default_days": advice_service.DEFAULT_SLOW_DAYS,
        "max_days": 30,
        "has_custom_target": bool(user.target_sleep_time and user.target_wake_time),
        "target_sleep_time": user.target_sleep_time,
        "target_wake_time": user.target_wake_time,
        "meal_flags": {
            "breakfast": bool(user.meal_breakfast),
            "lunch": bool(user.meal_lunch),
            "dinner": bool(user.meal_dinner),
        },
        "steps": {
            # 前移/后移共用同一阈值：只用于提示，不再截断生成的曲线
            "cap_minutes": advice_service.STEP_CAP_ADVANCE,
            "hard_cap_minutes": advice_service.STEP_CAP_HARD,
            # 曲线由「总偏移 + 天数」现场生成，不再是固定分段：只暴露形状参数
            "shape": {
                "alpha": advice_service.STEP_SHAPE_ALPHA,
                "power": advice_service.STEP_SHAPE_POWER,
            },
            "short_plan_daily_threshold": advice_service.SHORT_PLAN_DAILY_THRESHOLD,
        },
        "deviation": {
            "ok_minutes": advice_service.DEVIATION_OK,
            "ask_minutes": advice_service.DEVIATION_ASK,
            "tolerance_minutes": advice_service.HIT_TOLERANCE,
        },
        "meal_state": _meal_plan_state(db, user, today),
        "active_plan": _plan_summary(plan_service.active_plan(db, user.id)),
        "acked_notices": sorted(advice_service._acked_notices(user)),
        "today": today.isoformat(),
        "engine": advice_service.ENGINE,
    }
