"""数据库引擎与会话管理。

SQLite 多人并发提交策略：
  * WAL 日志模式，读写不互相阻塞；
  * busy_timeout 让 SQLite 自身先等待锁释放；
  * 之上再包一层"等待重试"装饰器（run_with_retry），
    对 OperationalError: database is locked 做指数退避重试。
"""
from __future__ import annotations

import random
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

T = TypeVar("T")

settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

engine: Engine = create_engine(
    f"sqlite+pysqlite:///{settings.DB_PATH}",
    future=True,
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": settings.DB_BUSY_TIMEOUT_MS / 1000.0,
    },
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """每个连接建立时统一设置 PRAGMA。"""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={settings.DB_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


def is_lock_error(exc: BaseException) -> bool:
    """判断异常是否为 SQLite 锁竞争。"""
    if isinstance(exc, OperationalError):
        message = str(getattr(exc, "orig", exc)).lower()
        return "locked" in message or "busy" in message
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        return "locked" in message or "busy" in message
    return False


def run_with_retry(func: Callable[[], T], *, retries: int | None = None, label: str = "db") -> T:
    """对写操作做等待重试，缓解多人同时提交时的 database is locked。"""
    attempts = settings.DB_WRITE_RETRIES if retries is None else retries
    last_error: BaseException | None = None
    for attempt in range(attempts + 1):
        try:
            return func()
        except BaseException as exc:  # noqa: BLE001 - 需要按类型判断后重放
            if not is_lock_error(exc) or attempt >= attempts:
                raise
            last_error = exc
            delay = min(
                settings.DB_RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, 0.05),
                settings.DB_RETRY_MAX_DELAY,
            )
            time.sleep(delay)
    raise last_error if last_error else RuntimeError("unreachable")


@contextmanager
def write_session() -> Iterator[Session]:
    """写事务上下文：提交失败自动回滚，锁竞争自动重试。

    同时登记"当前线程正在写库"，让嵌套的写库日志自动降级为文件日志，
    避免同一个线程里两层写事务互相等锁（SQLite 单写者）。
    """
    from .core import logkit

    depth = getattr(logkit._WRITE_DEPTH, "depth", 0)
    logkit.mark_db_write(depth + 1)
    session = SessionLocal()
    try:
        yield session
        run_with_retry(session.commit, label="commit")
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
        logkit.mark_db_write(depth)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：只读/普通请求会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def commit_with_retry(session: Session) -> None:
    """在路由中显式提交时使用，带等待重试。"""
    run_with_retry(session.commit, label="commit")


def init_db() -> None:
    """建表 + 首次启动引导。"""
    from . import models  # noqa: F401  确保模型已注册

    models.Base.metadata.create_all(bind=engine)
    _migrate_deletion_requests()
    _migrate_users_advice_notice()
    _migrate_meal_checks_refused()
    _migrate_sleep_records_wake_date()
    _migrate_sleep_records_meal_date()
    _ensure_sqlite_pragmas()


def _migrate_sleep_records_wake_date() -> None:
    """给 sleep_records 表补 wake_date 列，并按老规则回填（"入睡日 + 起床日"改动）。

    老库只有 record_date（入睡日历日），起床算哪天是靠时刻推的。新版本把起床日
    显式存下来，因此这里：
      * ALTER TABLE 加列（SQLite 不能给旧库加 NOT NULL 列，所以列可空）；
      * 用与原逻辑完全相同的规则回填：起床时刻 <= 入睡时刻 → 次日，否则同一天。
        这样老记录的时长、时间图与统计一个都不变。

    幂等：列已存在且没有空值时直接返回，重启不会重复写。
    """
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(sleep_records)")).fetchall()
        if not rows:
            return
        if not any(row[1] == "wake_date" for row in rows):
            conn.execute(text("ALTER TABLE sleep_records ADD COLUMN wake_date VARCHAR(10)"))
        # create_all() 只建缺失的表，老库已有 sleep_records，索引要自己补。
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_sleep_records_wake_date ON sleep_records (wake_date)")
        )
        sleep_minutes = "CAST(substr(sleep_time, 1, 2) AS INTEGER) * 60 + CAST(substr(sleep_time, 4, 2) AS INTEGER)"
        wake_minutes = "CAST(substr(wake_time, 1, 2) AS INTEGER) * 60 + CAST(substr(wake_time, 4, 2) AS INTEGER)"
        conn.execute(
            text(
                "UPDATE sleep_records "
                "SET wake_date = CASE WHEN " + wake_minutes + " <= " + sleep_minutes + " "
                "THEN date(record_date, '+1 day') ELSE date(record_date) END "
                "WHERE wake_date IS NULL OR wake_date = ''"
            )
        )


def _migrate_sleep_records_meal_date() -> None:
    """给 sleep_records 表补 meal_date 列（"三餐记录"的独立日期，create_all 不会加列）。

    回填口径按确认过的默认值走：
      * 一般情况：三餐记录的就是"进入睡眠那天"的三餐 → meal_date = record_date；
      * 入睡与醒来同一天（跨零点入睡，wake_date = record_date）→ 那一晚属于前一天 →
        meal_date = record_date 的前一天。

    只补空值，已有值一律不动；所以重复启动是空操作，也不会覆盖用户后来改过的日期。
    """
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(sleep_records)")).fetchall()
        if not rows:
            return
        if not any(row[1] == "meal_date" for row in rows):
            conn.execute(text("ALTER TABLE sleep_records ADD COLUMN meal_date VARCHAR(10)"))
        # create_all() 只建缺失的表，老库已有 sleep_records，索引要自己补。
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_sleep_records_meal_date ON sleep_records (meal_date)")
        )
        conn.execute(
            text(
                "UPDATE sleep_records "
                "SET meal_date = CASE WHEN wake_date = record_date "
                "THEN date(record_date, '-1 day') ELSE date(record_date) END "
                "WHERE meal_date IS NULL OR meal_date = ''"
            )
        )


def _migrate_meal_checks_refused() -> None:
    """给 meal_checks 表补 refused 列（"早餐不想吃"新增，create_all 不会加列）。

    SQLite 的 ALTER TABLE 不支持带默认值的 NOT NULL 加列组合在旧库上"回填"，
    所以这里显式给默认值 0：老库里的历史打卡一律视为"没有明确拒绝"。
    """
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(meal_checks)")).fetchall()
        if not rows:
            return
        if any(row[1] == "refused" for row in rows):
            return
        conn.execute(
            text("ALTER TABLE meal_checks ADD COLUMN refused BOOLEAN NOT NULL DEFAULT 0")
        )


def _migrate_users_advice_notice() -> None:
    """给 users 表补 advice_notice_ack 列（算法升级新增，create_all 不会加列）。"""
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        if not rows:
            return
        if any(row[1] == "advice_notice_ack" for row in rows):
            return
        conn.execute(text("ALTER TABLE users ADD COLUMN advice_notice_ack VARCHAR(64)"))


def _migrate_deletion_requests() -> None:
    """把旧版 deletion_requests 升级为"user_id 可空 + username 快照"结构。

    旧结构在主键上带 ondelete=CASCADE，删除账号时会把审核记录一起带走，
    后台就查不到"谁被删除了"。这里做一次幂等的表重建（SQLite 无法 ALTER 约束）。
    """
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(deletion_requests)")).fetchall()
        if not rows:
            return
        user_id_row = next((row for row in rows if row[1] == "user_id"), None)
        has_username = any(row[1] == "username" for row in rows)
        user_id_notnull = bool(user_id_row[3]) if user_id_row is not None else False
        if has_username and not user_id_notnull:
            return

        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(
            text(
                "CREATE TABLE deletion_requests__new ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "user_id INTEGER, "
                "username VARCHAR(32), "
                "status VARCHAR(16) NOT NULL, "
                "reason VARCHAR(200), "
                "requested_at DATETIME NOT NULL, "
                "handled_at DATETIME, "
                "handled_by INTEGER, "
                "handle_note VARCHAR(200), "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, "
                "FOREIGN KEY(handled_by) REFERENCES users (id) ON DELETE SET NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO deletion_requests__new "
                "(id, user_id, username, status, reason, requested_at, handled_at, handled_by, "
                " handle_note, created_at, updated_at) "
                "SELECT d.id, "
                "       CASE WHEN u.id IS NULL THEN NULL ELSE d.user_id END, "
                "       u.username, d.status, d.reason, d.requested_at, d.handled_at, "
                "       CASE WHEN h.id IS NULL THEN NULL ELSE d.handled_by END, "
                "       d.handle_note, d.created_at, d.updated_at "
                "FROM deletion_requests AS d "
                "LEFT JOIN users AS u ON u.id = d.user_id "
                "LEFT JOIN users AS h ON h.id = d.handled_by"
            )
        )
        conn.execute(text("DROP TABLE deletion_requests"))
        conn.execute(text("ALTER TABLE deletion_requests__new RENAME TO deletion_requests"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_deletion_requests_user_id ON deletion_requests (user_id)")
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_deletion_requests_status ON deletion_requests (status)")
        )


def _ensure_sqlite_pragmas() -> None:
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA foreign_keys=ON"))


def healthcheck() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
