"""安全：密码哈希、JWT 签发与校验"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

_ph = PasswordHasher()


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(raw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, raw)
    except (VerifyMismatchError, Exception):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except Exception:
        return False


def password_version(user: Any) -> int:
    """口令版本 = 最后一次改密的秒级时间戳；从未改密为 0

    写进令牌的 pv 声明，改密后版本变化 → 旧令牌立即失效。
    比拿 iat 和 password_updated_at 比大小可靠：两者都是秒级，
    同一秒内签发的令牌会误判成"改密前的旧令牌"。
    """
    updated = getattr(user, "password_updated_at", None)
    return int(updated.timestamp()) if updated else 0


def create_access_token(user_id: int, extra: dict | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "pv": 0,
        **(extra or {}),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("登录已过期，请重新登录") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError("登录凭证无效") from e
