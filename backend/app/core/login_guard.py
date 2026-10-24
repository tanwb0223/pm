"""登录失败限流（落库实现）

## 为什么改

早期版本把失败次数放在**进程内字典**里，有三个致命问题：
1. 部署脚本默认 `--workers 2`，两个进程各自计数 → 实际阈值从 5 次变成 10 次；
2. 服务重启后计数清零，攻击者只要触发一次重启（或赶上发版）就重置；
3. 管理员看不到也解不开锁定 —— 用户被锁后只能干等，没有解锁入口。

现在改为落库（`sys_user.failed_login_count` / `locked_until`）：
- 多 worker / 重启后仍然有效；
- 管理员可通过 `POST /api/system/users/{id}/unlock` 手动解锁。

## 为什么还保留内存兜底

"用户名不存在"时数据库里没有对应的用户行，失败次数无处可记。
这类请求（撞库/用户名枚举）仍用进程内计数挡一层，属于**补充**而非主实现；
主链路（真实账号）一律走数据库。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import SysUser

logger = logging.getLogger(__name__)


class LoginLockedError(HTTPException):
    """账号锁定中：429

    为什么用 429 而不是 400：前端需要区分"账号密码错"与"被锁定"，
    429 Too Many Requests 语义最准确，且能带 Retry-After 头。
    """

    def __init__(self, wait_seconds: int):
        minutes = max(1, (wait_seconds + 59) // 60)
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"账号已锁定，请 {minutes} 分钟后再试",
            headers={"Retry-After": str(max(1, wait_seconds))},
        )
        self.wait_seconds = wait_seconds


# ---------------------------------------------------------------- 数据库实现
def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读出来的时间可能没有时区，统一按 UTC 解释"""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def locked_seconds(user: SysUser | None) -> int:
    """剩余锁定秒数；未锁定返回 0"""
    if user is None:
        return 0
    until = _as_utc(getattr(user, "locked_until", None))
    if until is None:
        return 0
    remain = (until - datetime.now(UTC)).total_seconds()
    return int(remain) + 1 if remain > 0 else 0


def check_locked(user: SysUser | None) -> None:
    """登录前检查：锁定中直接 429（带剩余分钟数）

    顺带处理"锁已过期"：把过期锁与计数清掉，否则解锁后再失败一次
    又会立刻触发锁定（历史计数仍然 >= 阈值）。
    """
    if user is None:
        return
    wait = locked_seconds(user)
    if wait > 0:
        raise LoginLockedError(wait)
    if getattr(user, "locked_until", None) is not None:
        user.locked_until = None
        user.failed_login_count = 0


def record_failure(db: Session, user: SysUser | None) -> None:
    """记一次失败：count+1，达到上限则锁定 login_lock_minutes 分钟

    注意：这里只 flush 不 commit，由调用方（登录接口）统一提交，
    避免"登录失败但计数没写进去"或"计数写进去但事务回滚"。
    """
    if user is None:
        return
    count = (user.failed_login_count or 0) + 1
    user.failed_login_count = count
    if count >= settings.login_max_attempts:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=settings.login_lock_minutes)
        # 锁定时把计数归零：解锁后重新给满额机会，而不是"一失败就再锁"
        user.failed_login_count = 0
        logger.warning(
            "账号 %s 连续登录失败达到 %s 次，锁定 %s 分钟",
            user.staff_no,
            settings.login_max_attempts,
            settings.login_lock_minutes,
        )
    db.flush()


def record_success(db: Session, user: SysUser | None) -> None:
    """登录成功：清零失败计数与锁定状态"""
    if user is None:
        return
    user.failed_login_count = 0
    user.locked_until = None
    db.flush()


def unlock_user(db: Session, user: SysUser) -> None:
    """管理员手动解锁"""
    user.failed_login_count = 0
    user.locked_until = None
    db.flush()


# ---------------------------------------------------------------- 内存兜底
class LoginThrottle:
    """进程内失败计数（仅用于"账号不存在"的请求）

    为什么不删：查不到用户行时数据库无处落账，这类撞库请求仍需要
    一个廉价的上限；真实账号的锁定一律走上面的数据库实现。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    # ---------------------------------------------------------------- 内部
    def _window_seconds(self) -> int:
        # 统计窗口 = 锁定时长的一半，最少 60 秒：
        # 5 次/15 分钟 这种组合等价于"15 分钟窗口内 5 次"，口径简单可解释。
        return max(60, int(settings.login_lock_minutes * 60 / 2))

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures.setdefault(key, deque())
        limit = now - self._window_seconds()
        while q and q[0] < limit:
            q.popleft()
        return q

    # ---------------------------------------------------------------- 对外
    def locked_seconds(self, key: str) -> int:
        """剩余锁定秒数；未锁定返回 0"""
        now = time.monotonic()
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until <= now:
                self._locked_until.pop(key, None)
                return 0
            return int(until - now) + 1

    def record_failure(self, key: str) -> None:
        """记一次失败；达到阈值即锁定"""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            q.append(now)
            if len(q) >= settings.login_max_attempts:
                self._locked_until[key] = now + settings.login_lock_minutes * 60
                q.clear()

    def reset(self, key: str) -> None:
        """登录成功后清零（换设备登录不该被历史失败拖累）"""
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


login_throttle = LoginThrottle()
