"""时间工具：统一 "HH:MM" 字符串与"距零点分钟数"之间的转换。

睡眠跨零点是常态（23:30 → 07:10），因此内部计算一律使用分钟偏移：
  * 入睡时间映射到当天 0..1439；
  * 起床时间若 <= 入睡时间，视为次日，+1440 归一化。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

TIME_FMT_LEN = 5


class TimeFormatError(ValueError):
    pass


def parse_hhmm(value: str) -> int:
    """"HH:MM" -> 距零点分钟数。格式非法抛 TimeFormatError。"""
    if not isinstance(value, str):
        raise TimeFormatError("time must be a string")
    text = value.strip()
    if len(text) != TIME_FMT_LEN or text[2] != ":":
        raise TimeFormatError(f"invalid time format: {value!r}")
    hh, mm = text[:2], text[3:]
    if not (hh.isdigit() and mm.isdigit()):
        raise TimeFormatError(f"invalid time format: {value!r}")
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TimeFormatError(f"time out of range: {value!r}")
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    """距零点分钟数 -> "HH:MM"（自动对 1440 取模）。"""
    total = int(minutes) % (24 * 60)
    if total < 0:
        total += 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def normalize_span(sleep_minutes: int, wake_minutes: int) -> tuple[int, int]:
    """把入睡/起床归一化到同一条时间轴；起床早于或等于入睡视为次日。"""
    wake = wake_minutes
    if wake <= sleep_minutes:
        wake += 24 * 60
    return sleep_minutes, wake


def sleep_duration_minutes(sleep_minutes: int, wake_minutes: int) -> int:
    """睡眠时长（分钟）。"""
    _, wake = normalize_span(sleep_minutes, wake_minutes)
    return wake - sleep_minutes


def circular_diff_minutes(a: int, b: int) -> int:
    """两个钟点之间的最短有向差（a → b），范围 (-720, 720]。"""
    diff = (b - a) % (24 * 60)
    if diff > 12 * 60:
        diff -= 24 * 60
    return diff


def circular_mean_minutes(values: list[int], weights: list[float] | None = None) -> int:
    """圆环均值，用于求平均入睡/起床时间（避免 23:50 与 00:10 平均成 12:00）。"""
    if not values:
        return 0
    import math

    sin_sum = 0.0
    cos_sum = 0.0
    for index, value in enumerate(values):
        weight = 1.0 if weights is None else float(weights[index])
        angle = 2 * math.pi * (value % (24 * 60)) / (24 * 60)
        sin_sum += weight * math.sin(angle)
        cos_sum += weight * math.cos(angle)
    if abs(sin_sum) < 1e-9 and abs(cos_sum) < 1e-9:
        return int(sum(values) / len(values))
    angle = math.atan2(sin_sum, cos_sum)
    if angle < 0:
        angle += 2 * math.pi
    return int(round(angle * (24 * 60) / (2 * math.pi))) % (24 * 60)


def iso_date(value: date | datetime | str) -> str:
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def clamp_minutes(value: int, low: int, high: int) -> int:
    """把钟点夹到 [low, high] 区间；若区间跨零点则按环处理。"""
    if low <= high:
        return max(low, min(high, value))
    # 跨零点区间，例如 22:00 ~ 02:00
    if value >= low or value <= high:
        return value
    return low if circular_diff_minutes(value, low) <= circular_diff_minutes(value, high) else high


def month_shift(value: datetime, months: int) -> datetime:
    """向前/向后平移若干个月（用于 6 个月保留策略）。"""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day
