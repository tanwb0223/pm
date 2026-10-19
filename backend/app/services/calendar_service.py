"""日期与日历：双日历口径

文档 4.2 有明确要求：
- 时间进度按「自然日」计算
- 预警计时（停滞/零进度/滞后持续）按「有效日」计算，免填期暂停
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import SysHoliday


class CalendarService:
    """免填日历 + 自然日/有效日换算"""

    def __init__(self, free_days: set[date] | None = None):
        self._free_days: set[date] = free_days or set()

    # ------------------------------------------------------------ 构造
    @classmethod
    def load(cls, db: Session) -> "CalendarService":
        rows = db.scalars(select(SysHoliday))
        return cls({r.date for r in rows})

    def refresh(self, db: Session) -> None:
        rows = db.scalars(select(SysHoliday))
        self._free_days = {r.date for r in rows}

    # ------------------------------------------------------------ 判断
    def is_free_day(self, d: date) -> bool:
        return d in self._free_days

    def iter_days(self, start: date, end: date):
        d = start
        while d <= end:
            yield d
            d += timedelta(days=1)

    # ------------------------------------------------------------ 换算
    def natural_days_between(self, start: date, end: date) -> int:
        """自然日差（含首尾之间的天数，不含 end 当天）"""
        return max(0, (end - start).days)

    def effective_days_between(self, start: date, end: date) -> int:
        """有效日差：排除免填日"""
        if end <= start:
            return 0
        total = 0
        for d in self.iter_days(start, end - timedelta(days=1)):
            if d not in self._free_days:
                total += 1
        return total

    def add_effective_days(self, start: date, days: int) -> date:
        """从 start 起算，往后数 days 个有效日，返回那一天的日期"""
        d = start
        counted = 0
        guard = 0
        while counted < days:
            d += timedelta(days=1)
            if d not in self._free_days:
                counted += 1
            guard += 1
            if guard > 3650:
                break
        return d

    def effective_days_since(self, start: date | None, today: date) -> int | None:
        if start is None:
            return None
        return self.effective_days_between(start.date() if hasattr(start, "date") else start, today)


def time_progress(plan_start: date | None, plan_end: date | None, today: date) -> int | None:
    """时间进度：按自然日，含当日，截断 [0,100]（文档 4.2）

    未设置计划起止时间时返回 None（不计算偏差，触发 AL-10）
    """
    if plan_start is None or plan_end is None:
        return None
    if today < plan_start:
        return 0
    if today >= plan_end:
        return 100
    total = (plan_end - plan_start).days
    if total <= 0:
        return 100
    passed = (today - plan_start).days
    return max(0, min(100, passed * 100 // total))


def deviation(actual: int, time_prog: int | None) -> int | None:
    """偏差值 = 实际进度 − 时间进度（文档 4.2）"""
    if time_prog is None:
        return None
    return actual - time_prog
