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
    assert options["steps"]["cap_minutes"] == 60
    assert options["steps"]["shape"] == {"alpha": 0.6, "power": 1.0}
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
    assert steps == advice_service._shape_steps(240, 10)
    assert all(later <= earlier + 1e-6 for earlier, later in zip(steps, steps[1:])), steps
    assert sum(steps) == 240
    assert steps[-1] > 0, "末日也要继续移动"
    assert steps[0] <= 1.3 * (240 / 10), "首日只比日均快一点，别一上来就猛推"
    assert body["plan"][-1]["sleep_time"] == "22:00"


def test_step_profile_is_smooth_and_keeps_speed_at_the_end() -> None:
    """纯函数：形状与天数、跨度无关；总和精确等于总偏移；末日仍在移动。"""
    steps, feasible = advice_service._step_magnitudes(240, 10, -1)
    assert feasible is True
    assert steps == advice_service._shape_steps(240, 10)
    assert sum(steps) == 240
    assert all(later <= earlier for earlier, later in zip(steps, steps[1:])), steps
    assert steps[-1] > 0, steps

    # 尾速恒定：末日保留首日约 62% 的速度，10 天和 30 天都是这一档
    ten = advice_service._shape_steps(300, 10)
    thirty = advice_service._shape_steps(300, 30)
    assert 0.55 <= ten[-1] / ten[0] <= 0.7 and 0.55 <= thirty[-1] / thirty[0] <= 0.7
    # 开头也不陡：首日只有日均的 1.23 倍左右
    assert 1.15 <= ten[0] / (300 / 10) <= 1.3

    # 天数不足只影响"是否提示"，不会把差额堆到最后一天（旧实现末日会跳 150 分钟）
    tight, ok = advice_service._step_magnitudes(240, 3, -1)
    assert ok is False
    assert tight == advice_service._shape_steps(240, 3) == [98, 80, 62]
    assert sum(tight) == 240
    assert advice_service.suggested_days(240, -1) == 5

    # 小偏移同样平滑落点，不再"集中到更少的天数"
    small, small_ok = advice_service._step_magnitudes(20, 3, 1)
    assert small_ok is True and sum(small) == 20 and all(step > 0 for step in small)


def test_last_step_minutes_reports_the_final_day(client: TestClient) -> None:
    """提示要报"最后一天挪多少"：这个数必须等于日卡上的 step_minutes（含不可行档）。"""
    _make_user("laststep", rows=_records_pattern("02:00", "10:00"), target=("22:00", "06:00"))
    _login(client, "laststep")
    body = client.post("/api/advice", json={"mode": "slow", "days": 10}).json()

    assert body["steps"]["last_step_minutes"] == body["plan"][-1]["step_minutes"]
    assert body["steps"]["last_step_minutes"] <= body["steps"]["cap_minutes"]

    # 天数不足：只是"某一步越过阈值"，末日不再被堆一个大数
    tight = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    assert tight["steps"]["feasible"] is False
    assert tight["steps"]["last_step_minutes"] == tight["plan"][-1]["step_minutes"]
    assert tight["steps"]["last_step_minutes"] < tight["steps"]["first_step_minutes"]
    assert tight["steps"]["first_step_minutes"] > advice_service.step_cap(-1)


def test_infeasible_notice_names_the_biggest_step(client: TestClient) -> None:
    """口径统一：不可行提示说清"最大一步 N 分钟、超过每日建议 C 分钟"。"""
    _make_user("tightnotice", rows=_records_pattern("03:00", "11:00"), target=("21:00", "05:00"))
    _login(client, "tightnotice")
    body = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()

    assert body["steps"]["feasible"] is False
    codes = {notice["code"]: notice for notice in body["notices"]}
    assert "advice.notice.infeasible" in codes
    notice = codes["advice.notice.infeasible"]
    assert notice["params"]["step"] == body["steps"]["max_step_minutes"]
    assert notice["params"]["step"] == max(day["step_minutes"] for day in body["plan"])
    assert notice["params"]["cap"] == body["steps"]["cap_minutes"]
    assert notice["params"]["step"] > notice["params"]["cap"]
    assert notice["params"]["suggested_days"] > len(body["plan"])


def test_both_directions_share_the_same_threshold(client: TestClient) -> None:
    """前后移现在共用一个每日阈值；方向只影响偏移符号，不影响曲线形状。"""
    assert advice_service.step_cap(-1) == advice_service.step_cap(1) == 60

    _make_user("delay", rows=_records_pattern("21:30", "05:30"), target=("01:30", "09:30"))
    _login(client, "delay")
    body = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()

    assert body["steps"]["direction"] == "delay"
    assert body["steps"]["cap_minutes"] == 60
    steps = [day["step_minutes"] for day in body["plan"]]
    assert all(later <= earlier for earlier, later in zip(steps, steps[1:])), steps
    assert body["steps"]["daily_average_minutes"] > 0


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


def test_today_breakfast_skip_shifts_lunch_and_dinner(client: TestClient) -> None:
    """今天早餐没吃 → 今天午/晚各提前 60 分钟；明天的早餐不受影响。"""
    username = "mealshift"
    _make_user(username, rows=_records_pattern("00:30", "08:30"), target=("23:00", "07:00"))
    _login(client, username)

    base = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    assert base["meal_plan"]["today"]["breakfast"] == "unknown"

    body = client.post("/api/advice/checkin", json={"meal": "breakfast", "eaten": False}).json()
    assert body["ok"] is True
    assert body["meal_plan"]["today"]["breakfast"] == "skipped"

    shifted = client.post("/api/advice", json={"mode": "slow", "days": 3}).json()
    today = shifted["plan"][0]
    assert today["meals"]["lunch"]["shifted_minutes"] == 60
    assert today["meals"]["lunch"]["shift_reason"] == "breakfast"
    assert today["meals"]["dinner"]["shifted_minutes"] == 60
    assert today["meals"]["dinner"]["shift_reason"] == "breakfast"
    assert shifted["plan"][1]["meals"]["breakfast"]["shifted_minutes"] == 0
    # 午餐仍受“起床后至少 2 小时”和现实窗口约束
    wake = parse_hhmm(today["wake_time"])
    lunch = parse_hhmm(today["meals"]["lunch"]["best_time"])
    assert (lunch - wake) % advice_service.DAY >= advice_service.LUNCH_FLOOR_AFTER_WAKE
    if wake <= advice_service.NORMAL_WAKE_UNTIL:
        assert lunch >= advice_service.LUNCH_NORMAL_WINDOW[0]

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


def test_meals_shift_only_from_yesterday_and_today(client: TestClient) -> None:
    """三餐只按昨天与当天移动：昨天晚餐影响今天早餐，今天各餐影响今天午晚与明天早餐。"""
    username = "mealrules"
    _make_user(username, rows=_records_pattern("00:30", "08:30"), target=("23:00", "07:00"))
    _login(client, username)

    base = client.post("/api/advice", json={"mode": "slow", "days": 4}).json()
    assert base["meal_plan"]["today"]["breakfast"] == "unknown"

    # 昨天晚餐没吃 → 今天早餐提前，并被“起床后 5 分钟”的地板卡住
    client.post(
        "/api/advice/checkin",
        json={"meal": "dinner", "eaten": False, "date": (TODAY - timedelta(days=1)).isoformat()},
    )
    marked = client.post("/api/advice", json={"mode": "slow", "days": 4}).json()
    today = marked["plan"][0]
    breakfast = today["meals"]["breakfast"]
    assert breakfast["shift_reason"] == "yesterday_dinner"
    assert breakfast["shifted_minutes"] == max(
        0,
        min(
            advice_service.MEAL_EARLY_AFTER_SKIP["dinner"],
            advice_service.BREAKFAST_AFTER_WAKE[0] - advice_service.BREAKFAST_FLOOR_AFTER_WAKE,
        ),
    )
    assert today["meals"]["lunch"]["shifted_minutes"] == 0
    assert today["meals"]["dinner"]["shifted_minutes"] == 0
    assert marked["plan"][1]["meals"]["breakfast"]["shifted_minutes"] == 0

    # 今天早餐没吃 → 今天午餐与晚餐各提前 60
    client.post("/api/advice/checkin", json={"meal": "breakfast", "eaten": False})
    after = client.post("/api/advice", json={"mode": "slow", "days": 4}).json()
    day = after["plan"][0]
    assert day["meals"]["lunch"]["shifted_minutes"] == 60
    assert day["meals"]["lunch"]["shift_reason"] == "breakfast"
    assert day["meals"]["dinner"]["shifted_minutes"] == 60
    assert day["meals"]["dinner"]["shift_reason"] == "breakfast"
    assert after["plan"][1]["meals"]["breakfast"]["shifted_minutes"] == 0

    # 今天午餐也没吃 → 晚餐叠加到 90（60 + 30）封顶
    client.post("/api/advice/checkin", json={"meal": "lunch", "eaten": False})
    stacked = client.post("/api/advice", json={"mode": "slow", "days": 4}).json()
    dinner = stacked["plan"][0]["meals"]["dinner"]
    assert dinner["shifted_minutes"] == advice_service.DINNER_MAX_EARLY
    assert dinner["shift_reason"] == "breakfast_and_lunch"

    # 今天晚餐没吃 → 明天早餐提前 30，再往后没有数据所以不动
    client.post("/api/advice/checkin", json={"meal": "dinner", "eaten": False})
    nextday = client.post("/api/advice", json={"mode": "slow", "days": 4}).json()
    assert nextday["plan"][1]["meals"]["breakfast"]["shift_reason"] == "yesterday_dinner"
    assert nextday["plan"][2]["meals"]["breakfast"]["shifted_minutes"] == 0
    assert nextday["meal_plan"]["today"]["breakfast"] == "skipped"

def test_old_refused_flag_behaves_like_skipped(client: TestClient) -> None:
    """「不想吃」按钮下线了，但库里老的 refused 行必须与 skipped 等价。"""
    username = "refusedold"
    _make_user(username, rows=_records_pattern("00:30", "08:30"), target=("23:00", "07:00"))
    _login(client, username)
    client.post("/api/advice", json={"mode": "slow", "days": 3})

    body = client.post(
        "/api/advice/checkin",
        json={"meal": "breakfast", "eaten": False, "refused": True},
    ).json()
    assert body["ok"] is True
    assert body["meal_plan"]["today"]["breakfast"] == "skipped"
    day = body["today"]
    assert day["meals"]["lunch"]["shifted_minutes"] == 60
    assert day["meals"]["dinner"]["shifted_minutes"] == 60
    assert "refusal_preview" not in day

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
def _fake(record_date: str, sleep: str, wake: str, wake_date: str | None = None) -> SimpleNamespace:
    """轻量假记录：wake_date 默认 None，正好走"老记录按时刻推导起床日"的回退路径。"""
    return SimpleNamespace(
        record_date=record_date, sleep_time=sleep, wake_time=wake, wake_date=wake_date
    )


def _night(card_date: str, sleep: str, wake: str) -> SimpleNamespace:
    """一条"前一晚睡、card_date 早上起"的记录：挂在 card_date 这张卡片上。

    新约定的表单就是这样填的（入睡日期 = 昨天，起床日期 = 今天 = 卡片日）。
    """
    return _fake(
        (date.fromisoformat(card_date) - timedelta(days=1)).isoformat(),
        sleep,
        wake,
        wake_date=card_date,
    )


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
        _night(first["date"], "23:10", "07:05"),      # 命中
        _night(third["date"], "23:20", "07:10"),      # 命中（中间那天没记录 → 不中断）
    ]
    stats = plan_service.adherence(payload, records, TODAY)
    assert stats["days_elapsed"] == 3
    assert stats["days_recorded"] == 2
    assert stats["hit_days"] == 2
    assert stats["hit_rate"] == 1.0
    assert stats["current_streak"] == 2
    assert stats["remaining_days"] == 0
    assert second["date"] not in {advice_service.wake_day_of(record) for record in records}


def test_deviation_levels() -> None:
    payload = _payload()
    third = payload["plan"][-1]

    assert plan_service.deviation(payload, [], TODAY)["level"] == "none"

    ok = plan_service.deviation(payload, [_night(third["date"], "23:25", "07:20")], TODAY)
    assert ok["level"] == "none" and ok["minutes"] == 25

    auto = plan_service.deviation(payload, [_night(third["date"], "00:00", "08:00")], TODAY)
    assert auto["level"] == "auto" and auto["minutes"] == 60
    assert auto["direction"] == "later"
    assert auto["options"] == []

    ask = plan_service.deviation(payload, [_night(third["date"], "01:00", "09:00")], TODAY)
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
    # 两截睡眠都属于"同一觉"，各自带 partial 标记与前/后段的时间区间：
    # 前端靠 segment 把它们标成"睡眠前段/后段"，并在轴徽标里写"两端相接"
    # （用户看时间图时以为睡了两觉）。改这里就要同步改 frontend/shared/timeline.js。
    assert {item["segment"]: item["partial"] for item in timeline["items"] if item["kind"] == "sleep"} == {
        "morning": True,
        "night": True,
    }
    assert (morning["start_time"], morning["end_time"]) == ("06:30", "08:30")
    assert (night["start_time"], night["end_time"]) == ("01:30", "03:30")
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


# --------------------------------------------------------------------------- #
# 两个日期：入睡日 + 起床日
# --------------------------------------------------------------------------- #
def test_wake_date_defaults_to_the_next_day(client: TestClient) -> None:
    """只给入睡日期时，起床日按"起床时刻 <= 入睡时刻 → 次日"推导（跨夜 8 小时）。"""
    username = "wakedefault"
    _make_user(username, target=("23:00", "07:00"))
    _login(client, username)
    yesterday = (TODAY - timedelta(days=1)).isoformat()

    body = client.post(
        "/api/records",
        json={"record_date": yesterday, "sleep_time": "23:30", "wake_time": "07:30"},
    ).json()
    assert body["record_date"] == yesterday
    assert body["wake_date"] == TODAY.isoformat()
    assert body["duration_minutes"] == 8 * 60


def test_wake_date_can_be_the_same_day(client: TestClient) -> None:
    """午睡/凌晨睡早上起：起床日可以就是入睡日，时长按当天算。"""
    username = "wakesame"
    _make_user(username, target=("23:00", "07:00"))
    _login(client, username)
    day = (TODAY - timedelta(days=1)).isoformat()

    body = client.post(
        "/api/records",
        json={
            "record_date": day,
            "wake_date": day,
            "sleep_time": "01:30",
            "wake_time": "08:30",
        },
    ).json()
    assert body["wake_date"] == day
    assert body["duration_minutes"] == 7 * 60


def test_wake_date_range_and_order_are_validated(client: TestClient) -> None:
    """起床日最远只能是次日；起床时刻必须晚于入睡时刻（同一天时尤其要挡住）。"""
    username = "wakebad"
    _make_user(username, target=("23:00", "07:00"))
    _login(client, username)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    tomorrow = (TODAY + timedelta(days=1)).isoformat()

    far = client.post(
        "/api/records",
        json={
            "record_date": yesterday,
            "wake_date": tomorrow,
            "sleep_time": "23:30",
            "wake_time": "07:30",
        },
    )
    assert far.status_code == 400
    assert far.json()["code"] == "error.invalid_date_range"

    same_times = client.post(
        "/api/records",
        json={
            "record_date": yesterday,
            "wake_date": yesterday,
            "sleep_time": "07:30",
            "wake_time": "07:30",
        },
    )
    assert same_times.status_code == 400
    assert same_times.json()["code"] == "error.invalid_time_range"


def test_meals_belong_to_the_meal_date(client: TestClient) -> None:
    """记录页勾的三餐按“三餐日期”归档：跨夜那晚的三餐属于入睡日，不是起床日。"""
    username = "mealday"
    _make_user(username, target=("23:00", "07:00"))
    _login(client, username)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    created = client.post(
        "/api/records",
        json={
            "record_date": yesterday,
            "wake_date": TODAY.isoformat(),
            "sleep_time": "23:30",
            "wake_time": "07:30",
            "meal_lunch": False,
        },
    ).json()
    assert created["meal_date"] == yesterday

    state = client.get("/api/advice/plan").json()
    days = {item["date"]: item["meals"] for item in state["meal_plan"]["days"]}
    assert days[yesterday]["lunch"]["state"] == "skipped"
    assert days[TODAY.isoformat()]["lunch"]["state"] == "unknown"

def test_checkin_writes_back_to_the_meal_day_record(client: TestClient) -> None:
    """打卡回写落在「三餐所属日」（meal_date）那条记录上，不是起床日那条。"""
    username = "wakeback"
    _make_user(username, target=("23:00", "07:00"))
    _login(client, username)
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    # 今天这一夜：入睡日=今天、起床日=明天 → 三餐记录属于今天
    record_id = client.post(
        "/api/records",
        json={
            "record_date": TODAY.isoformat(),
            "wake_date": tomorrow,
            "sleep_time": "23:30",
            "wake_time": "07:30",
        },
    ).json()["id"]
    # 昨晚那一夜：入睡日=昨天、起床日=今天 → 三餐记录属于昨天，今天的打卡不该动它
    other_id = client.post(
        "/api/records",
        json={
            "record_date": yesterday,
            "wake_date": TODAY.isoformat(),
            "sleep_time": "23:30",
            "wake_time": "07:30",
        },
    ).json()["id"]

    body = client.post("/api/advice/checkin", json={"meal": "breakfast", "eaten": False}).json()
    assert body["ok"] is True

    rows = {item["id"]: item for item in client.get("/api/records").json()["items"]}
    assert rows[record_id]["meal_breakfast"] is False
    assert rows[record_id]["meal_date"] == TODAY.isoformat()
    assert rows[other_id]["meal_breakfast"] is True
    assert rows[other_id]["meal_date"] == yesterday


def test_meal_date_defaults_from_the_wake_date(client: TestClient) -> None:
    """三餐日期：跨夜记录=入睡日；同日入睡（凌晨）时=入睡日−1；显式传就照传（限相邻两天）。"""
    username = "mealdates"
    _make_user(username)
    _login(client, username)
    yesterday = (TODAY - timedelta(days=1)).isoformat()

    crossing = client.post(
        "/api/records",
        json={
            "record_date": yesterday,
            "wake_date": TODAY.isoformat(),
            "sleep_time": "23:30",
            "wake_time": "07:30",
        },
    ).json()
    assert crossing["meal_date"] == yesterday

    same_day = client.post(
        "/api/records",
        json={"record_date": yesterday, "sleep_time": "00:30", "wake_time": "08:30"},
    ).json()
    assert same_day["wake_date"] == yesterday
    assert same_day["meal_date"] == (TODAY - timedelta(days=2)).isoformat()

    # 同日入睡默认是“入睡日−1”，显式写入睡日本身也应该被接受
    explicit = client.post(
        "/api/records",
        json={
            "record_date": (TODAY - timedelta(days=2)).isoformat(),
            "sleep_time": "00:30",
            "wake_time": "08:30",
            "meal_date": (TODAY - timedelta(days=2)).isoformat(),
        },
    ).json()
    assert explicit["meal_date"] == (TODAY - timedelta(days=2)).isoformat()

    bad = client.post(
        "/api/records",
        json={
            "record_date": yesterday,
            "wake_date": TODAY.isoformat(),
            "sleep_time": "23:30",
            "wake_time": "07:30",
            "meal_date": (TODAY - timedelta(days=3)).isoformat(),
        },
    )
    assert bad.status_code == 400
    assert "invalid_meal_date" in str(bad.json())

def test_basis_weights_decay_by_calendar_distance():
    """权重只看日历距离：每早一天 ×0.8；缺记录的那天不占权。"""
    today = date(2026, 10, 20)
    records = [
        _fake("2026-10-13", "23:00", "07:00"),   # 7 天前
        _fake("2026-10-18", "23:00", "07:00"),   # 2 天前（10-19 那天缺）
        _fake("2026-10-20", "23:00", "07:00"),   # 今天
    ]
    weights = advice_service._basis_weights(records, today=today)
    assert weights[0] == pytest.approx(0.8 ** 7)
    assert weights[1] == pytest.approx(0.8 ** 2)
    assert weights[2] == pytest.approx(1.0)


def test_basis_leans_on_the_recent_nights():
    """同一条「熬夜」记录，放在最近的夜晚比放在最早那天更能拉动均值。"""
    today = date(2026, 10, 20)

    def mean_with(spike_on_oldest: bool) -> int:
        records = []
        for offset in range(0, 7):
            spike = (offset == 6) if spike_on_oldest else (offset == 0)
            sleep = "03:00" if spike else "23:00"
            records.append(_fake((today - timedelta(days=offset)).isoformat(), sleep, "07:00"))
        return advice_service._basis(records, today=today)["sleep_minutes"]

    recent_spike = mean_with(spike_on_oldest=False)
    oldest_spike = mean_with(spike_on_oldest=True)
    # 环形距离（跨零点）才算得对：03:00 与 23:00 隔着午夜。
    assert abs(advice_service.circular_diff_minutes(recent_spike, 3 * 60)) < abs(
        advice_service.circular_diff_minutes(oldest_spike, 3 * 60)
    )


def test_naps_stay_out_of_the_basis():
    """时长不足 2 小时的记录算小睡：不进平均入睡/起床、不进平均时长。"""
    today = date(2026, 10, 20)
    nights = [
        _fake(
            (today - timedelta(days=offset)).isoformat(),
            "23:30",
            "07:30",
            wake_date=(today - timedelta(days=offset - 1)).isoformat(),
        )
        for offset in range(5)
    ]
    nap = _fake((today - timedelta(days=2)).isoformat(), "14:00", "14:30")

    plain = advice_service._basis(nights, today=today)
    with_nap = advice_service._basis(nights + [nap], today=today)

    assert plain["naps"] == 0
    assert with_nap["naps"] == 1
    assert with_nap["sample_size"] == plain["sample_size"] == 5
    assert with_nap["sleep_minutes"] == plain["sleep_minutes"]
    assert with_nap["wake_minutes"] == plain["wake_minutes"]
    assert with_nap["duration_minutes"] == plain["duration_minutes"]

    only_naps = advice_service._basis([nap], today=today)
    assert only_naps["sample_size"] == 0
    assert only_naps["naps"] == 1
