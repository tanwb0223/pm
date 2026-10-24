"""冒烟测试 3：认证边角

覆盖：
- 配置密钥守卫（占位密钥不能上生产）
- 登录失败锁定
- 改密后旧令牌失效 + 换发新令牌可用
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request


# ---------------------------------------------------------------- 密钥守卫
def test_placeholder_secret_rejected_in_production():
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(
            secret_key="请替换为随机字符串（至少 32 位）",
            debug=False,
            _env_file=None,
        )


def test_placeholder_secret_allowed_in_debug():
    from app.core.config import Settings

    s = Settings(secret_key="dev-secret-change-me-in-production", debug=True, _env_file=None)
    assert s.debug is True


def test_short_secret_rejected():
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(secret_key="short", debug=False, _env_file=None)


# ---------------------------------------------------------------- 登录限流
def test_login_throttle_locks_after_max_attempts():
    from app.core.config import settings
    from app.core.login_guard import LoginThrottle

    t = LoginThrottle()
    key = "pwd:someone"
    for _ in range(settings.login_max_attempts - 1):
        t.record_failure(key)
        assert t.locked_seconds(key) == 0
    t.record_failure(key)
    assert t.locked_seconds(key) > 0
    t.reset(key)
    assert t.locked_seconds(key) == 0


# ---------------------------------------------------------------- 令牌失效
def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/x", "headers": []})


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_password_change_invalidates_old_token(db):
    from app.core.deps import get_current_user
    from app.core.exceptions import UnauthorizedError
    from app.core.security import create_access_token, hash_password, password_version
    from app.models.system import SysUser

    user = SysUser(
        staff_no="T900",
        name="测试用户",
        password_hash=hash_password("Test@123"),
        status="ACTIVE",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 1) 改密前签发的令牌可用
    old_token = create_access_token(user.id, {"pv": password_version(user)})
    assert get_current_user(_request(), _creds(old_token), db).id == user.id

    # 2) 改密后旧令牌立即失效
    user.password_updated_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(UnauthorizedError):
        get_current_user(_request(), _creds(old_token), db)

    # 3) 改密后重新登录拿到的令牌（带新 pv）可用
    fresh = create_access_token(user.id, {"pv": password_version(user)})
    assert get_current_user(_request(), _creds(fresh), db).id == user.id
