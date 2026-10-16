"""SQLAlchemy ORM 模型。

全部使用 ORM 定义，禁止手写拼接 SQL（防注入）。
用户被删除时，关联数据通过 ondelete="CASCADE" 级联清除。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """统一使用 UTC naive 时间入库（展示时由前端本地化）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 自定义目标作息（用户设置后取代系统推荐作息）
    target_sleep_time: Mapped[str | None] = mapped_column(String(5), nullable=True)   # "HH:MM"
    target_wake_time: Mapped[str | None] = mapped_column(String(5), nullable=True)    # "HH:MM"

    # 个性化设置
    language: Mapped[str] = mapped_column(String(8), default="zh", nullable=False)     # zh | en
    theme: Mapped[str] = mapped_column(String(8), default="dark", nullable=False)      # dark | light

    # 三餐建议开关（默认全部开启）
    meal_breakfast: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meal_lunch: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meal_dinner: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 登录安全
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 写入节流（30 秒防重复提交）
    last_write_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 建议页提示的"我已知晓，不再提示"记录（逗号分隔的提示代码）
    advice_notice_ack: Mapped[str | None] = mapped_column(String(64), nullable=True)

    records: Mapped[list["SleepRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    meal_checks: Mapped[list["MealCheck"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    advice_plans: Mapped[list["AdvicePlan"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    # 只读视图：删除账号时由业务代码显式保留审计记录（把 user_id 置空），
    # 因此这里不使用 delete-orphan，避免 SQLAlchemy 误删审核历史。
    deletion_requests: Mapped[list["DeletionRequest"]] = relationship(
        back_populates="user",
        foreign_keys="DeletionRequest.user_id",
        viewonly=True,
    )


class AdminCredential(Base, TimestampMixin):
    """管理员多重密码登录所需的额外密码段（分段 bcrypt 存储）。"""

    __tablename__ = "admin_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_index: Mapped[int] = mapped_column(Integer, nullable=False)   # 从 1 开始
    part_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "part_index", name="uq_admin_part"),)


class SleepRecord(Base, TimestampMixin):
    """睡眠记录。时间字段统一存 "HH:MM"，日期字段存 "YYYY-MM-DD"。

    record_date 是入睡日历日，wake_date 是起床日历日（表单默认 = 入睡日 + 1）。
    老库里的 wake_date 是迁移时按"起床时刻 <= 入睡时刻 → 次日"补出来的；
    读取时若该列为空（例如迁移前就存在的行、或直接写库的测试数据）仍按同一规则推导，
    见 core/timing.py 的 derive_wake_date()。
    """

    __tablename__ = "sleep_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sleep_time: Mapped[str] = mapped_column(String(5), nullable=False)          # 入睡时间
    wake_time: Mapped[str] = mapped_column(String(5), nullable=False)           # 起床时间
    # 曾经有 latest_wake_time / earliest_sleep_time 两列（最晚起床 / 最早入睡约束），
    # 这两个功能已按用户要求删除：模型与接口都不再认它们，老库里的列留着但没人读写。
    meal_breakfast: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meal_lunch: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meal_dinner: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    record_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    # 起床日历日。老库补列时没有 NOT NULL 默认值，所以这里可空；新记录一律写入。
    wake_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)  # YYYY-MM-DD
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped["User"] = relationship(back_populates="records")

    __table_args__ = (
        Index("ix_sleep_records_user_date", "user_id", "record_date"),
    )


class MealCheck(Base, TimestampMixin):
    """三餐实际执行情况（建议页"已吃 / 跳过 / 不想吃"一键确认）。

    sleep_records.meal_* 是记录页填写的旧字段；算法读取时对同一天取 updated_at
    较新的一方，因此这里既能补充"还没有睡眠记录的那些天"，也不会覆盖用户在
    记录页的修改。

    refused 是"不想吃"这个更强的状态（记录页只有勾/不勾两态，表达不了它）：
    早餐 refused 时算法会把今天的午餐与晚餐都往前挪，见 services/advice.py 的
    `_meal_times`。它和 eaten 互斥——点"不想吃"必然意味着没吃。
    """

    __tablename__ = "meal_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    check_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    meal: Mapped[str] = mapped_column(String(16), nullable=False)   # breakfast | lunch | dinner
    eaten: Mapped[bool] = mapped_column(Boolean, nullable=False)
    refused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 不想吃

    user: Mapped["User"] = relationship(back_populates="meal_checks")

    __table_args__ = (
        UniqueConstraint("user_id", "check_date", "meal", name="uq_meal_check"),
        Index("ix_meal_checks_user_date", "user_id", "check_date"),
    )


class AdvicePlan(Base, TimestampMixin):
    """保存的作息计划快照（跨设备/跨会话看到同一份，并用于"计划与实际对照"）。

    payload 存 AdviceResponse 的 JSON 全文；每次生成会写入新快照并把旧快照
    置为非活跃（is_active=False），便于保留历史与统计重算次数。
    """

    __tablename__ = "advice_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)   # 计划第一天
    target_date: Mapped[str] = mapped_column(String(10), nullable=False)  # 达成目标的日期
    target_sleep_time: Mapped[str] = mapped_column(String(5), nullable=False)
    target_wake_time: Mapped[str] = mapped_column(String(5), nullable=False)
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    replans: Mapped[int] = mapped_column(Integer, default=0, nullable=False)          # 重算次数
    postponed_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # 累计顺延天数

    user: Mapped["User"] = relationship(back_populates="advice_plans")

    __table_args__ = (
        Index("ix_advice_plans_user_active", "user_id", "is_active"),
    )


class DeletionRequest(Base, TimestampMixin):
    """账号注销申请。

    user_id 可空：审核通过后申请人的账号会被真实删除，为了保留"谁删的、
    什么时候删的"这条审计记录，删除账号时把 user_id 置空而不是连带删除申请。
    """

    __tablename__ = "deletion_requests"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_DELETED = "deleted"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)   # 申请人用户名快照
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    handled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    handle_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped["User | None"] = relationship(
        back_populates="deletion_requests", foreign_keys=[user_id]
    )


class SessionToken(Base):
    """Cookie 会话（30 天有效期）。只存 token 的 SHA-256，不存明文。"""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="user", nullable=False)   # user | admin
    remember: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class LoginAttempt(Base):
    """登录失败记录（按用户名 + IP 统计，防暴力破解）。"""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class SystemLog(Base):
    """系统日志（同时写入数据库与本地文件）。"""

    __tablename__ = "system_logs"

    LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
