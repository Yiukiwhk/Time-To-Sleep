"""作息建议服务（adaptive-v2）。

算法设计依据（4 篇参考文献见网站底部"参考文献"弹窗）：
  * 相位前移速度：Eastman 2005 的 1 小时/天方案实测 DLMO 平均前移约 30 分钟/天，
    2 小时/天方案只多到 36 分钟/天，且第 3 天起出现"入睡前相位错位"、入睡潜伏期
    延长、睡眠效率下降；综述统计 7 项实验室研究的估计值全部小于 2 小时/天。
    因此本实现前移每天默认不超过 45 分钟、后移不超过 90 分钟（跨时区数据表明
    后移比前移容易），单日硬上限 120 分钟。
  * 三餐窗口按"当天的起床/入睡"自适应平移，而不是固定钟点；不吃早餐时午餐
    分级前移（第 1 天 60 → 第 2 天 90 → 第 3 天起 120 分钟，不累积、有上限）。
  * 现有文献不支持"进餐时间能移动昼夜相位"（没有 DLMO/CBTmin 终点），所以三餐
    只用于规律进食与避免过晚进食，不当作相位杠杆宣传。
  * 步长"先快后缓"：前段上限 60 分钟，中段 45，后段 30，最后几天 15；实际步长
    = min(该段上限, 剩余偏移 ÷ 剩余天数)，最后一天精确落到目标；若所需日均超过
    60 分钟则给出"计划天数偏短"的温和提示（可"不再提示"）。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from ..core.timing import (
    circular_diff_minutes,
    circular_mean_minutes,
    format_hhmm,
    parse_hhmm,
    sleep_duration_minutes,
)
from ..models import MealCheck, SleepRecord, User, utcnow

DAY = 24 * 60
ENGINE = "adaptive-v2"

# --------------------------------------------------------------------------- #
# 三餐：相对起床/入睡的窗口（分钟）
# --------------------------------------------------------------------------- #
BREAKFAST_AFTER_WAKE = (15, 30)          # 起床后 15~30 分钟，最佳取窗口起点
LUNCH_AFTER_WAKE = (3 * 60, 5 * 60)      # 起床后 3~5 小时
DINNER_BEFORE_SLEEP = (2 * 60, 210)      # 睡前 2~3.5 小时，最佳 3.5 小时
MEAL_BAR_MINUTES = {"breakfast": 30, "lunch": 60, "dinner": 60}   # 时间图条长（仅视觉）
LUNCH_ADVANCE_LADDER = (60, 90, 120)     # 连续未吃早餐第 1/2/3+ 天的前移量
LUNCH_FLOOR_AFTER_WAKE = 120             # 午餐不早于起床后 2 小时
MEAL_KEYS = ("breakfast", "lunch", "dinner")

# --------------------------------------------------------------------------- #
# 相位步长
# --------------------------------------------------------------------------- #
STEP_CAP_ADVANCE = 45        # 前移每天上限（前移比后移难）
STEP_CAP_DELAY = 90          # 后移每天上限
STEP_CAP_HARD = 120          # 单日硬上限（用户设定 15 分钟 ~ 2 小时）
STEP_MIN = 15                # 单日最小有效步长，小于它的偏移集中在更少的天数里走完
STEP_CURVE = ((1 / 3, 60), (2 / 3, 45), (0.9, 30), (1.0, 15))     # 先快后缓
SHORT_PLAN_DAILY_THRESHOLD = 60      # 所需日均超过它就提示"天数偏短"

# --------------------------------------------------------------------------- #
# 偏离与达标
# --------------------------------------------------------------------------- #
DEVIATION_OK = 30            # ≤30 分钟：不处理
DEVIATION_ASK = 90           # >90 分钟：弹窗询问（30~90 分钟静默重算）
HIT_TOLERANCE = 30           # 计划与实际相差 ≤30 分钟算"当天达成"
MEAL_HISTORY_DAYS = 7

# 系统推荐目标：把作息往健康区间收敛
IDEAL_SLEEP = 23 * 60          # 23:00
IDEAL_WAKE = 7 * 60            # 07:00
IDEAL_DURATION = 8 * 60        # 8 小时
DEFAULT_SLOW_DAYS = 7
DEFAULT_HISTORY_DAYS = 14


# --------------------------------------------------------------------------- #
# 读取：记录、三餐执行情况
# --------------------------------------------------------------------------- #
def _recent_records(db, user_id: int, limit_days: int = DEFAULT_HISTORY_DAYS) -> list[SleepRecord]:
    statement = (
        select(SleepRecord)
        .where(SleepRecord.user_id == user_id)
        .order_by(SleepRecord.record_date.desc(), SleepRecord.id.desc())
        .limit(limit_days)
    )
    return list(db.execute(statement).scalars().all())


def _meal_state(meal: str, day: str, record: SleepRecord | None, checks: dict) -> dict:
    """单日单餐的执行情况：优先取"较新的一方"（一键确认 vs 记录页勾选）。"""
    check = checks.get((day, meal))
    if check is not None and record is not None:
        use_check = (check.updated_at or utcnow()) >= (record.updated_at or utcnow())
    else:
        use_check = check is not None
    if use_check:
        return {"state": "eaten" if check.eaten else "skipped", "source": "checkin"}
    if record is not None:
        value = bool(getattr(record, f"meal_{meal}", True))
        return {"state": "eaten" if value else "skipped", "source": "record"}
    return {"state": "unknown", "source": None}


def meal_history(
    db, user_id: int, *, days: int = MEAL_HISTORY_DAYS, today: date | None = None
) -> dict:
    """最近若干天的三餐执行情况（含来源），用于"未吃早餐 → 午餐前移"与达标统计。"""
    today = today or date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    records = {
        row.record_date: row
        for row in db.execute(
            select(SleepRecord).where(
                SleepRecord.user_id == user_id, SleepRecord.record_date >= start
            )
        )
        .scalars()
        .all()
    }
    checks = {
        (row.check_date, row.meal): row
        for row in db.execute(
            select(MealCheck).where(
                MealCheck.user_id == user_id, MealCheck.check_date >= start
            )
        )
        .scalars()
        .all()
    }

    items: list[dict] = []
    for offset in range(days - 1, -1, -1):
        the_date = (today - timedelta(days=offset)).isoformat()
        record = records.get(the_date)
        items.append(
            {
                "date": the_date,
                "meals": {
                    meal: _meal_state(meal, the_date, record, checks) for meal in MEAL_KEYS
                },
            }
        )

    summary = {
        meal: {
            state: sum(1 for item in items if item["meals"][meal]["state"] == state)
            for state in ("eaten", "skipped", "unknown")
        }
        for meal in MEAL_KEYS
    }
    return {"days": items, "summary": summary}


def breakfast_skipped_streak(history: dict, *, today: date | None = None) -> int:
    """连续未吃早餐的天数。

    今天单独处理：今天的早餐通常还没到/还没确认，而记录页的勾选默认是"已吃"，
    所以今天只有被明确标成"跳过"才计入，否则一律不打断（避免上午就误判）。
    """
    today_iso = (today or date.today()).isoformat()
    streak = 0
    for index, item in enumerate(sorted(history["days"], key=lambda d: d["date"], reverse=True)):
        state = item["meals"]["breakfast"]["state"]
        if index == 0 and item["date"] == today_iso:
            if state == "skipped":
                streak += 1
            continue
        if state == "skipped":
            streak += 1
            continue
        break
    return streak


def lunch_shift_minutes(streak: int) -> int:
    """连续未吃早餐对应的午餐前移量（分级、不累积、有上限）。"""
    if streak <= 0:
        return 0
    return LUNCH_ADVANCE_LADDER[min(streak, len(LUNCH_ADVANCE_LADDER)) - 1]


# --------------------------------------------------------------------------- #
# 基准与目标
# --------------------------------------------------------------------------- #
def _basis(records: list[SleepRecord]) -> dict:
    """基准作息：最近记录的加权圆环均值（越新权重越高）。"""
    if not records:
        return {
            "sample_size": 0,
            "sleep_minutes": IDEAL_SLEEP,
            "wake_minutes": IDEAL_WAKE,
            "duration_minutes": IDEAL_DURATION,
            "avg_latest_wake_minutes": None,
            "avg_earliest_sleep_minutes": None,
            "latest_record_date": None,
        }

    ordered = sorted(records, key=lambda r: r.record_date)
    weights = [1.0 + index * 0.1 for index in range(len(ordered))]
    sleep_values = [parse_hhmm(r.sleep_time) for r in ordered]
    wake_values = [parse_hhmm(r.wake_time) for r in ordered]

    sleep_mean = circular_mean_minutes(sleep_values, weights)
    wake_mean = circular_mean_minutes(wake_values, weights)
    latest_wake = [parse_hhmm(r.latest_wake_time) for r in ordered if r.latest_wake_time]
    earliest_sleep = [parse_hhmm(r.earliest_sleep_time) for r in ordered if r.earliest_sleep_time]

    durations = [sleep_duration_minutes(s, w) for s, w in zip(sleep_values, wake_values)]
    avg_duration = int(round(sum(durations) / len(durations)))

    return {
        "sample_size": len(ordered),
        "sleep_minutes": sleep_mean,
        "wake_minutes": wake_mean,
        "duration_minutes": avg_duration,
        "avg_latest_wake_minutes": circular_mean_minutes(latest_wake) if latest_wake else None,
        "avg_earliest_sleep_minutes": circular_mean_minutes(earliest_sleep) if earliest_sleep else None,
        "latest_record_date": ordered[-1].record_date,
        "duration_minutes_latest": durations[-1],
    }


def _system_target(basis: dict, records: list[SleepRecord]) -> dict:
    """系统推荐目标：向健康作息收敛，但尊重用户设置的最晚起床/最早入睡约束。"""
    sleep_target = basis["sleep_minutes"]
    wake_target = basis["wake_minutes"]

    duration = sleep_duration_minutes(sleep_target, wake_target)
    if duration < IDEAL_DURATION:
        sleep_target -= min(IDEAL_DURATION - duration, 90)
    elif duration > IDEAL_DURATION + 60:
        sleep_target += min(duration - IDEAL_DURATION, 60)

    wake_shift = circular_diff_minutes(wake_target, IDEAL_WAKE)
    wake_target += max(-45, min(45, wake_shift))

    latest_wake_values = [parse_hhmm(r.latest_wake_time) for r in records if r.latest_wake_time]
    if latest_wake_values:
        limit = circular_mean_minutes(latest_wake_values)
        if circular_diff_minutes(wake_target, limit) > 0:
            wake_target = limit
        sleep_target = min(sleep_target, wake_target - IDEAL_DURATION + 60)
    earliest_sleep_values = [parse_hhmm(r.earliest_sleep_time) for r in records if r.earliest_sleep_time]
    if earliest_sleep_values:
        floor = circular_mean_minutes(earliest_sleep_values)
        if circular_diff_minutes(floor, sleep_target) > 0:
            sleep_target = floor
    if sleep_duration_minutes(sleep_target, wake_target) < 6 * 60:
        wake_target = sleep_target + 6 * 60

    return {
        "sleep_minutes": sleep_target % DAY,
        "wake_minutes": wake_target % DAY,
        "duration_minutes": sleep_duration_minutes(sleep_target, wake_target),
    }


# --------------------------------------------------------------------------- #
# 步长：先快后缓 + 前后移不对称
# --------------------------------------------------------------------------- #
def _step_caps(days: int, direction: int) -> list[int]:
    """每天的步长上限：前段大、后段小（direction<0 前移，>0 后移）。"""
    ceiling = STEP_CAP_ADVANCE if direction < 0 else STEP_CAP_DELAY
    caps: list[int] = []
    for index in range(max(1, days)):
        progress = (index + 1) / max(1, days)
        cap = STEP_CURVE[-1][1]
        for boundary, value in STEP_CURVE:
            if progress <= boundary + 1e-9:
                cap = value
                break
        caps.append(max(STEP_MIN, min(cap, ceiling, STEP_CAP_HARD)))
    return caps


def _step_magnitudes(span: int, days: int, direction: int) -> tuple[list[int], bool]:
    """把总偏移量 span 分摊到 days 天：先快后缓、每天不超过当天上限、总和精确等于 span。

    曲线用 min(当天上限, λ·递减权重) 表示：λ 越大整体越大，用二分求到总和为 span，
    这样既保持"前期步长大、后期步长小"，又不会顶穿任何一天的上限。
    """
    span = abs(int(span))
    days = max(1, int(days))
    caps = _step_caps(days, direction)
    if span == 0:
        return [0] * days, True
    if days == 1:
        return [span], span <= caps[0]

    feasible = span <= sum(caps)
    weights = [days - index for index in range(days)]      # 递减权重：先快后缓

    if feasible:
        low, high = 0.0, float(max(caps))
        for _ in range(60):
            mid = (low + high) / 2
            total = sum(min(float(caps[i]), mid * weights[i]) for i in range(days))
            if total < span:
                low = mid
            else:
                high = mid
        raw = [min(float(caps[i]), high * weights[i]) for i in range(days)]
    else:                                                   # 不可行：先顶满上限，差额最后补
        raw = [float(cap) for cap in caps]

    steps: list[int] = []
    running = 0.0
    placed = 0
    for value in raw:                                       # 累计取整，尽量贴近原比例
        running += value
        cumulative = int(round(running))
        steps.append(max(0, cumulative - placed))
        placed = cumulative

    for index in range(days - 1, 0, -1):                    # 取整后压平：保证单调不增
        if steps[index] <= steps[index - 1]:
            continue
        excess = steps[index] - steps[index - 1]
        steps[index] = steps[index - 1]
        for back in range(index - 1, -1, -1):
            room = caps[back] - steps[back]
            take = min(room, excess)
            if take > 0:
                steps[back] += take
                excess -= take
            if excess <= 0:
                break
        if excess > 0:
            steps[index] += excess

    delta = span - sum(steps)                               # 零头补给最早还有余量的天
    index = 0
    while delta > 0 and index < days:
        take = min(caps[index] - steps[index], delta)
        steps[index] += take
        delta -= take
        index += 1
    if delta > 0:                                           # 天数不足时把走不完的部分留到最后一天
        steps[-1] += delta
    index = days - 1
    while delta < 0 and index >= 0:                         # 走多了从最后一天往前扣
        take = min(steps[index], -delta)
        steps[index] -= take
        delta += take
        index -= 1
    return steps, feasible


def _cumulative_magnitudes(span: int, days: int, direction: int) -> tuple[list[int], bool]:
    """返回每天的累计移动量（正值）与是否在天数内可行。"""
    steps, feasible = _step_magnitudes(span, days, direction)
    cumulative: list[int] = []
    moved = 0
    for step in steps:
        moved += step
        cumulative.append(moved)
    return cumulative, feasible


def suggested_days(span: int, direction: int, *, limit: int = 90) -> int:
    """在每天上限的约束下走完 span 至少需要多少天（供"建议增加天数"提示）。"""
    span = abs(int(span))
    if span == 0:
        return 1
    for days in range(1, limit + 1):
        if span <= sum(_step_caps(days, direction)):
            return days
    return limit


def _plan_steps(total_sleep: int, total_wake: int, days: int, mode: str) -> dict:
    """计算每天的累计偏移（入睡/起床分别按同一进度比例推进）。"""
    days = max(1, days)
    total_sleep = int(total_sleep)
    total_wake = int(total_wake)
    dominant = total_sleep if abs(total_sleep) >= abs(total_wake) else total_wake
    direction = -1 if dominant < 0 else 1

    if mode == "fast" or days == 1:
        return {
            "cumulative": [(total_sleep, total_wake)],
            "feasible": abs(dominant) <= STEP_CAP_HARD,
            "suggested_days": suggested_days(dominant, direction),
            "direction": direction,
            "caps": [STEP_CAP_HARD],
            "daily_average": abs(dominant),
            "compact_days": 1,
        }

    # 偏移很小（日均不足 15 分钟）时集中在更少的天数里走完，其余天保持目标
    compact_days = min(days, max(1, -(-abs(dominant) // STEP_MIN))) if dominant else 1
    shape_days = max(1, compact_days)
    magnitudes, feasible = _cumulative_magnitudes(dominant, shape_days, direction)
    magnitudes = magnitudes + [magnitudes[-1]] * (days - shape_days)

    cumulative: list[tuple[int, int]] = []
    for index, magnitude in enumerate(magnitudes):
        if dominant == 0:
            cumulative.append((0, 0))
            continue
        ratio = magnitude / abs(dominant)
        cumulative.append((int(round(total_sleep * ratio)), int(round(total_wake * ratio))))
    if cumulative:      # 最后一天精确落在目标上
        cumulative[-1] = (total_sleep, total_wake)

    daily = [abs(cumulative[0][0]) if cumulative else 0]
    for index in range(1, len(cumulative)):
        daily.append(abs(cumulative[index][0] - cumulative[index - 1][0]))

    return {
        "cumulative": cumulative,
        "feasible": bool(feasible),
        "suggested_days": suggested_days(dominant, direction),
        "direction": direction,
        "caps": _step_caps(shape_days, direction),
        "daily_average": int(round(abs(dominant) / days)),
        "compact_days": compact_days,
    }


# --------------------------------------------------------------------------- #
# 三餐与时间图
# --------------------------------------------------------------------------- #
def _meal_times(
    sleep_minutes: int,
    wake_minutes: int,
    enabled: dict[str, bool],
    *,
    lunch_shift: int = 0,
) -> dict[str, dict]:
    """三餐推荐时间：窗口按当天的起床/入睡自适应，最佳时间取窗口起点。"""
    wake = wake_minutes % DAY
    sleep = sleep_minutes % DAY

    breakfast_best = wake + BREAKFAST_AFTER_WAKE[0]
    breakfast_end = wake + BREAKFAST_AFTER_WAKE[1]

    lunch_base = wake + LUNCH_AFTER_WAKE[0]
    lunch_shift = max(0, min(int(lunch_shift), LUNCH_ADVANCE_LADDER[-1]))
    lunch_best = max(wake + LUNCH_FLOOR_AFTER_WAKE, lunch_base - lunch_shift)
    lunch_end = lunch_best + (LUNCH_AFTER_WAKE[1] - LUNCH_AFTER_WAKE[0])

    dinner_best = sleep - DINNER_BEFORE_SLEEP[1]
    dinner_end = sleep - DINNER_BEFORE_SLEEP[0]

    plan = {
        "breakfast": {
            "best": breakfast_best,
            "end": breakfast_end,
            "anchor": "wake",
            "shifted_minutes": 0,
        },
        "lunch": {
            "best": lunch_best,
            "end": lunch_end,
            "anchor": "wake",
            "shifted_minutes": lunch_shift,
        },
        "dinner": {
            "best": dinner_best,
            "end": dinner_end,
            "anchor": "sleep",
            "shifted_minutes": 0,
        },
    }

    meals: dict[str, dict] = {}
    for key, item in plan.items():
        meals[key] = {
            "enabled": bool(enabled.get(key, True)),
            "time": format_hhmm(item["best"]),                 # 兼容旧字段：推荐时间
            "best_time": format_hhmm(item["best"]),            # 最佳建议用餐时间（窗口起点）
            "window_start": format_hhmm(item["best"]),
            "window_end": format_hhmm(item["end"]),
            "bar_minutes": MEAL_BAR_MINUTES[key],
            "anchor": item["anchor"],
            "shifted_minutes": item["shifted_minutes"],
            # note 只能放"不需要参数"的文案键：带 {占位符} 的句子（例如午餐提前）
            # 一律由 meal_plan 里的数字字段在前端用 tp() 拼，否则页面上会直接显示
            # "{streak}/{shift}"。午餐那句已经在今日提示与方案行徽标里出现过，这里不再重复。
            "note": None,
        }
    return meals


def _unwrapped_day(sleep_minutes: int, wake_minutes: int) -> tuple[int, int, int]:
    """把"一天"放到一条不折返的时间轴上：起床当天的分钟数 + 睡眠锚点。"""
    wake = wake_minutes % DAY
    sleep = sleep_minutes % DAY
    if sleep <= wake:            # 入睡晚于零点（例如 01:30 睡、08:30 起）
        sleep += DAY
    return wake, sleep, sleep - wake


def _timeline(sleep_minutes: int, wake_minutes: int, meals: dict[str, dict]) -> dict:
    """时间图数据：轴窗 = 起床前 2 小时 → 入睡后 2 小时（跨零点自然环绕）。"""
    wake, sleep, _ = _unwrapped_day(sleep_minutes, wake_minutes)
    start = wake - 2 * 60
    end = sleep + 2 * 60
    span = end - start
    if span <= 0:
        span += DAY

    items: list[dict] = []
    if wake - start > 0:
        items.append(
            {
                "kind": "sleep",
                "segment": "morning",
                "start": 0,
                "end": wake - start,
                "start_time": format_hhmm(start),
                "end_time": format_hhmm(wake),
                "partial": True,
            }
        )
    for key in MEAL_KEYS:
        meal = meals.get(key)
        if not meal or not meal.get("enabled"):
            continue
        best_rel = parse_hhmm(meal["best_time"]) - start
        if best_rel < 0:
            best_rel += DAY
        items.append(
            {
                "kind": "meal",
                "meal": key,
                "start": best_rel,
                "end": best_rel + int(meal.get("bar_minutes") or 0),
                "start_time": meal["best_time"],
                "end_time": meal["window_end"],
                "partial": False,
                "shifted_minutes": int(meal.get("shifted_minutes") or 0),
            }
        )
    items.append(
        {
            "kind": "sleep",
            "segment": "night",
            "start": sleep - start,
            "end": span,
            "start_time": format_hhmm(sleep),
            "end_time": format_hhmm(start + span),
            "partial": True,
        }
    )
    items.sort(key=lambda item: (item["start"], 0 if item["kind"] == "sleep" else 1))

    return {
        "axis_start": format_hhmm(start),
        "axis_end": format_hhmm(start + span),
        "span_minutes": span,
        "sleep_label": f"{format_hhmm(sleep)}–{format_hhmm(wake)}",
        "sleep_minutes": sleep - wake,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# 每日提示
# --------------------------------------------------------------------------- #
def _tips(
    index: int,
    days: int,
    mode: str,
    sleep_minutes: int,
    duration: int,
    wake_conflict: bool,
    *,
    direction: int,
    step_minutes: int,
) -> list[str]:
    """提示文案使用 i18n 键，前端按当前语言渲染。"""
    tips: list[str] = []
    if mode == "slow" and index == 0 and days > 1:
        tips.append("advice.tip.slow_start")
    if index == days - 1:
        tips.append("advice.tip.target_reached")
    if duration < 6 * 60:
        tips.append("advice.tip.too_short")
    elif duration > 10 * 60:
        tips.append("advice.tip.too_long")
    if wake_conflict:
        tips.append("advice.tip.wake_conflict")
    if sleep_minutes >= DAY - 60 or sleep_minutes <= 60:
        tips.append("advice.tip.midnight")
    if step_minutes > 0:
        if direction < 0:
            tips.append("advice.tip.morning_light")
            tips.append("advice.tip.evening_dim")
        else:
            tips.append("advice.tip.evening_light")
    if index == 0:
        tips.append("advice.tip.caffeine")
        tips.append("advice.tip.nap_limit")
        tips.append("advice.tip.no_late_alcohol")
    if not tips:
        tips.append("advice.tip.steady")
    return tips


def _notices(
    *,
    records: list[SleepRecord],
    use_target: bool,
    custom_target: bool,
    mode: str,
    days: int,
    daily_average: int,
    feasible: bool,
    suggested: int,
    acked: set[str],
    lunch_shift: int,
    skipped_streak: int,
) -> list[dict]:
    """结构化提示：severity 决定样式，ackable 决定是否显示"不再提示"。"""
    notices: list[dict] = []

    def add(code: str, *, severity: str = "info", ackable: bool = False, **params) -> None:
        notices.append(
            {
                "code": code,
                "severity": severity,
                "ackable": ackable,
                "acked": code in acked,
                "params": params,
            }
        )

    if not records:
        add("advice.warn.no_history", severity="warn")
    if use_target and not custom_target:
        add("advice.warn.no_custom_target")
    if mode == "slow" and days == 1:
        add("advice.warn.slow_single_day")
    if daily_average > SHORT_PLAN_DAILY_THRESHOLD:
        add(
            "advice.notice.short_plan",
            severity="warn",
            ackable=True,
            daily_average=daily_average,
            days=days,
            suggested_days=suggested,
        )
    if not feasible:
        add(
            "advice.notice.infeasible",
            severity="danger",
            ackable=True,
            days=days,
            suggested_days=suggested,
        )
    if skipped_streak > 0 and lunch_shift > 0:
        add(
            "advice.notice.lunch_advanced",
            streak=skipped_streak,
            shift=lunch_shift,
        )
    # warnings 只保留传统的 advice.warn.* 代码：结构化提示（notices）已经带
    # severity / ackable，前端按 notices 渲染即可，避免同一条提示出现两次。
    warnings = [item["code"] for item in notices if item["code"].startswith("advice.warn.")]
    return notices, warnings


def _notes(*, source: str, modes_enabled: bool) -> list[str]:
    notes = [
        "advice.note.first_three_days",
        "advice.note.meal_window",
        "advice.note.meal_not_phase_lever",
        "advice.note.evidence",
    ]
    if source == "custom_target":
        notes.append("advice.note.custom_target_priority")
    if not modes_enabled:
        notes.append("advice.note.meals_disabled")
    return notes


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def build_advice(
    db,
    user: User,
    *,
    mode: str = "fast",
    days: int | None = None,
    use_target: bool = True,
    meals_enabled: dict[str, bool] | None = None,
    today: date | None = None,
) -> dict:
    """生成未来 N 天作息调整建议（含三餐窗口、时间图、步长说明与提示）。"""
    today = today or date.today()
    records = _recent_records(db, user.id)
    basis = _basis(records)

    custom_target = bool(user.target_sleep_time and user.target_wake_time)
    source = "system"
    if use_target and custom_target:
        target = {
            "sleep_minutes": parse_hhmm(user.target_sleep_time),
            "wake_minutes": parse_hhmm(user.target_wake_time),
            "duration_minutes": sleep_duration_minutes(
                parse_hhmm(user.target_sleep_time), parse_hhmm(user.target_wake_time)
            ),
        }
        source = "custom_target"
    else:
        target = _system_target(basis, records)

    mode = "slow" if mode == "slow" else "fast"
    plan_days = 1 if mode == "fast" else max(1, min(30, days or DEFAULT_SLOW_DAYS))

    enabled = {
        "breakfast": bool(user.meal_breakfast),
        "lunch": bool(user.meal_lunch),
        "dinner": bool(user.meal_dinner),
    }
    if meals_enabled:
        for key, value in meals_enabled.items():
            if value is not None and key in enabled:
                enabled[key] = bool(value)

    history = meal_history(db, user.id, today=today)
    streak = breakfast_skipped_streak(history, today=today)
    shift = lunch_shift_minutes(streak)

    steps = _plan_steps(
        circular_diff_minutes(basis["sleep_minutes"], target["sleep_minutes"]),
        circular_diff_minutes(basis["wake_minutes"], target["wake_minutes"]),
        plan_days,
        mode,
    )
    direction = steps["direction"]

    latest_wake_limit = basis.get("avg_latest_wake_minutes")
    plan: list[dict] = []
    for index, (cumulative_sleep, cumulative_wake) in enumerate(steps["cumulative"]):
        sleep_minutes = (basis["sleep_minutes"] + cumulative_sleep) % DAY
        wake_minutes = (basis["wake_minutes"] + cumulative_wake) % DAY
        duration = sleep_duration_minutes(sleep_minutes, wake_minutes)
        wake_conflict = (
            latest_wake_limit is not None
            and circular_diff_minutes(wake_minutes, latest_wake_limit) < 0
        )
        step_minutes = 0
        if index > 0:
            previous_sleep = steps["cumulative"][index - 1][0]
            step_minutes = abs(cumulative_sleep - previous_sleep)
        else:
            step_minutes = abs(cumulative_sleep)
        meals = _meal_times(sleep_minutes, wake_minutes, enabled, lunch_shift=shift)
        plan.append(
            {
                "index": index + 1,
                "date": (today + timedelta(days=index)).isoformat(),
                "sleep_time": format_hhmm(sleep_minutes),
                "wake_time": format_hhmm(wake_minutes),
                "duration_minutes": duration,
                "shift_sleep_minutes": cumulative_sleep,
                "shift_wake_minutes": cumulative_wake,
                "step_minutes": step_minutes,
                "meals": meals,
                "timeline": _timeline(sleep_minutes, wake_minutes, meals),
                "tips": _tips(
                    index,
                    plan_days,
                    mode,
                    sleep_minutes,
                    duration,
                    wake_conflict,
                    direction=direction,
                    step_minutes=step_minutes,
                ),
            }
        )

    acked = _acked_notices(user)
    notices, warnings = _notices(
        records=records,
        use_target=use_target,
        custom_target=custom_target,
        mode=mode,
        days=plan_days,
        daily_average=steps["daily_average"],
        feasible=steps["feasible"],
        suggested=steps["suggested_days"],
        acked=acked,
        lunch_shift=shift,
        skipped_streak=streak,
    )

    today_index = next(
        (index for index, day in enumerate(plan) if day["date"] == today.isoformat()), 0
    )
    today_card = plan[today_index] if plan else None
    target_date = plan[-1]["date"] if plan else today.isoformat()

    return {
        "mode": mode,
        "days": plan_days,
        "source": source,
        "basis": {
            "sample_size": basis["sample_size"],
            "sleep_time": format_hhmm(basis["sleep_minutes"]),
            "wake_time": format_hhmm(basis["wake_minutes"]),
            "duration_minutes": basis["duration_minutes"],
            "latest_wake_time": (
                format_hhmm(basis["avg_latest_wake_minutes"])
                if basis.get("avg_latest_wake_minutes") is not None
                else None
            ),
            "earliest_sleep_time": (
                format_hhmm(basis["avg_earliest_sleep_minutes"])
                if basis.get("avg_earliest_sleep_minutes") is not None
                else None
            ),
            "latest_record_date": basis.get("latest_record_date"),
        },
        "target": {
            "sleep_time": format_hhmm(target["sleep_minutes"]),
            "wake_time": format_hhmm(target["wake_minutes"]),
            "duration_minutes": target["duration_minutes"],
            "custom": source == "custom_target",
        },
        "steps": {
            "direction": "advance" if direction < 0 else "delay",
            "cap_advance_minutes": STEP_CAP_ADVANCE,
            "cap_delay_minutes": STEP_CAP_DELAY,
            "hard_cap_minutes": STEP_CAP_HARD,
            "daily_average_minutes": steps["daily_average"],
            "curve": steps["caps"],
            "curve_minutes": [60, 45, 30, 15],
            "compact_days": steps["compact_days"],
            "feasible": steps["feasible"],
            "suggested_days": steps["suggested_days"],
        },
        "meal_plan": {
            "breakfast_skipped_streak": streak,
            "lunch_shift_minutes": shift,
            "history": history["summary"],
            "recent": history["days"][-MEAL_HISTORY_DAYS:],
        },
        "warnings": warnings,
        "notices": notices,
        "notes": _notes(source=source, modes_enabled=any(enabled.values())),
        "plan": plan,
        "today": today_card,
        "timeline": today_card["timeline"] if today_card else None,
        "plan_meta": {
            "start_date": plan[0]["date"] if plan else today.isoformat(),
            "target_date": target_date,
            "postponed_days": 0,
            "replans": 0,
            "saved": False,
            "plan_id": None,
        },
        "generated_at": utcnow(),
        "engine": ENGINE,
    }


def day_for(plan: list[dict], the_date: date) -> dict | None:
    """从计划里取"今天"这天：精确命中优先，否则取最近的一天，最后兜底第一天。

    计划快照是持久化的，隔天打开时"今天"要重新定位，并把时间图按当天重渲染。
    """
    if not plan:
        return None
    target = the_date.isoformat()
    for day in plan:
        if day.get("date") == target:
            return _render_day(day)
    earlier = [day for day in plan if str(day.get("date")) < target]
    if earlier:
        return _render_day(earlier[-1])
    return _render_day(plan[0])


def _render_day(day: dict) -> dict:
    fresh = dict(day)
    fresh["timeline"] = _timeline(
        parse_hhmm(day["sleep_time"]), parse_hhmm(day["wake_time"]), day.get("meals") or {}
    )
    return fresh


def _acked_notices(user: User) -> set[str]:
    raw = getattr(user, "advice_notice_ack", None) or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


__all__ = [
    "ENGINE",
    "BREAKFAST_AFTER_WAKE",
    "LUNCH_AFTER_WAKE",
    "DINNER_BEFORE_SLEEP",
    "MEAL_BAR_MINUTES",
    "MEAL_HISTORY_DAYS",
    "DEVIATION_OK",
    "DEVIATION_ASK",
    "HIT_TOLERANCE",
    "STEP_CAP_ADVANCE",
    "STEP_CAP_DELAY",
    "STEP_CAP_HARD",
    "STEP_MIN",
    "SHORT_PLAN_DAILY_THRESHOLD",
    "STEP_CURVE",
    "DEFAULT_SLOW_DAYS",
    "build_advice",
    "day_for",
    "meal_history",
    "breakfast_skipped_streak",
    "lunch_shift_minutes",
    "suggested_days",
]
