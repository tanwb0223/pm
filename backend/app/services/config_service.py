"""配置服务：从 sys_config 读取参数，带进程内 TTL 缓存

## 为什么加 TTL

原实现是「类级字典 + 一次性加载」：进程启动时读一遍 `sys_config`，
之后永不刷新。单 worker 时管理员改参数后 `ConfigService.clear_cache()`
能生效；但部署脚本默认 `--workers 2`，只有处理那次 PUT 请求的 worker
清了缓存，另一个 worker 继续用旧值 —— 表现为「参数改了，一半请求生效」。

## 为什么选 TTL 而不是版本号比对

版本号方案（每次 get 前查 `max(updated_at)`）要么每读一次参数多一次 DB 查询
（参数读取在预警规则里是热点），要么还得再缓存版本号、等于换汤不换药。
TTL 实现简单、行为可预期：最多 30 秒后所有实例一致，足够内网系统使用。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import SysConfig

logger = logging.getLogger(__name__)


class ConfigService:
    _cache: dict[str, str] = {}
    _loaded = False
    _loaded_at: float = 0.0      # 上次加载的 monotonic 时刻

    def __init__(self, db: Session):
        self.db = db
        if self._is_expired():
            self.reload()

    # ------------------------------------------------------------ 缓存
    @classmethod
    def _ttl(cls) -> float:
        """缓存有效期（秒），允许通过环境变量调整；0 表示每次重读"""
        return max(0, int(getattr(settings, "config_cache_ttl_seconds", 30)))

    @classmethod
    def _is_expired(cls) -> bool:
        if not cls._loaded:
            return True
        ttl = cls._ttl()
        if ttl <= 0:
            return True
        return (time.monotonic() - cls._loaded_at) > ttl

    def reload(self) -> None:
        rows = self.db.scalars(select(SysConfig))
        ConfigService._cache = {r.key: r.value for r in rows}
        ConfigService._loaded = True
        ConfigService._loaded_at = time.monotonic()

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache = {}
        cls._loaded = False
        cls._loaded_at = 0.0

    def _ensure_fresh(self) -> None:
        """读之前检查 TTL：过期就地重载（多 worker 下 30 秒内收敛）"""
        if self._is_expired():
            try:
                self.reload()
            except Exception:
                # 重载失败不能影响业务：沿用旧缓存并告警
                logger.warning("系统参数缓存重载失败，沿用旧值", exc_info=True)

    # ------------------------------------------------------------ getter
    def get(self, key: str, default: str | None = None) -> str | None:
        self._ensure_fresh()
        return self._cache.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get(key)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.get(key)
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self.get(key)
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------ setter
    def set(self, key: str, value: Any) -> None:
        row = self.db.get(SysConfig, key)
        if row is None:
            row = SysConfig(key=key, value=str(value), value_type="STRING")
            self.db.add(row)
        else:
            row.value = str(value)
        self.db.flush()
        self._cache[key] = str(value)

    def all(self) -> dict[str, str]:
        self._ensure_fresh()
        return dict(self._cache)
