"""登录失败限流（进程内实现）

为什么需要：
    Argon2 让离线拖库无法快速还原口令，但在线撞库不需要拖库 ——
    只要接口不限制尝试次数，攻击者可以拿字典慢慢试。原先
    /auth/login 与 /auth/login/ldap 完全没有计数。

适用范围与局限：
    计数放在进程内存里，单实例（便携版 / 单机部署）足够；
    多 worker / 多实例时各进程各自计数，等效于把阈值放大 N 倍。
    真要做到全局锁定，需要把计数落到 Redis 或 sys_config，
    这属于二期；当前先堵住"无限次尝试"这个最要命的口子。
"""
from __future__ import annotations

import threading
import time
from collections import deque

from app.core.config import settings


class LoginThrottle:
    """按 key（账号）统计失败次数，超限后锁定一段时间"""

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
