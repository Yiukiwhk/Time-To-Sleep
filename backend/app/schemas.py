"""Pydantic 请求/响应模型（第一层输入校验：类型、范围、长度）。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import settings
from .core.timing import TimeFormatError, parse_hhmm

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Language = Literal["zh", "en"]
Theme = Literal["dark", "light"]
AdjustMode = Literal["fast", "slow"]


def _validate_username(value: str) -> str:
    value = (value or "").strip()
    if not (settings.MIN_USERNAME_LEN <= len(value) <= settings.MAX_USERNAME_LEN):
        raise ValueError("error.username_length")
    if not USERNAME_RE.match(value):
        raise ValueError("error.username_charset")
    return value


def _validate_time(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    if not TIME_RE.match(text):
        raise ValueError("error.invalid_time")
    try:
        parse_hhmm(text)
    except TimeFormatError as exc:  # pragma: no cover - 正则已拦截
        raise ValueError("error.invalid_time") from exc
    return text


def _validate_password(value: str) -> str:
    if len(value) < settings.MIN_PASSWORD_LEN:
        raise ValueError("error.password_too_short")
    if len(value) > settings.MAX_PASSWORD_LEN:
        raise ValueError("error.password_too_long")
    return value


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=settings.MAX_USERNAME_LEN)
    password: str = Field(min_length=1, max_length=settings.MAX_PASSWORD_LEN)
    remember: bool = False

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return _validate_username(value)


class AdminLoginRequest(BaseModel):
    """管理员多重密码登录：多段密码全部校验通过才可进入后台。"""

    username: str = Field(min_length=1, max_length=settings.MAX_USERNAME_LEN)
    passwords: list[str] = Field(default_factory=list, max_length=8)
    remember: bool = False

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return _validate_username(value)

    @field_validator("passwords")
    @classmethod
    def _check_passwords(cls, value: list[str]) -> list[str]:
        cleaned = [str(item) for item in value]
        for item in cleaned:
            if len(item) > settings.MAX_PASSWORD_LEN:
                raise ValueError("error.password_too_long")
        return cleaned


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=settings.MAX_PASSWORD_LEN)
    new_password: str = Field(min_length=1, max_length=settings.MAX_PASSWORD_LEN)

    @field_validator("new_password")
    @classmethod
    def _check_new(cls, value: str) -> str:
        return _validate_password(value)


class UserPublic(ORMModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    language: Language
    theme: Theme
    target_sleep_time: str | None = None
    target_wake_time: str | None = None
    meal_breakfast: bool
    meal_lunch: bool
    meal_dinner: bool
    created_at: datetime
    last_login_at: datetime | None = None


# --------------------------------------------------------------------------- #
# 睡眠记录
# --------------------------------------------------------------------------- #
class SleepRecordBase(BaseModel):
    sleep_time: str
    wake_time: str
    meal_breakfast: bool = True
    meal_lunch: bool = True
    meal_dinner: bool = True
    record_date: str | None = None
    # 起床日历日。不传时服务端按"起床时刻 <= 入睡时刻 → 次日"推导（见 api/records.py）。
    wake_date: str | None = None
    # 三餐记录所属日。不传时服务端补默认值：入睡日；入睡与醒来同一天（跨零点入睡）则前移一天。
    meal_date: str | None = None
    note: str | None = Field(default=None, max_length=settings.MAX_NOTE_LEN)

    @field_validator("sleep_time", "wake_time")
    @classmethod
    def _check_required_time(cls, value: str) -> str:
        result = _validate_time(value)
        if result is None:
            raise ValueError("error.invalid_time")
        return result

    @field_validator("record_date", "wake_date", "meal_date")
    @classmethod
    def _check_date(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        text = str(value).strip()
        if not DATE_RE.match(text):
            raise ValueError("error.invalid_date")
        try:
            datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("error.invalid_date") from exc
        return text

    @field_validator("note")
    @classmethod
    def _check_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class SleepRecordCreate(SleepRecordBase):
    pass


class SleepRecordUpdate(BaseModel):
    sleep_time: str | None = None
    wake_time: str | None = None
    meal_breakfast: bool | None = None
    meal_lunch: bool | None = None
    meal_dinner: bool | None = None
    record_date: str | None = None
    wake_date: str | None = None
    meal_date: str | None = None
    note: str | None = Field(default=None, max_length=settings.MAX_NOTE_LEN)

    @field_validator("sleep_time", "wake_time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        result = _validate_time(value)
        if value is not None and result is None:
            raise ValueError("error.invalid_time")
        return result

    @field_validator("record_date", "wake_date", "meal_date")
    @classmethod
    def _check_date(cls, value: str | None) -> str | None:
        return SleepRecordBase._check_date(value)

    @field_validator("note")
    @classmethod
    def _check_note(cls, value: str | None) -> str | None:
        return SleepRecordBase._check_note(value)


class SleepRecordOut(ORMModel):
    id: int
    user_id: int
    sleep_time: str
    wake_time: str
    meal_breakfast: bool
    meal_lunch: bool
    meal_dinner: bool
    record_date: str
    wake_date: str | None = None
    meal_date: str | None = None
    note: str | None = None
    duration_minutes: int = 0
    created_at: datetime
    updated_at: datetime
    # 仅管理员视图填充
    username: str | None = None


class SleepRecordListOut(BaseModel):
    items: list[SleepRecordOut]
    total: int
    page: int
    page_size: int
    pages: int
    stats: dict | None = None


class IdListRequest(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("ids")
    @classmethod
    def _check_ids(cls, value: list[int]) -> list[int]:
        cleaned = []
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
                raise ValueError("error.invalid_id")
            cleaned.append(item)
        return cleaned


# --------------------------------------------------------------------------- #
# 作息建议
# --------------------------------------------------------------------------- #
class AdviceRequest(BaseModel):
    mode: AdjustMode = "fast"
    days: int | None = Field(default=None, ge=1, le=30)
    use_target: bool = True
    meal_breakfast: bool | None = None
    meal_lunch: bool | None = None
    meal_dinner: bool | None = None
    save: bool = True          # 生成后是否保存为"当前计划"快照


class AdviceCheckinRequest(BaseModel):
    """建议页三餐卡片上的"已吃 / 跳过 / 不想吃"一键确认。"""

    meal: Literal["breakfast", "lunch", "dinner"]
    eaten: bool
    # "不想吃"：只有早餐会用到（前端只在早餐卡片给这个按钮）。它必然是没吃，
    # 并且会让今天的午餐与晚餐都提前 60 分钟；点已吃/跳过时前端会传 False 清掉它
    refused: bool = False
    date: str | None = Field(default=None, max_length=10)


class AdviceNoticeAckRequest(BaseModel):
    """账号级"我已知晓，不再提示"。"""

    notice: str = Field(min_length=3, max_length=64)


class AdviceReplanRequest(BaseModel):
    """偏离过大时弹窗里的三种选择。"""

    strategy: Literal["postpone", "recompute", "keep"]


class MealPlan(BaseModel):
    enabled: bool
    time: str | None = None                 # 兼容字段：等于 best_time
    best_time: str | None = None            # 最佳建议用餐时间（窗口起点）
    window_start: str | None = None
    window_end: str | None = None
    bar_minutes: int | None = None          # 时间图上的条长（仅视觉）
    # 午餐取早餐与晚餐的中点，所以它的锚点是两端（wake / sleep / both）
    anchor: Literal["wake", "sleep", "both"] | None = None
    shifted_minutes: int = 0                # 因未吃早餐而前移的分钟数
    note: str | None = None                 # i18n 键
    # 这一餐为什么提前：yesterday_dinner / breakfast / lunch / breakfast_and_lunch
    shift_reason: str | None = None


class DayPlan(BaseModel):
    index: int
    date: str
    sleep_time: str
    wake_time: str
    duration_minutes: int
    shift_sleep_minutes: int
    shift_wake_minutes: int
    step_minutes: int = 0
    meals: dict[str, MealPlan]
    timeline: dict | None = None
    tips: list[str] = Field(default_factory=list)


class AdviceResponse(BaseModel):
    mode: AdjustMode
    days: int
    source: Literal["custom_target", "system"]
    basis: dict
    target: dict
    steps: dict | None = None
    meal_plan: dict | None = None
    warnings: list[str] = Field(default_factory=list)
    notices: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    plan: list[DayPlan]
    today: DayPlan | None = None
    timeline: dict | None = None
    plan_meta: dict | None = None
    generated_at: datetime
    engine: str = "adaptive-v2"


# --------------------------------------------------------------------------- #
# 个性化设置
# --------------------------------------------------------------------------- #
class PreferenceUpdate(BaseModel):
    language: Language | None = None
    theme: Theme | None = None
    meal_breakfast: bool | None = None
    meal_lunch: bool | None = None
    meal_dinner: bool | None = None


class TargetScheduleUpdate(BaseModel):
    """自定义目标作息；传 null / 空字符串表示清除并恢复系统推荐。"""

    target_sleep_time: str | None = None
    target_wake_time: str | None = None

    @field_validator("target_sleep_time", "target_wake_time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        return _validate_time(value)

    model_config = ConfigDict(extra="forbid")


class ProfileUpdate(PreferenceUpdate):
    pass


# --------------------------------------------------------------------------- #
# 注销申请
# --------------------------------------------------------------------------- #
class DeletionRequestCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class DeletionRequestOut(ORMModel):
    id: int
    user_id: int | None = None
    username: str | None = None
    status: str
    reason: str | None = None
    requested_at: datetime
    handled_at: datetime | None = None
    handled_by: int | None = None
    handled_by_username: str | None = None
    handle_note: str | None = None


# --------------------------------------------------------------------------- #
# 管理后台
# --------------------------------------------------------------------------- #
class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=settings.MAX_USERNAME_LEN)
    password: str = Field(min_length=1, max_length=settings.MAX_PASSWORD_LEN)
    is_admin: bool = False
    language: Language = "zh"
    theme: Theme = "dark"
    admin_passwords: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return _validate_username(value)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password(value)


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    language: Language | None = None
    theme: Theme | None = None


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=1, max_length=settings.MAX_PASSWORD_LEN)

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password(value)


class AdminUserOut(ORMModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    language: Language
    theme: Theme
    target_sleep_time: str | None = None
    target_wake_time: str | None = None
    failed_login_count: int
    locked_until: datetime | None = None
    locked: bool = False
    created_at: datetime
    last_login_at: datetime | None = None
    last_login_ip: str | None = None
    record_count: int = 0


class AdminUserListOut(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int
    pages: int


class DeletionDecision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def _check_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class DeletionRequestListOut(BaseModel):
    items: list[DeletionRequestOut]
    total: int
    page: int
    page_size: int
    pages: int


class SystemLogOut(ORMModel):
    id: int
    level: str
    event: str
    message: str
    user_id: int | None = None
    username: str | None = None
    ip: str | None = None
    path: str | None = None
    created_at: datetime


class SystemLogListOut(BaseModel):
    items: list[SystemLogOut]
    total: int
    page: int
    page_size: int
    pages: int
    level_counts: dict[str, int] = Field(default_factory=dict)


class MessageOut(BaseModel):
    ok: bool = True
    message: str = "ok"
    extra: dict | None = None
