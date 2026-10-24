"""预警规则基类与候选结构"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class AlertCandidate:
    """规则命中的候选"""

    alert_type: str
    level: str
    sub_id: int | None = None
    major_id: int | None = None
    college_id: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    # 接收人兜底链（子项目无负责人 → 学院负责人 → 大项目负责人 → 归口管理员）
    owner_id: int | None = None
    college_owner_id: int | None = None
    major_owner_id: int | None = None
    # 去重键后缀：默认按「子项目 + 规则」去重，但有些规则的同源对象不是
    # 子项目（如 AL-08 风险超期，一条子项目下可能有多条风险）。
    # 早期 AL-08 全部 dedup_key = "-:AL-08"，多条风险被合并成一条，
    # 处置人不知道是哪条风险超期（P1-9）。这里允许规则指定后缀。
    dedup_suffix: str | None = None

    @property
    def dedup_key(self) -> str:
        key = f"{self.sub_id or '-'}:{self.alert_type}"
        if self.dedup_suffix:
            key = f"{key}:{self.dedup_suffix}"
        return key[:128]


class AlertRule(ABC):
    """预警规则基类：一条规则一个类"""

    code: str = ""
    level: str = "ORANGE"
    name: str = ""
    trigger_mode: str = "daily"   # event / daily / hourly
    immutable: bool = False       # AL-09 / AL-10 不可关闭

    @abstractmethod
    def evaluate(self, db: Session, ctx: "RuleContext") -> list[AlertCandidate]:
        """返回命中的候选"""
        ...

    def enabled(self, ctx: "RuleContext") -> bool:
        return ctx.config.get_bool(f"alert.{self.code}.enabled", True)


@dataclass
class RuleContext:
    """规则执行上下文：一次性加载配置与日历，避免每条规则重复查库"""

    db: Session
    day: date
    calendar: Any                     # CalendarService
    config: Any                       # ConfigService
    snapshot_rows: dict[int, Any] = field(default_factory=dict)

    def get_int(self, key: str, default: int) -> int:
        return self.config.get_int(key, default)

    def get_bool(self, key: str, default: bool) -> bool:
        return self.config.get_bool(key, default)
