"""advice 引擎与计划服务的单元测试（不依赖已有 data/app.db）。

用独立的临时数据库：在导入 app 之前设置 SSP_DB_PATH / SSP_DATA_DIR。
运行：backend\\.venv\\Scripts\\python.exe -m pytest tests/test_advice_engine.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

_TMP = Path(tempfile.mkdtemp(prefix="ssp-advice-test-"))
os.environ["SSP_DB_PATH"] = str(_TMP / "test.db")
os.environ["SSP_DATA_DIR"] = str(_TMP)
os.environ["SSP_LOG_DIR"] = str(_TMP)
# 必须连 LOG_FILE 一起改：只改 LOG_DIR 的话，日志文件名仍是默认的 <项目根>/logs/app.log，
# 而 logkit 只会创建 LOG_DIR 指向的目录 —— 在没有 logs/ 的新克隆里会以
# FileNotFoundError 直接死在 import 阶段（CI 之所以没暴露，是因为 workflow 里两个变量都设了）。
os.environ["SSP_LOG_FILE"] = str(_TMP / "test.log")
os.environ["SSP_WRITE_COOLDOWN_SECONDS"] = "0"

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import pytest                                                    # noqa: E402
from fastapi.testclient import TestClient                        # noqa: E402

from app.core.security import hash_password                      # noqa: E402
from app.core.timing import parse_hhmm                           # noqa: E402
from app.db import SessionLocal, init_db                         # noqa: E402
from app.main import app                                         # noqa: E402
from app.models import SleepRecord, User                         # noqa: E402
from app.services import advice as advice_service                # noqa: E402
from app.services import advice_plan as plan_service             # noqa: E402

USERNAME = "tester"
PASSWORD = "User@12345"
TODAY = date.today()


def _records_pattern(sleep: str, wake: str, days: int = 5) -> list[tuple[str, str, str]]:
    """最近 days 天的记录，**包含今天**（今天有记录才能判断偏离）。"""
    return [
        ((TODAY - timedelta(days=days - 1 - index)).isoformat(), sleep, wake)
        for index in range(days)
    ]


def _make_user(username: str, *, rows: list[tuple[str, str, str]] | None = None,
               target: tuple[str, str] | None = None) -> int:
    with SessionLocal() as session:
        user = User(username=username, password_hash=hash_password(PASSWORD))
        if target:
            user.target_sleep_time, user.target_wake_time = target
        session.add(user)
        session.flush()
        user_id = int(user.id)
        for record_date, sleep_time, wake_time in rows or []:
            session.add(
                SleepRecord(
                    user_id=user_id,
                    record_date=record_date,
                    sleep_time=sleep_time,
                    wake_time=wake_time,
                )
            )
        session.commit()
    return user_id


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD, "remember": True}
    )
    assert response.status_code == 200, response.text


@pytest.fixture(scope="module", autouse=True)
def _bootstrap() -> None:
    init_db()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #
def test_advice_shape_and_windows(client: TestClient) -> None:
    _make_user("shape", rows=_records_pattern("01:00", "09:00"), target=("21:00", "05:00"))
    _login(client, "shape")

    options = client.get("/api/advice/options").json()
    assert options["engine"] == "adaptive-v2"
    assert options["steps"]["direction_caps"] == {"advance": 45, "delay": 90}
    assert options["steps"]["curve_minutes"] == [60, 45, 30, 15]
    assert options["deviation"]["ask_minutes"] == 90

    body = client.post(
        "/api/advice", json={"mode": "slow", "days": 7, "use_target": True}
    ).json()
    assert body["engine"] == "adaptive-v2"
    assert body["source"] == "custom_target"
    assert len(body["plan"]) == 7
    assert body["plan_meta"]["saved"] is True and body["plan_meta"]["plan_id"]
    assert body["steps"]["direction"] == "advance"

    for day in body["plan"]:
        wake = parse_hhmm(day["wake_time"])
        sleep = parse_hhmm(day["sleep_time"])
        meals = day["meals"]
        assert meals["breakfast"]["best_time"] == advice_service.format_hhmm(
            (wake + 15) % advice_service.DAY
        )
        # 本用例的作息（09:00 起 / 01:00 睡，目标 05:00 起 / 21:00 睡）全程都在"正常范围"里，
        # 所以午/晚餐必定被就近靠拢进社会常规时段：午餐 11:00~15:00、晚餐 17:00~22:00。
        lunch = parse_hhmm(meals["lunch"]["best_time"])
        dinner = parse_hhmm(meals["dinner"]["best_time"])
        assert advice_service.LUNCH_NORMAL_WINDOW[0] <= lunch <= advice_service.LUNCH_NORMAL_WINDOW[1], lunch
        assert advice_service.DINNER_NORMAL_WINDOW[0] <= dinner <= advice_service.DINNER_NORMAL_WINDOW[1], dinner
        assert meals["lunch"]["anchor"] == "both"
        # 顺序与缓冲：早餐 → 午餐 → 晚餐 依次向后，晚餐留出睡前 2 小时以上
        assert (lunch - wake - 15) % advice_service.DAY >= advice_service.LUNCH_AFTER_BREAKFAST_GAP
        assert (dinner - lunch) % advice_service.DAY >= advice_service.MIN_MEAL_GAP, (lunch, dinner)
        span_sleep = sleep + advice_service.DAY if sleep <= wake else sleep
        assert span_sleep - dinner >= advice_service.DINNER_BEFORE_SLEEP[0]
        # 窗口长度不变：午餐 120 分钟、晚餐 90 分钟
        assert meals["lunch"]["window_end"] == advice_service.format_hhmm((lunch + 120) % advice_service.DAY)
        assert meals["dinner"]["window_start"] == advice_service.format_hhmm(dinner % advice_service.DAY)
        assert meals["dinner"]["window_end"] == advice_service.format_hhmm(
            (dinner + advice_service.DINNER_BEFORE_SLEEP[1] - advice_service.DINNER_BEFORE_SLEEP[0])
            % advice_service.DAY
        )
        assert meals["breakfast"]["bar_minutes"] < meals["lunch"]["bar_minutes"]
        assert meals["breakfast"]["time"] == meals["breakfast"]["best_time"]
        assert day["timeline"]["items"], "时间图不能为空"
        kinds = {item["kind"] for item in day["timeline"]["items"]}
        assert kinds == {"sleep", "meal"}
        assert day["tips"]

    # 最后一天精确落在自定义目标上
    assert body["plan"][-1]["sleep_time"] == "21:00"
    assert body["plan"][-1]["wake_time"] == "05:00"
    assert body["today"]["date"] == TODAY.isoformat()
    assert body["timeline"] == body["today"]["timeline"]
    assert "advice.note.first_three_days" in body["notes"]


def test_step_curve_is_decreasing_and_lands_on_target(client: TestClient) -> None:
    _make_user("curve", rows=_records_pattern("02:00", "10:00"), target=("22:00", "06:00"))
    _login(client, "curve")
    body = client.post("/api/advice", json={"mode": "slow", "days": 10}).json()

    steps = [day["step_minutes"] for day in body["plan"]]
    assert steps[0] <= advice_service.STEP_CURVE[0][1]
    assert all(later <= earlier + 1e-6 for earlier, later in zip(steps, steps[1:])), steps
    assert sum(steps) == 240
    assert body["plan"][-1]["sleep_time"] == "22:00"


def test_step_profile_respects_caps_and_front_loads() -> None:
    """纯函数：每天不超过当天上限、整体先快后缓、总和精确等于总偏移。"""
    caps = advice_service._step_caps(10, -1)
    steps, feasible = advice_service._step_magnitudes(240, 10, -1)
    assert feasible is True
    assert sum(steps) == 240
    assert all(step <= cap for step, cap in zip(steps, caps)), (steps, caps)
    assert all(later <= earlier for earlier, later in zip(steps, steps[1:])), steps
    assert steps[0] > steps[-1], steps

    # 天数不足时不报 feasible，并把走不完的差额留到最后一天
    tight, ok = advice_service._step_magnitudes(240, 3, -1)
    assert ok is False and sum(tight) == 240
    assert advice_service.suggested_days(240, -1) == 7
    # 小偏移（日均不足 15 分钟）也能精确落点
    small, small_ok = advice_service._step_magnitudes(20, 3, 1)
    assert small_ok is True and sum(small) == 20


def test_delay_cap_is_looser_than_advance(client: TestClient) -> None:
    _make_user("delay", rows=_records_pattern("21:30", "05:30"), target=("01:30", "09:30"))
    _login(client, "delay")
    body = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    assert body["steps"]["direction"] == "delay"
    assert body["steps"]["direction"] == "delay"
    assert body["steps"]["cap_delay_minutes"] == 90
    assert body["steps"]["daily_average_minutes"] <= 90


def test_short_plan_notice_can_be_acknowledged(client: TestClient) -> None:
    _make_user("shortplan", rows=_records_pattern("03:00", "11:00"), target=("21:00", "05:00"))
    _login(client, "shortplan")
    body = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()

    assert body["steps"]["daily_average_minutes"] > advice_service.SHORT_PLAN_DAILY_THRESHOLD
    codes = {notice["code"]: notice for notice in body["notices"]}
    assert "advice.notice.short_plan" in codes
    notice = codes["advice.notice.short_plan"]
    assert notice["ackable"] is True and notice["acked"] is False
    assert notice["params"]["daily_average"] > 60
    assert notice["params"]["suggested_days"] >= 4

    ack = client.post(
        "/api/advice/notice-ack", json={"notice": "advice.notice.short_plan"}
    ).json()
    assert ack["ok"] is True
    assert "advice.notice.short_plan" in ack["acked_notices"]

    again = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    acked = {notice["code"]: notice for notice in again["notices"]}
    assert acked["advice.notice.short_plan"]["acked"] is True


def test_lunch_shifts_earlier_when_breakfast_skipped(client: TestClient) -> None:
    username = "mealshift"
    user_id = _make_user(username, rows=_records_pattern("00:30", "08:30"), target=("23:00", "07:00"))
    _login(client, username)

    base = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    assert base["meal_plan"]["lunch_shift_minutes"] == 0

    for offset in (3, 2, 1):
        response = client.post(
            "/api/advice/checkin",
            json={
                "meal": "breakfast",
                "eaten": False,
                "date": (TODAY - timedelta(days=offset)).isoformat(),
            },
        ).json()
        assert response["ok"] is True
    assert response["meal_plan"]["breakfast_skipped_streak"] == 3
    assert response["meal_plan"]["lunch_shift_minutes"] == 120

    shifted = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    assert shifted["meal_plan"]["lunch_shift_minutes"] == 120
    day = shifted["plan"][0]
    wake = parse_hhmm(day["wake_time"])
    shifted_lunch = parse_hhmm(day["meals"]["lunch"]["best_time"])
    # 阶梯前移在"现实窗口"里能走多少走多少：午餐被夹进 11:00~15:00，
    # 所以前移量最多到窗口下限（11:00）为止，不会跑到 11:00 之前去。
    base_lunch = parse_hhmm(base["plan"][0]["meals"]["lunch"]["best_time"])
    expected_lunch = max(
        advice_service.LUNCH_NORMAL_WINDOW[0],
        (base_lunch - 120) % advice_service.DAY,
    )
    assert shifted_lunch < base_lunch
    assert shifted_lunch == expected_lunch, (base_lunch, shifted_lunch)
    assert advice_service.LUNCH_NORMAL_WINDOW[0] <= shifted_lunch <= advice_service.LUNCH_NORMAL_WINDOW[1]
    assert (shifted_lunch - wake) % advice_service.DAY >= advice_service.LUNCH_FLOOR_AFTER_WAKE
    assert day["meals"]["lunch"]["shifted_minutes"] == 120
    assert any(
        notice["code"] == "advice.notice.lunch_advanced" for notice in shifted["notices"]
    )

    # 一键确认会同步写回当天的睡眠记录
    with SessionLocal() as session:
        record = (
            session.query(SleepRecord)
            .filter(
                SleepRecord.user_id == user_id,
                SleepRecord.record_date == (TODAY - timedelta(days=1)).isoformat(),
            )
            .one_or_none()
        )
    assert record is not None and record.meal_breakfast is False


def test_lunch_midpoint_keeps_gaps_reasonable_on_long_days(client: TestClient) -> None:
    """晚睡早起（白天 19 小时）不该出现"午到晚"十几个小时的间隔。

    旧口径把午餐锚在"起床 + 3 小时"：06:00 起 / 01:00 睡的人午餐 09:00、晚餐 21:30，
    间隔 12.5 小时。现在午餐取早餐与晚餐的中点，同一作息的间隔收敛到 7.6 小时。
    """
    username = "longday"
    _make_user(username, rows=_records_pattern("01:00", "06:00"), target=("23:00", "07:00"))
    _login(client, username)

    body = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    meals = body["today"]["meals"]
    breakfast = parse_hhmm(meals["breakfast"]["best_time"])
    lunch = parse_hhmm(meals["lunch"]["best_time"])
    dinner = parse_hhmm(meals["dinner"]["best_time"])

    to_dinner = (dinner - lunch) % advice_service.DAY
    assert advice_service.LUNCH_TO_DINNER_MIN <= to_dinner <= 8 * 60, to_dinner
    assert (lunch - breakfast) % advice_service.DAY >= 4 * 60      # 早餐到午餐也别挤在一起
    assert (dinner - breakfast) % advice_service.DAY >= 8 * 60     # 早餐到晚餐至少 8 小时
    assert meals["lunch"]["anchor"] == "both"


def test_meals_snap_to_reality_windows_for_normal_schedules(client: TestClient) -> None:
    """作息正常就按社会时钟吃饭，作息越界就回落到相对锚点算法。

    正常：起床 ≤ 15:00 → 午餐落在 11:00~15:00；入睡不晚于次日 06:00 → 晚餐落在 17:00~22:00。
    越界：18:00 起床 + 上午 09:00 入睡（白天补觉）→ 两侧窗口都不套用，晚餐回到"睡前 4.5 小时"。
    """
    # ① 正常作息：08:30 起、00:30 睡
    _make_user("normalday", rows=_records_pattern("00:30", "08:30"), target=("00:30", "08:30"))
    _login(client, "normalday")
    day = client.post("/api/advice", json={"mode": "slow", "days": 1}).json()["plan"][0]
    meals = day["meals"]
    lunch = parse_hhmm(meals["lunch"]["best_time"])
    dinner = parse_hhmm(meals["dinner"]["best_time"])
    assert advice_service.LUNCH_NORMAL_WINDOW[0] <= lunch <= advice_service.LUNCH_NORMAL_WINDOW[1], lunch
    assert advice_service.DINNER_NORMAL_WINDOW[0] <= dinner <= advice_service.DINNER_NORMAL_WINDOW[1], dinner

    # ② 越界作息：18:00 起、09:00 睡 —— 起床晚于 15:00，入睡落在 06:00~17:00 的白天区间
    _make_user("nightowl", rows=_records_pattern("09:00", "18:00"), target=("09:00", "18:00"))
    _login(client, "nightowl")
    day = client.post("/api/advice", json={"mode": "slow", "days": 1}).json()["plan"][0]
    wake = parse_hhmm(day["wake_time"])
    sleep = parse_hhmm(day["sleep_time"])
    meals = day["meals"]
    lunch = parse_hhmm(meals["lunch"]["best_time"])
    dinner = parse_hhmm(meals["dinner"]["best_time"])
    span = sleep + advice_service.DAY if sleep <= wake else sleep
    assert wake > advice_service.NORMAL_WAKE_UNTIL
    # 晚餐回到相对锚点：睡前 4.5 小时（3.5 小时 + 整体提前 1 小时）
    assert dinner == (span - advice_service.DINNER_BEFORE_SLEEP[1] - advice_service.MEAL_ADVANCE_MIN) % advice_service.DAY
    # 午餐也不再被硬塞进社会时钟，而是停在相对锚点算出的位置
    assert not (advice_service.LUNCH_NORMAL_WINDOW[0] <= lunch <= advice_service.LUNCH_NORMAL_WINDOW[1]), lunch
    assert (dinner - lunch) % advice_service.DAY >= advice_service.MIN_MEAL_GAP


def test_checkin_eaten_true_writes_back(client: TestClient) -> None:
    username = "mealeaten"
    _make_user(username, rows=_records_pattern("23:30", "07:30"), target=("23:00", "07:00"))
    _login(client, username)
    body = client.post(
        "/api/advice/checkin", json={"meal": "dinner", "eaten": True}
    ).json()
    assert body["ok"] is True and body["date"] == TODAY.isoformat()
    assert body["meal_plan"]["recent"][-1]["meals"]["dinner"]["state"] == "eaten"


def test_refused_breakfast_pulls_today_meals_earlier(client: TestClient) -> None:
    """早餐"不想吃"：算作没吃早餐，并把**今天**的午餐与晚餐各提前 60 分钟。

    只影响今天这一格（未来的计划日照旧），而且点"已吃"要能撤销；
    重新读计划时今天卡片与方案列表的第一行必须是同一份时间。
    """
    username = "refused"
    _make_user(username, rows=_records_pattern("00:30", "08:30"), target=("23:00", "07:00"))
    _login(client, username)

    base = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    base_lunch = parse_hhmm(base["today"]["meals"]["lunch"]["best_time"])
    base_dinner = parse_hhmm(base["today"]["meals"]["dinner"]["best_time"])
    tomorrow_dinner = parse_hhmm(base["plan"][1]["meals"]["dinner"]["best_time"])
    assert base["meal_plan"]["refused_breakfast_today"] is False

    body = client.post(
        "/api/advice/checkin",
        json={"meal": "breakfast", "eaten": False, "refused": True},
    ).json()
    assert body["refused"] is True
    assert body["meal_plan"]["refused_breakfast_today"] is True
    assert body["meal_plan"]["dinner_shift_minutes"] == advice_service.REFUSED_BREAKFAST_SHIFT
    assert body["meal_plan"]["recent"][-1]["meals"]["breakfast"]["state"] == "refused"
    # 打卡响应直接带回重算后的今天 + 时间图，前端不需要再拉一次整页
    assert body["today"] is not None and body["timeline"] is not None
    lunch = parse_hhmm(body["today"]["meals"]["lunch"]["best_time"])
    dinner = parse_hhmm(body["today"]["meals"]["dinner"]["best_time"])
    # 晚餐：精确提前 60 分钟；午餐：先按"没吃早餐"的阶梯前移 60 分钟，
    # 又因为午餐取的是"早餐~晚餐"的中点，而晚餐也提前了 60 → 中点再跟着走 30 分钟
    assert (base_dinner - dinner) % advice_service.DAY == advice_service.REFUSED_BREAKFAST_SHIFT
    lunch_moved = (base_lunch - lunch) % advice_service.DAY
    assert advice_service.REFUSED_BREAKFAST_SHIFT <= lunch_moved <= advice_service.LUNCH_ADVANCE_LADDER[-1]
    assert body["today"]["meals"]["dinner"]["shifted_minutes"] == advice_service.REFUSED_BREAKFAST_SHIFT
    # 提前之后"午→晚"的间隔仍要落在合理区间里（这也是当初改取中点的原因）
    gap = (dinner - lunch) % advice_service.DAY
    assert advice_service.LUNCH_TO_DINNER_MIN <= gap <= 8 * 60, gap

    # 重新读计划：叠加是"读取时"做的，方案列表第一行（同一天）必须和今天卡片一致
    state = client.get("/api/advice/plan").json()
    row = next(day for day in state["plan"]["plan"] if day["date"] == TODAY.isoformat())
    assert row["meals"]["dinner"]["best_time"] == state["today"]["meals"]["dinner"]["best_time"]
    assert row["meals"]["lunch"]["best_time"] == state["today"]["meals"]["lunch"]["best_time"]
    # 未来的计划日不受今天"不想吃"的影响
    future = next(
        day
        for day in state["plan"]["plan"]
        if day["date"] == (TODAY + timedelta(days=1)).isoformat()
    )
    assert parse_hhmm(future["meals"]["dinner"]["best_time"]) == tomorrow_dinner

    # 点"已吃"撤销：今天的三餐回到原样
    undone = client.post(
        "/api/advice/checkin", json={"meal": "breakfast", "eaten": True}
    ).json()
    assert undone["meal_plan"]["refused_breakfast_today"] is False
    assert undone["meal_plan"]["dinner_shift_minutes"] == 0
    assert parse_hhmm(undone["today"]["meals"]["lunch"]["best_time"]) == base_lunch
    assert parse_hhmm(undone["today"]["meals"]["dinner"]["best_time"]) == base_dinner

    # "不想吃"只对早餐有意义：午餐传 refused 要按普通"没吃"处理
    other = client.post(
        "/api/advice/checkin", json={"meal": "lunch", "eaten": False, "refused": True}
    ).json()
    assert other["refused"] is False
    assert other["meal_plan"]["refused_breakfast_today"] is False


# --------------------------------------------------------------------------- #
# 快照 / 偏离 / 重算
# --------------------------------------------------------------------------- #
def test_plan_snapshot_deviation_and_replan(client: TestClient) -> None:
    username = "deviate"
    user_id = _make_user(
        username, rows=_records_pattern("01:00", "09:00"), target=("23:00", "07:00")
    )
    _login(client, username)
    created = client.post("/api/advice", json={"mode": "slow", "days": 7}).json()
    original_target = created["plan_meta"]["target_date"]

    state = client.get("/api/advice/plan").json()
    assert state["has_plan"] is True
    assert state["plan_meta"]["plan_id"] == created["plan_meta"]["plan_id"]
    assert state["today"]["date"] == TODAY.isoformat()
    assert state["deviation"]["level"] == "none"
    assert state["adherence"]["days_recorded"] == 1
    assert state["adherence"]["hit_rate"] == 1.0

    # 今天睡了比计划晚 2 小时 → 触发 >90 分钟的询问
    with SessionLocal() as session:
        record = (
            session.query(SleepRecord)
            .filter(
                SleepRecord.user_id == user_id,
                SleepRecord.record_date == TODAY.isoformat(),
            )
            .one()
        )
        record.sleep_time = "03:00"
        record.wake_time = "11:00"
        session.commit()

    state = client.get("/api/advice/plan").json()
    assert state["deviation"]["level"] == "ask"
    assert state["deviation"]["minutes"] >= 90
    strategies = [option["strategy"] for option in state["deviation"]["options"]]
    assert strategies == ["postpone", "recompute", "keep"]
    assert state["deviation"]["options"][0]["recommended"] is True

    postponed = client.post("/api/advice/replan", json={"strategy": "postpone"}).json()
    assert postponed["plan_meta"]["postponed_days"] > 0
    assert postponed["plan_meta"]["replans"] == 1
    assert postponed["plan_meta"]["target_date"] > original_target

    kept = client.post("/api/advice/replan", json={"strategy": "keep"}).json()
    assert kept["plan_meta"]["target_date"] == postponed["plan_meta"]["target_date"]


def test_auto_recompute_only_once_per_day(client: TestClient) -> None:
    username = "autoreplan"
    user_id = _make_user(
        username, rows=_records_pattern("00:00", "08:00"), target=("23:00", "07:00")
    )
    _login(client, username)
    client.post("/api/advice", json={"mode": "slow", "days": 7})

    # 让快照看起来是昨天建立的，且今天偏离 60 分钟（落在"静默重算"区间）
    with SessionLocal() as session:
        record = (
            session.query(SleepRecord)
            .filter(SleepRecord.user_id == user_id)
            .order_by(SleepRecord.record_date.desc())
            .first()
        )
        record.sleep_time = "01:00"
        record.wake_time = "09:00"
        session.commit()

    state = client.get("/api/advice/plan").json()
    assert state["deviation"]["level"] == "auto"
    # 快照是今天生成的 → 不再自动重算（避免每次刷新都重写）
    assert state["auto_recomputed"] is False
    assert state["plan_meta"]["replans"] == 0


# --------------------------------------------------------------------------- #
# 统计（用轻量假对象，直接测纯函数）
# --------------------------------------------------------------------------- #
def _fake(record_date: str, sleep: str, wake: str) -> SimpleNamespace:
    return SimpleNamespace(record_date=record_date, sleep_time=sleep, wake_time=wake)


def _payload() -> dict:
    days = [
        {
            "index": index + 1,
            "date": (TODAY - timedelta(days=3 - index)).isoformat(),
            "sleep_time": "23:00",
            "wake_time": "07:00",
            "meals": {},
        }
        for index in range(3)
    ]
    return {"plan": days, "days": 3, "mode": "slow", "source": "custom_target"}


def test_adherence_ignores_unrecorded_days() -> None:
    payload = _payload()
    first, second, third = payload["plan"]
    records = [
        _fake(first["date"], "23:10", "07:05"),      # 命中
        _fake(third["date"], "23:20", "07:10"),      # 命中（中间那天没记录 → 不中断）
    ]
    stats = plan_service.adherence(payload, records, TODAY)
    assert stats["days_elapsed"] == 3
    assert stats["days_recorded"] == 2
    assert stats["hit_days"] == 2
    assert stats["hit_rate"] == 1.0
    assert stats["current_streak"] == 2
    assert stats["remaining_days"] == 0
    assert second["date"] not in {record.record_date for record in records}


def test_deviation_levels() -> None:
    payload = _payload()
    third = payload["plan"][-1]

    assert plan_service.deviation(payload, [], TODAY)["level"] == "none"

    ok = plan_service.deviation(payload, [_fake(third["date"], "23:25", "07:20")], TODAY)
    assert ok["level"] == "none" and ok["minutes"] == 25

    auto = plan_service.deviation(payload, [_fake(third["date"], "00:00", "08:00")], TODAY)
    assert auto["level"] == "auto" and auto["minutes"] == 60
    assert auto["direction"] == "later"
    assert auto["options"] == []

    ask = plan_service.deviation(payload, [_fake(third["date"], "01:00", "09:00")], TODAY)
    assert ask["level"] == "ask" and ask["minutes"] == 120
    assert [option["strategy"] for option in ask["options"]] == [
        "postpone",
        "recompute",
        "keep",
    ]
    assert ask["options"][0]["extra_days"] >= 1


# --------------------------------------------------------------------------- #
# 时间图
# --------------------------------------------------------------------------- #
def test_timeline_wraps_around_midnight() -> None:
    day = {
        "index": 1,
        "date": TODAY.isoformat(),
        "sleep_time": "01:30",
        "wake_time": "08:30",
        "duration_minutes": 420,
        "shift_sleep_minutes": 0,
        "shift_wake_minutes": 0,
        "step_minutes": 0,
        "meals": {
            "breakfast": {
                "enabled": True,
                "best_time": "08:45",
                "window_end": "09:00",
                "bar_minutes": 30,
                "shifted_minutes": 0,
            },
            "lunch": {
                "enabled": True,
                "best_time": "11:30",
                "window_end": "13:30",
                "bar_minutes": 60,
                "shifted_minutes": 0,
            },
            "dinner": {
                "enabled": False,
                "best_time": None,
                "bar_minutes": 60,
            },
        },
        "tips": [],
    }
    rendered = advice_service.day_for([day], TODAY)
    timeline = rendered["timeline"]
    assert timeline["axis_start"] == "06:30"
    assert timeline["axis_end"] == "03:30"
    assert timeline["sleep_label"] == "01:30–08:30"
    assert timeline["span_minutes"] == 21 * 60

    meals = [item for item in timeline["items"] if item["kind"] == "meal"]
    assert [item["meal"] for item in meals] == ["breakfast", "lunch"]
    night = [item for item in timeline["items"] if item.get("segment") == "night"][0]
    morning = [item for item in timeline["items"] if item.get("segment") == "morning"][0]
    assert (morning["start"], morning["end"]) == (0, 120)
    assert (night["start"], night["end"]) == (1140, 1260)
    # 餐次条目：start_time 直接取 best_time，条长由 bar_minutes 换算成分钟落在 [start, end)。
    # 注意条目里没有 bar_minutes 字段，所以只能断言 end - start —— 这里原来写的是
    # `assert meals[0]["start_time"] == "08:45" and meals[0]["bar_minutes"] if False else True`：
    # 被 `if False` 短路掉，实际什么都没断言（而且一旦求值会先 KeyError）。
    assert meals[0]["meal"] == "breakfast"
    assert meals[0]["start_time"] == "08:45"
    assert meals[0]["end_time"] == "09:00"
    assert meals[0]["end"] - meals[0]["start"] == 30
    assert meals[1]["meal"] == "lunch"
    assert meals[1]["start_time"] == "11:30"
    assert meals[1]["end"] - meals[1]["start"] == 60

    earlier = {**day, "date": (TODAY - timedelta(days=3)).isoformat()}
    assert advice_service.day_for([earlier, day], TODAY)["date"] == day["date"]
    assert advice_service.day_for([earlier], TODAY)["date"] == earlier["date"]
    assert advice_service.day_for([], TODAY) is None
