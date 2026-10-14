"""日志：同时写入本地日志文件与数据库 system_logs 表。"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from ..config import settings

_FILE_LOGGER_NAME = "ssp"
_DB_HANDLER_INSTALLED = False

# 防重入：SQLite 同一时刻只允许一个写事务，如果在已打开的写事务里再写日志，
# 内层连接会一直等锁直到 busy_timeout 超时并抛出 "database is locked"。
# 这里用线程内的标志位把嵌套的写库日志降级为只写文件。
_WRITE_DEPTH = threading.local()


def in_db_write() -> bool:
    return getattr(_WRITE_DEPTH, "depth", 0) > 0


def mark_db_write(depth: int) -> None:
    _WRITE_DEPTH.depth = depth


def _build_file_logger() -> logging.Logger:
    logger = logging.getLogger(_FILE_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    logger.propagate = False

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(stream_handler)
    return logger


logger = _build_file_logger()


def get_logger(name: str | None = None) -> logging.Logger:
    """取得统一的文件日志器（子 logger 继承 ssp 的 handler）。"""
    if not name or name == _FILE_LOGGER_NAME:
        return logger
    _build_file_logger()
    return logging.getLogger(f"{_FILE_LOGGER_NAME}.{name}")


class _DbLogHandler(logging.Handler):
    """把日志落到数据库，供管理后台分页查看。

    注意：已处于写事务中时直接跳过，避免自锁（见 _WRITE_DEPTH）。
    """

    def emit(self, record: logging.LogRecord) -> None:
        if in_db_write():
            return
        try:
            payload = getattr(record, "ssp_payload", None) or {}
            write_system_log(
                level=record.levelname,
                event=payload.get("event", "app"),
                message=record.getMessage(),
                user_id=payload.get("user_id"),
                username=payload.get("username"),
                ip=payload.get("ip"),
                path=payload.get("path"),
            )
        except Exception:  # noqa: BLE001 - 日志本身不能抛出
            pass


def install_db_log_handler() -> None:
    """在数据库就绪后安装 DB 日志 handler（只安装一次）。"""
    global _DB_HANDLER_INSTALLED
    if _DB_HANDLER_INSTALLED:
        return
    handler = _DbLogHandler()
    handler.setLevel(logging.WARNING)   # 常规事件用 log_event 显式写库，避免噪音
    logger.addHandler(handler)
    _DB_HANDLER_INSTALLED = True


def write_system_log(
    *,
    level: str,
    event: str,
    message: str,
    user_id: int | None = None,
    username: str | None = None,
    ip: str | None = None,
    path: str | None = None,
) -> None:
    """写一条系统日志到数据库（带锁重试；失败只记录到文件）。"""
    from ..db import write_session
    from ..models import SystemLog

    if in_db_write():
        # 已在写事务中：只留文件日志，避免 SQLite 自锁
        logger.log(
            getattr(logging, (level or "INFO").upper(), logging.INFO),
            "[%s] %s",
            event,
            message,
        )
        return

    level = (level or "INFO").upper()
    try:
        with write_session() as session:
            session.add(
                SystemLog(
                    level=level if level in SystemLog.LEVELS else "INFO",
                    event=(event or "app")[:48],
                    message=(message or "")[:4000],
                    user_id=user_id,
                    username=(username or None) and username[:32],
                    ip=(ip or None) and ip[:64],
                    path=(path or None) and path[:160],
                )
            )
            session.flush()
    except Exception as exc:  # noqa: BLE001
        # 这里只写文件：再走 logger.error 会触发 DB handler 造成递归刷屏
        logger.error("failed to persist system log (%s): %s", event, str(exc)[:400])


def log_event(
    event: str,
    message: str,
    *,
    level: str = "INFO",
    user_id: int | None = None,
    username: str | None = None,
    ip: str | None = None,
    path: str | None = None,
    to_file: bool = True,
) -> None:
    """记录业务事件：文件 + 数据库。"""
    if to_file:
        logger.log(
            getattr(logging, level.upper(), logging.INFO),
            "[%s] %s",
            event,
            message,
            extra={
                "ssp_payload": {
                    "event": event,
                    "user_id": user_id,
                    "username": username,
                    "ip": ip,
                    "path": path,
                }
            },
        )
    write_system_log(
        level=level,
        event=event,
        message=message,
        user_id=user_id,
        username=username,
        ip=ip,
        path=path,
    )


def debug(message: str, *args: object) -> None:
    logger.debug(message, *args)


def info(message: str, *args: object) -> None:
    logger.info(message, *args)


def warning(message: str, *args: object) -> None:
    logger.warning(message, *args)


def error(message: str, *args: object) -> None:
    logger.error(message, *args)
