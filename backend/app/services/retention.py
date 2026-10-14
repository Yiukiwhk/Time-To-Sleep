"""数据保留策略：每个用户仅保留最近 6 个月的睡眠记录。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select

from ..config import settings
from ..core.timing import month_shift
from ..db import run_with_retry, write_session
from ..models import LoginAttempt, SleepRecord, SystemLog, utcnow
from ..core import logkit


def retention_cutoff(today: date | None = None) -> str:
    """过期阈值日期（YYYY-MM-DD）。该日期之前的记录会被清理。"""
    today = today or date.today()
    cutoff = month_shift(utcnow().replace(year=today.year, month=today.month, day=today.day),
                         -settings.RETENTION_MONTHS)
    return cutoff.date().isoformat()


def purge_old_sleep_records(user_id: int | None = None) -> int:
    """清理超过保留期的睡眠记录；user_id 为空时清理所有用户。"""
    cutoff = retention_cutoff()

    def _delete() -> int:
        with write_session() as session:
            statement = delete(SleepRecord).where(SleepRecord.record_date < cutoff)
            if user_id is not None:
                statement = statement.where(SleepRecord.user_id == user_id)
            result = session.execute(statement)
            session.flush()
            return result.rowcount or 0

    return run_with_retry(_delete, label="purge_records")


def count_user_records(user_id: int) -> int:
    from sqlalchemy import func

    from ..db import SessionLocal

    session = SessionLocal()
    try:
        return int(
            session.execute(
                select(func.count(SleepRecord.id)).where(SleepRecord.user_id == user_id)
            ).scalar_one()
        )
    finally:
        session.close()


def purge_login_attempts(days: int = 30) -> int:
    from datetime import timedelta

    threshold = utcnow() - timedelta(days=days)

    def _delete() -> int:
        with write_session() as session:
            result = session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < threshold))
            session.flush()
            return result.rowcount or 0

    return run_with_retry(_delete, label="purge_attempts")


def trim_system_logs(keep: int | None = None) -> int:
    """系统日志只保留最近 N 条，防止数据库无限膨胀。"""
    keep = keep or settings.LOG_RETENTION_ROWS

    def _delete() -> int:
        from ..db import SessionLocal

        session = SessionLocal()
        try:
            threshold_row = session.execute(
                select(SystemLog.id).order_by(SystemLog.id.desc()).offset(keep).limit(1)
            ).scalar_one_or_none()
        finally:
            session.close()
        if threshold_row is None:
            return 0
        with write_session() as inner:
            result = inner.execute(delete(SystemLog).where(SystemLog.id <= threshold_row))
            inner.flush()
            return result.rowcount or 0

    return run_with_retry(_delete, label="trim_logs")


def run_cleanup() -> dict:
    """周期性清理：睡眠记录 + 过期会话 + 登录尝试 + 日志裁剪。"""
    from ..services import sessions

    result = {
        "records_removed": purge_old_sleep_records(),
        "sessions_removed": sessions.purge_expired_sessions(),
        "attempts_removed": purge_login_attempts(),
        "logs_trimmed": trim_system_logs(),
        "cutoff": retention_cutoff(),
    }
    if any(result[key] for key in ("records_removed", "sessions_removed", "attempts_removed", "logs_trimmed")):
        logkit.log_event(
            "retention_cleanup",
            "cleanup done: {records_removed} records, {sessions_removed} sessions, "
            "{attempts_removed} attempts, {logs_trimmed} logs (cutoff {cutoff})".format(**result),
        )
    return result
