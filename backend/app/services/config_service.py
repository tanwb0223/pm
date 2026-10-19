"""配置服务：从 sys_config 读取参数，带进程内缓存"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import SysConfig


class ConfigService:
    _cache: dict[str, str] = {}
    _loaded = False

    def __init__(self, db: Session):
        self.db = db
        if not ConfigService._loaded:
            self.reload()

    def reload(self) -> None:
        rows = self.db.scalars(select(SysConfig))
        ConfigService._cache = {r.key: r.value for r in rows}
        ConfigService._loaded = True

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache = {}
        cls._loaded = False

    # ------------------------------------------------------------ getter
    def get(self, key: str, default: str | None = None) -> str | None:
        return self._cache.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        v = self._cache.get(key)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self._cache.get(key)
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self._cache.get(key)
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
        return dict(self._cache)
