"""作息建议服务（adaptive-v2）。

算法设计依据（4 篇参考文献见网站底部"参考文献"弹窗）：
  * 相位前移速度：Eastman 2005 的 1 小时/天方案实测 DLMO 平均前移约 30 分钟/天，
    2 小时/天方案只多到 36 分钟/天，且第 3 天起出现"入睡前相位错位"、入睡潜伏期
    延长、睡眠效率下降；综述统计 7 项实验室研究的估计值全部小于 2 小时/天。
    因此本实现前移每天默认不超过 45 分钟、后移不超过 90 分钟（跨时区数据表明
    后移比前移容易），单日硬上限 120 分钟。
  * 三餐窗口按"当天的起床/入睡"自适应平移，而不是固定钟点；三餐记录会带动下一餐
    提前，但**只看昨天与当天**：昨天晚餐没吃 → 今天早餐提前 30；今天早餐没吃 →
    午餐与晚餐各提前 60；今天午餐没用 → 晚餐再提前 30（与早餐那条叠加，最多 90）；
    今天晚餐没吃 → 明天早餐提前 30。不再累积「连续几天没吃早餐」的阶梯。
  * 现有文献不支持"进餐时间能移动昼夜相位"（没有 DLMO/CBTmin 终点），所以三餐
    只用于规律进食与避免过晚进食，不当作相位杠杆宣传。
  * 步长由「总偏移 D ÷ 天数 N」现场生成的连续函数决定：s_i ∝ 1 + α(1-u)^p
    （u=(i-1)/(N-1)，α=0.6、p=1），先快后慢、末日仍以约 62% 的首日速度移动，
    首日只有日均的 (1+α)/(1+α/2)≈1.23 倍（前期不陡），且 s_last/s_first 与 N、D
    无关；每天 60 分钟只是"提示阈值"——超过它就在提示里写清最后一天要挪多少分钟，
    不阻止生成，也不会把差额堆到某一天（旧实现把走不完的部分全压到最后一天）。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import or_, select

from ..core.timing import (
    derive_meal_date,
    circular_diff_minutes,
    circular_mean_minutes,
    derive_wake_date,
    format_hhmm,
    parse_hhmm,
    sleep_duration_minutes,
    parse_iso_date,
)
from ..models import MealCheck, SleepRecord, User, utcnow

DAY = 24 * 60
ENGINE = "adaptive-v2"

# --------------------------------------------------------------------------- #
# 三餐：相对起床/入睡的窗口（分钟）
# --------------------------------------------------------------------------- #
BREAKFAST_AFTER_WAKE = (15, 30)          # 起床后 15~30 分钟，最佳取窗口起点
LUNCH_AFTER_WAKE = (3 * 60, 5 * 60)      # 午餐窗口长度 2 小时（窗口起点不再锚在起床，见 _meal_times）
DINNER_BEFORE_SLEEP = (2 * 60, 210)      # 睡前 2~3.5 小时，最佳 3.5 小时（实际起点再叠加 MEAL_ADVANCE_MIN）
LUNCH_TO_DINNER_MIN = 4 * 60             # 午餐至少早于晚餐 4 小时，别把两餐挤在一起
MEAL_BAR_MINUTES = {"breakfast": 30, "lunch": 60, "dinner": 60}   # 时间图条长（仅视觉）
MEAL_EARLY_AFTER_SKIP = {                # 「哪一餐没吃 → 让下一餐提前多少」（只看昨天与当天）
    "dinner": 30,                        # 昨天晚餐没吃 → 今天早餐提前 30 分钟
    "breakfast": 60,                     # 今天早餐没吃 → 今天午餐与晚餐各提前 60 分钟
    "lunch": 30,                         # 今天午餐没用 → 今天晚餐再提前 30（与上面叠加）
}
DINNER_MAX_EARLY = 90                    # 晚餐最多提前 90 分钟（早餐 60 + 午餐 30）
BREAKFAST_FLOOR_AFTER_WAKE = 5           # 早餐提前后仍不早于起床后 5 分钟
LUNCH_FLOOR_AFTER_WAKE = 120             # 午餐不早于起床后 2 小时
MEAL_KEYS = ("breakfast", "lunch", "dinner")

# 现实作息锚点：作息落在"正常范围"内时，午/晚餐就近靠拢到社会常规时段，
# 而不是继续跟着（可能很极端的）起床/入睡时刻线性漂移。
NORMAL_WAKE_UNTIL = 15 * 60              # 起床不晚于 15:00 才算正常作息
NORMAL_SLEEP_UNTIL = 6 * 60              # 入睡不晚于次日 06:00 才算正常作息
LUNCH_NORMAL_WINDOW = (11 * 60, 15 * 60)     # 常规午餐时段 11:00~15:00
DINNER_NORMAL_WINDOW = (17 * 60, 22 * 60)    # 常规晚餐时段 17:00~22:00
MEAL_ADVANCE_MIN = 60                    # 现有算法整体提前 1 小时（午餐与晚餐一起前移）
LUNCH_AFTER_BREAKFAST_GAP = 30           # 保序：午餐至少晚于早餐结束 30 分钟
MIN_MEAL_GAP = 120                       # 保序：午餐与晚餐之间至少留 2 小时

# --------------------------------------------------------------------------- #
# 相位步长
# --------------------------------------------------------------------------- #
# 曲线不再写死"第几段用几分钟"：由「总偏移 D + 天数 N」现场生成一条连续函数，
# 保证先快后慢、总和精确等于 D、末日仍有速度，最大一步只有日均的约 1.23 倍。
STEP_SHAPE_ALPHA = 0.6       # 首日比末日快多少：s_last / s_first = 1 / (1 + alpha)
STEP_SHAPE_POWER = 1.0       # 递减分布：1.0 = 每天等量递减（开始最平缓）
STEP_CAP_ADVANCE = 60        # 前移每天的"提示阈值"（不是硬约束，出处见模块文档）
STEP_CAP_DELAY = 60          # 后移每天的"提示阈值"（同上）
STEP_CAP_HARD = 120          # 单日输入上限（用户设定 15 分钟 ~ 2 小时）
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
    """单日单餐的执行情况：优先取「较新的一方」（一键确认 vs 记录页勾选）。

    两种状态：eaten 已吃、skipped 没吃。老数据里的「不想吃」
    （meal_checks.refused）并入 skipped —— 那个按钮已经下线，
    「吃没吃」这一件事就够了。
    """
    check = checks.get((day, meal))
    if check is not None and record is not None:
        use_check = (check.updated_at or utcnow()) >= (record.updated_at or utcnow())
    else:
        use_check = check is not None
    if use_check:
        # 老数据里 refused=True 表示「不想吃」，现在与 skipped 同义（按钮已下线）
        return {"state": "eaten" if check.eaten else "skipped", "source": "checkin"}
    if record is not None:
        value = bool(getattr(record, f"meal_{meal}", True))
        return {"state": "eaten" if value else "skipped", "source": "record"}
    return {"state": "unknown", "source": None}


def wake_day_of(record: SleepRecord) -> str:
    """这条记录属于哪一天：**起床日**。

    一条记录跨两个日历日（昨天 23:30 睡、今天 07:30 起）。它描述的那一天是"起床后
    的那一天"：记录页勾的三餐是那天的饭，计划日卡片、打卡与达标率也都以那天为准。
    入睡日（record_date）仍然是睡眠统计、时间轴与保留策略的标签。老记录没有
    wake_date 时按老规则推导（起床时刻 <= 入睡时刻 → 次日），与迁移脚本一致。
    """
    if record.wake_date:
        return record.wake_date
    return derive_wake_date(record.record_date, record.sleep_time, record.wake_time)


def meal_day_of(record: SleepRecord) -> str:
    """这条记录的三餐属于哪一天：优先 meal_date（用户可以在表单里改）。

    没有 meal_date 的老记录按默认规则推导：入睡日；入睡与醒来同一天（过了零点才睡）
    则取前一天 —— 与 api/records.py 的默认值、迁移脚本完全一致（见
    core.timing.derive_meal_date）。
    """
    if record.meal_date:
        return record.meal_date
    return derive_meal_date(record.record_date, wake_day_of(record))


def meal_history(
    db, user_id: int, *, days: int = MEAL_HISTORY_DAYS, today: date | None = None
) -> dict:
    """最近若干天的三餐执行情况（含来源），用于「三餐没吃 → 下一餐提前」。

    键是**三餐记录日**（见 `meal_day_of`：默认等于入睡日，跨零点入睡时是前一天）；
    同一格可能落到多条记录上（例如同一晚分两次记），此时取 updated_at 较新的一条 ——
    与 `_meal_state` 里「打卡与勾选取较新一方」的口径一致。
    """
    today = today or date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    records: dict[str, SleepRecord] = {}
    for row in (
        db.execute(
            select(SleepRecord).where(
                SleepRecord.user_id == user_id,
                # 起床日也要纳入：昨天入睡、今天起床的记录属于今天这一格
                # 三餐记录日可能早于入睡日（跨零点入睡），三个日期都要纳入
                or_(
                    SleepRecord.record_date >= start,
                    SleepRecord.wake_date >= start,
                    SleepRecord.meal_date >= start,
                ),
            )
        )
        .scalars()
        .all()
    ):
        key = meal_day_of(row)
        current = records.get(key)
        if current is None or (row.updated_at or utcnow()) >= (current.updated_at or utcnow()):
            records[key] = row
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
    return {"days": items, "summary": summary, "by_date": {item["date"]: item["meals"] for item in items},}


def meal_plan_snapshot(history: dict, *, today: date) -> dict:
    """今天 / 昨天 / 明天早餐的三餐状态，供建议页说明「哪一餐没吃带动了哪一餐」。

    只给事实（eaten / skipped / unknown）与日期：谁影响谁由 `_meal_times` 的规则决定，
    不让前端重算业务规则。
    """

    by_date = history.get("by_date") or {}

    def day_state(the_date: date) -> dict:
        meals = by_date.get(the_date.isoformat()) or {}
        return {
            "date": the_date.isoformat(),
            "breakfast": (meals.get("breakfast") or {}).get("state", "unknown"),
            "lunch": (meals.get("lunch") or {}).get("state", "unknown"),
            "dinner": (meals.get("dinner") or {}).get("state", "unknown"),
        }

    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    tomorrow_meals = by_date.get(tomorrow.isoformat()) or {}
    return {
        "today": day_state(today),
        "yesterday": day_state(yesterday),
        # 明天早餐会不会提前，取决于今晚的晚餐吃没吃
        "tomorrow_breakfast": (tomorrow_meals.get("breakfast") or {}).get("state", "unknown"),
    }


def _basis(records: list[SleepRecord]) -> dict:
    """基准作息：最近记录的加权圆环均值（越新权重越高）。"""
    if not records:
        return {
            "sample_size": 0,
            "sleep_minutes": IDEAL_SLEEP,
            "wake_minutes": IDEAL_WAKE,
            "duration_minutes": IDEAL_DURATION,
            "latest_record_date": None,
        }

    ordered = sorted(records, key=lambda r: r.record_date)
    weights = [1.0 + index * 0.1 for index in range(len(ordered))]
    sleep_values = [parse_hhmm(r.sleep_time) for r in ordered]
    wake_values = [parse_hhmm(r.wake_time) for r in ordered]

    sleep_mean = circular_mean_minutes(sleep_values, weights)
    wake_mean = circular_mean_minutes(wake_values, weights)

    durations = [sleep_duration_minutes(s, w) for s, w in zip(sleep_values, wake_values)]
    avg_duration = int(round(sum(durations) / len(durations)))

    return {
        "sample_size": len(ordered),
        "sleep_minutes": sleep_mean,
        "wake_minutes": wake_mean,
        "duration_minutes": avg_duration,
        "latest_record_date": ordered[-1].record_date,
        "duration_minutes_latest": durations[-1],
    }


def _system_target(basis: dict) -> dict:
    """系统推荐目标：向健康作息收敛。

    早期这里还会拿用户填的"最晚起床 / 最早入睡"当硬上限与下限，那两个功能已按用户
    要求删除：目标只由实际作息均值与健康区间决定，不再受额外约束牵制。
    """
    sleep_target = basis["sleep_minutes"]
    wake_target = basis["wake_minutes"]

    duration = sleep_duration_minutes(sleep_target, wake_target)
    if duration < IDEAL_DURATION:
        sleep_target -= min(IDEAL_DURATION - duration, 90)
    elif duration > IDEAL_DURATION + 60:
        sleep_target += min(duration - IDEAL_DURATION, 60)

    wake_shift = circular_diff_minutes(wake_target, IDEAL_WAKE)
    wake_target += max(-45, min(45, wake_shift))

    if sleep_duration_minutes(sleep_target, wake_target) < 6 * 60:
        wake_target = sleep_target + 6 * 60

    return {
        "sleep_minutes": sleep_target % DAY,
        "wake_minutes": wake_target % DAY,
        "duration_minutes": sleep_duration_minutes(sleep_target, wake_target),
    }


# --------------------------------------------------------------------------- #
# 步长：由「总偏移 ÷ 天数」现场生成（先快后慢，尾部保留速度）
# --------------------------------------------------------------------------- #
def step_cap(direction: int) -> int:
    """每天的"提示阈值"（不是硬约束）：超过它只提示，不阻止生成计划。"""
    return STEP_CAP_ADVANCE if direction < 0 else STEP_CAP_DELAY


def _shape_steps(span: int, days: int) -> list[int]:
    """把 span 分摊到 days 天：φ(u) = 1 + α·(1-u)^p，u = (i-1)/(N-1)。

    性质（与天数、总跨度无关，所以天数变多曲线也不会变陡）：
      * sum(steps) == span，最后一天仍在移动；
      * steps 单调不增（先快后慢）；
      * steps[-1] / steps[0] == 1 / (1 + α)：末日保留首日的 1/(1+α) 速度；
      * steps[0] / 日均 == (1+α) / (1+α/2) ≈ 1.23：首日只比日均快两成，前期不陡。
    累计取整，避免每项四舍五入后总和跑偏。
    """
    span = abs(int(span))
    days = max(1, int(days))
    if span == 0:
        return [0] * days
    if days == 1:
        return [span]

    weights = [
        1.0 + STEP_SHAPE_ALPHA * (1.0 - index / (days - 1)) ** STEP_SHAPE_POWER
        for index in range(days)
    ]
    total = sum(weights)
    steps: list[int] = []
    placed = 0
    for index, weight in enumerate(weights):
        cumulative = int(round(span * sum(weights[: index + 1]) / total))
        steps.append(cumulative - placed)
        placed = cumulative

    for index in range(days - 1, 0, -1):          # 取整可能让后一天多 1 分钟：向前借平
        if steps[index] > steps[index - 1]:
            excess = steps[index] - steps[index - 1]
            steps[index] -= excess
            steps[index - 1] += excess
    return steps


def _step_magnitudes(span: int, days: int, direction: int) -> tuple[list[int], bool]:
    """每天的步长，以及「是否每一天都落在提示阈值内」。

    形状函数保证总和精确等于 span、末日仍在移动，因此不再需要"先顶满上限、再把
    差额压到最后一天"（旧实现会让末日突然挪 150 分钟）。feasible 只表示某一步
    超过阈值，用于提示，不改变生成的计划。
    """
    span = abs(int(span))
    days = max(1, int(days))
    steps = _shape_steps(span, days)
    cap = step_cap(direction)
    return steps, (max(steps) <= cap if steps else True)


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
    """要让最大的一步落进提示阈值，至少需要多少天（只用于提示，不阻止生成）。"""
    span = abs(int(span))
    if span == 0:
        return 1
    cap = step_cap(direction)
    for days in range(1, limit + 1):
        if max(_shape_steps(span, days)) <= cap:
            return days
    return limit


def _plan_steps(total_sleep: int, total_wake: int, days: int, mode: str) -> dict:
    """计算每天的累计偏移（入睡/起床分别按同一进度比例推进）。"""
    days = max(1, days)
    total_sleep = int(total_sleep)
    total_wake = int(total_wake)
    dominant = total_sleep if abs(total_sleep) >= abs(total_wake) else total_wake
    direction = -1 if dominant < 0 else 1
    cap = step_cap(direction)

    if mode == "fast" or days == 1:
        return {
            "cumulative": [(total_sleep, total_wake)],
            "feasible": abs(dominant) <= cap,
            "suggested_days": suggested_days(dominant, direction),
            "direction": direction,
            "daily_average": abs(dominant),
            # 只有一天：首日、末日、最大一步都是全天
            "last_step_minutes": abs(dominant),
            "max_step_minutes": abs(dominant),
            "first_step_minutes": abs(dominant),
        }

    magnitudes, feasible = _cumulative_magnitudes(dominant, days, direction)

    cumulative: list[tuple[int, int]] = []
    for magnitude in magnitudes:
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
        "daily_average": int(round(abs(dominant) / days)),
        # 形状函数对每一天直接给步长，不再有"小偏移集中到少数几天"的压缩
        # 最后一天的步长（入睡那条腿，与日卡上"今天移动 N 分钟"同一个数）
        "last_step_minutes": int(daily[-1]) if daily else 0,
        # 最大一步：形状函数下它总在开头，提示里报的是这个数
        "max_step_minutes": max(daily) if daily else 0,
        "first_step_minutes": int(daily[0]) if daily else 0,
    }


# --------------------------------------------------------------------------- #
# 三餐与时间图
# --------------------------------------------------------------------------- #
def _meal_times(
    sleep_minutes: int,
    wake_minutes: int,
    enabled: dict[str, bool],
    *,
    prev_meals: dict | None = None,
    today_meals: dict | None = None,
) -> dict[str, dict]:
    """三餐推荐时间：早餐锚起床、晚餐锚入睡，午餐取两者的中点，再按现实作息校准。

    早先午餐锚在「起床 + 3 小时」，白天越长（例如 06:00 起、01:00 睡）午餐与晚餐
    之间就拉得越开 —— 实测能到 12.5 小时。改成取早餐与晚餐的中点后，同一作息的间隔
    收敛到 7.6 小时；短白天的人（07:00 起、23:00 睡）从 9.5 小时收敛到 6.1 小时。
    中点再夹进 [起床+2h, 晚餐-4h]。

    **三餐记录只带动下一餐，且只看昨天与当天**（不再累积「连续几天没吃」的阶梯）：
    昨天晚餐 skip → 今天早餐提前 30；今天早餐 skip → 午餐、晚餐各提前 60；
    今天午餐 skip → 晚餐再提前 30（两条叠加，最多 90）。更远的日子没有数据，不动。

    求出的三餐还要过三道校正，顺序不能调换：
    ① 先按上面的锚点算基准，再整体提前 1 小时（MEAL_ADVANCE_MIN）；
    ② 现实作息窗口：正常作息时把午餐、晚餐就近压回社会常规时段；
    ③ 保序：晚餐早于入睡、午餐晚于早餐、两餐之间留够间隔。

    每一餐都带 `shift_reason`（哪一顿没吃导致它提前）：前端据此写「为什么提前」，
    不再自己猜来源。
    """
    wake = wake_minutes % DAY
    sleep = sleep_minutes % DAY
    if sleep <= wake:
        # 摊到一条不折返的轴上（入睡在零点后则 +24 小时）：否则"睡前 3.5 小时"
        # 与取中点都会算到负数上去，跨零点的作息（01:00 睡、06:00 起）直接错位。
        sleep += DAY
    sleep_clock = sleep % DAY

    def state_of(meals: dict | None, key: str) -> str:
        return str(((meals or {}).get(key) or {}).get("state") or "unknown")

    # 只看昨天与今天：早餐受「昨天晚餐没吃」影响，午餐受「今天早餐没吃」影响，
    # 晚餐受「今天早餐没吃 + 今天午餐没用」影响（两条叠加，最多 90 分钟）。
    breakfast_reason = "yesterday_dinner" if state_of(prev_meals, "dinner") == "skipped" else None
    breakfast_shift = MEAL_EARLY_AFTER_SKIP["dinner"] if breakfast_reason else 0
    breakfast_base = wake + BREAKFAST_AFTER_WAKE[0]
    breakfast_best = max(wake + BREAKFAST_FLOOR_AFTER_WAKE, breakfast_base - breakfast_shift)
    breakfast_end = breakfast_best + (BREAKFAST_AFTER_WAKE[1] - BREAKFAST_AFTER_WAKE[0])
    breakfast_applied = max(0, breakfast_base - breakfast_best)

    lunch_reason = "breakfast" if state_of(today_meals, "breakfast") == "skipped" else None
    lunch_shift = MEAL_EARLY_AFTER_SKIP["breakfast"] if lunch_reason else 0

    dinner_shift = 0
    dinner_reasons: list[str] = []
    if state_of(today_meals, "breakfast") == "skipped":
        dinner_shift += MEAL_EARLY_AFTER_SKIP["breakfast"]
        dinner_reasons.append("breakfast")
    if state_of(today_meals, "lunch") == "skipped":
        dinner_shift += MEAL_EARLY_AFTER_SKIP["lunch"]
        dinner_reasons.append("lunch")
    dinner_shift = min(dinner_shift, DINNER_MAX_EARLY)
    dinner_reason = "_and_".join(dinner_reasons) if dinner_reasons else None

    # ① 现有算法 + 整体提前 1 小时（三餐记录再额外提前上面算出的量）
    dinner_pre = sleep - DINNER_BEFORE_SLEEP[1] - MEAL_ADVANCE_MIN
    dinner_best = dinner_pre - dinner_shift
    lunch_mid = int(round((breakfast_best + dinner_best) / 2)) - MEAL_ADVANCE_MIN
    lunch_pre = max(
        wake + LUNCH_FLOOR_AFTER_WAKE,
        min(lunch_mid, dinner_best - LUNCH_TO_DINNER_MIN),
    )
    lunch_best = max(lunch_pre - lunch_shift, wake + LUNCH_FLOOR_AFTER_WAKE)

    # ② 现实窗口：作息正常就"就近靠拢"到常规用餐时段，越界则整步跳过。
    #    晚餐的"正常入睡"= 次日 06:00 之前，或当天傍晚之后（都算夜里睡）；
    #    06:00~17:00 之间入睡属于白天补觉，不套用社会时钟。
    if wake <= NORMAL_WAKE_UNTIL:
        lunch_best = max(LUNCH_NORMAL_WINDOW[0], min(lunch_best, LUNCH_NORMAL_WINDOW[1]))
    if sleep_clock <= NORMAL_SLEEP_UNTIL or sleep_clock >= DINNER_NORMAL_WINDOW[0]:
        dinner_best = max(DINNER_NORMAL_WINDOW[0], min(dinner_best, DINNER_NORMAL_WINDOW[1]))

    # ③ 保序：窗口可能把两餐推到睡眠之后或互相倒挂，这里统一收尾。
    dinner_best = min(dinner_best, sleep - DINNER_BEFORE_SLEEP[0])
    lunch_best = min(lunch_best, dinner_best - MIN_MEAL_GAP)
    lunch_best = max(lunch_best, breakfast_end + LUNCH_AFTER_BREAKFAST_GAP)
    if lunch_best + MIN_MEAL_GAP > dinner_best:
        # 起床到入睡不足 6 小时的极端作息下已无处安放三餐：顺序优先，
        # 让晚餐跟着午餐走，牺牲"睡前 2 小时"这条缓冲。
        dinner_best = lunch_best + MIN_MEAL_GAP

    # 偏移量按“实际发生了多少”记账：被窗口/顺序 clamp 掉的部分不算。
    lunch_applied = max(0, lunch_pre - lunch_best)
    dinner_applied = max(0, dinner_pre - dinner_best)

    dinner_end = dinner_best + (DINNER_BEFORE_SLEEP[1] - DINNER_BEFORE_SLEEP[0])
    lunch_end = lunch_best + (LUNCH_AFTER_WAKE[1] - LUNCH_AFTER_WAKE[0])

    plan = {
        "breakfast": {
            "best": breakfast_best,
            "end": breakfast_end,
            "anchor": "wake",
            "shifted_minutes": breakfast_applied,
            "shift_reason": breakfast_reason,
        },
        "lunch": {
            "best": lunch_best,
            "end": lunch_end,
            # 午餐同时受起床与入睡两端牵制（取中点），前端只把它当说明字段用
            "anchor": "both",
            "shifted_minutes": lunch_applied,
            "shift_reason": lunch_reason,
        },
        "dinner": {
            "best": dinner_best,
            "end": dinner_end,
            "anchor": "sleep",
            "shifted_minutes": dinner_applied,
            "shift_reason": dinner_reason,
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
            "shifted_minutes": int(item.get("shifted_minutes") or 0),
            "shift_reason": item["shift_reason"],
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
    max_step: int,
    cap: int,
    acked: set[str],
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
            # 曲线由函数决定，最大的一步出现在开头：提示直接报「最大一步」与阈值
            step=max_step,
            cap=cap,
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
        target = _system_target(basis)

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
    # 今天 / 昨天 / 明天早餐的三餐状态：前端用它写「为什么这一餐提前了」
    meal_states = history.get("by_date") or {}
    meal_plan = meal_plan_snapshot(history, today=today)

    steps = _plan_steps(
        circular_diff_minutes(basis["sleep_minutes"], target["sleep_minutes"]),
        circular_diff_minutes(basis["wake_minutes"], target["wake_minutes"]),
        plan_days,
        mode,
    )
    direction = steps["direction"]

    plan: list[dict] = []
    for index, (cumulative_sleep, cumulative_wake) in enumerate(steps["cumulative"]):
        sleep_minutes = (basis["sleep_minutes"] + cumulative_sleep) % DAY
        wake_minutes = (basis["wake_minutes"] + cumulative_wake) % DAY
        duration = sleep_duration_minutes(sleep_minutes, wake_minutes)
        step_minutes = 0
        if index > 0:
            previous_sleep = steps["cumulative"][index - 1][0]
            step_minutes = abs(cumulative_sleep - previous_sleep)
        else:
            step_minutes = abs(cumulative_sleep)
        day_date = today + timedelta(days=index)
        meals = _meal_times(
            sleep_minutes,
            wake_minutes,
            enabled,
            # 只看昨天与当天：更远的日子没有记录，get() 自然返回 None（= 不提前）
            prev_meals=meal_states.get((day_date - timedelta(days=1)).isoformat()),
            today_meals=meal_states.get(day_date.isoformat()),
        )
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
        max_step=steps["max_step_minutes"],
        cap=step_cap(direction),
        acked=acked,
    )

    today_index = next(
        (index for index, day in enumerate(plan) if day["date"] == today.isoformat()), 0
    )
    today_card = plan[today_index] if plan else None
    # 今日卡片自带"拒绝早餐的预测"：刚生成方案时前端也能在按钮下方提示"点了会变成…"
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
            # 每日建议步长（前移/后移同一个阈值）：只用于提示，不会截断计划
            "cap_minutes": step_cap(direction),
            "hard_cap_minutes": STEP_CAP_HARD,
            "daily_average_minutes": steps["daily_average"],
            # 曲线由形状函数现场生成：只报形状参数与首 / 最大 / 收尾三天的实际步长，
            # 每天具体多少分钟看 plan[*].step_minutes（不再返回固定分段表）
            "shape": {
                "alpha": STEP_SHAPE_ALPHA,
                "power": STEP_SHAPE_POWER,
            },
            "first_step_minutes": steps["first_step_minutes"],
            "max_step_minutes": steps["max_step_minutes"],
            "last_step_minutes": steps["last_step_minutes"],
            "feasible": steps["feasible"],
            "suggested_days": steps["suggested_days"],
        },
        "meal_plan": {
            **meal_plan,
            # 最近 7 天的计数（eaten / skipped / unknown），前端只做「有几条记录」的说明
            "days": history["days"],
            "summary": history["summary"],
            "history": history["summary"],
            "recent": history["days"],
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


def apply_meal_overlay(payload: dict | None, history: dict, *, today: date) -> dict | None:
    """把「今天与明天」的三餐按**当前**记录重算一遍（不重写快照）。

    快照是生成那一刻算好的：一次打卡 / 一次勾选不该重写它（否则 plan_meta 里的
    「已重算 N 次」会莫名上涨、历史计划也被悄悄改写）。所以读取时只覆盖今天与明天
    这两行 —— 规则只看昨天与当天，再往后没有数据可用，算出来也不会变。
    """
    states = history.get("by_date") or {}
    if not payload:
        return payload
    for day in payload.get("plan") or []:
        try:
            day_date = parse_iso_date(day["date"])
        except (KeyError, ValueError):
            continue
        if day_date not in (today, today + timedelta(days=1)):
            continue
        meals = day.get("meals") or {}
        enabled = {key: bool(item.get("enabled", True)) for key, item in meals.items()}
        sleep_minutes = parse_hhmm(day["sleep_time"])
        wake_minutes = parse_hhmm(day["wake_time"])
        fresh = _meal_times(
            sleep_minutes,
            wake_minutes,
            enabled,
            prev_meals=states.get((day_date - timedelta(days=1)).isoformat()),
            today_meals=states.get(day_date.isoformat()),
        )
        day["meals"] = fresh
        day["timeline"] = _timeline(sleep_minutes, wake_minutes, fresh)
    return payload


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
    "STEP_SHAPE_ALPHA",
    "STEP_SHAPE_POWER",
    "STEP_CAP_ADVANCE",
    "STEP_CAP_DELAY",
    "STEP_CAP_HARD",
    "SHORT_PLAN_DAILY_THRESHOLD",
    "DEFAULT_SLOW_DAYS",
    "build_advice",
    "day_for",
"meal_day_of",
"meal_history",
"meal_plan_snapshot",
"apply_meal_overlay",
    "wake_day_of",
    "suggested_days",
    "step_cap",
]
