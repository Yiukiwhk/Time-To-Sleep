"""安全工具：bcrypt 密码哈希、会话 token、客户端信息。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

import bcrypt

from ..config import settings

# bcrypt 会忽略第 73 个字节之后的内容，这里主动限制长度
_BCRYPT_MAX_BYTES = 72


def _to_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raw = raw[:_BCRYPT_MAX_BYTES]
    return raw


def hash_password(password: str) -> str:
    """bcrypt 加密存储，禁止明文。"""
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    """高熵会话 token（仅返回给 Cookie，库里只存哈希）。"""
    return secrets.token_urlsafe(48)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry(remember: bool, now: datetime) -> datetime:
    ttl = settings.SESSION_TTL_SECONDS if remember else settings.SESSION_TTL_SHORT_SECONDS
    return now + timedelta(seconds=ttl)


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


def client_ip(request) -> str:
    """取得客户端 IP（兼容反向代理场景）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def client_ua(request) -> str:
    return (request.headers.get("user-agent") or "")[:200]
