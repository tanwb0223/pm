"""进度服务：填报、留痕、汇总、偏差、进度总表、快照"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, ForbiddenError, NotFoundError
from app.core.scope import apply_scope
from app.models.project import ProgRecord, ProgSnapshot, ProjMajor, ProjSub
from app.models.system import SysCollege, SysUser
from app.services.aggregation_service import AggregationService
from app.services.calendar_service import CalendarService, deviation, time_progress

logger = logging.getLogger(__name__)


class ProgressService:
    def __init__(self, db: Session):
        self.db = db

    # ============================================================ 填报
    def update_progress(
        self,
        sub_id: int,
        *,
        progress: int,
        note: str | None,
        no_change: bool,
        rollback_reason: str | None,
        operator: Any,
    ) -> dict:
        """更新进度（写留痕 + 更新子项目当前值）"""
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")
        if sub.status == "CLOSED":
            raise BusinessError("已结项的子项目不能再更新进度")
        if sub.status == "ABORTED":
            raise BusinessError("已中止的子项目不能更新进度，请先重启")

        if not (0 <= progress <= 100):
            raise BusinessError("进度必须在 0 到 100 之间")

        # 代填判定：操作人不是负责人
        on_behalf_of = None
        if sub.owner_id and operator.id != sub.owner_id:
            # 只有上级可代填（此处简化：有 project:sub:edit 即可代填）
            on_behalf_of = sub.owner_id

        # 回调判定
        is_rollback = progress < sub.current_progress
        if is_rollback and not (rollback_reason and rollback_reason.strip()):
            raise BusinessError("进度回调必须填写原因")

        rec = ProgRecord(
            sub_id=sub_id,
            progress=progress,
            note=note,
            is_no_change=no_change,
            is_rollback=is_rollback,
            rollback_reason=rollback_reason if is_rollback else None,
            operator_id=operator.id,
            on_behalf_of=on_behalf_of,
        )
        self.db.add(rec)

        # 更新子项目当前值
        if not no_change:
            sub.current_progress = progress
        sub.last_update_at = datetime.now(UTC)

        # 首次填报自动置为进行中
        if sub.status == "NOT_STARTED":
            sub.status = "IN_PROGRESS"

        self.db.flush()
        return {
            "record_id": rec.id,
            "sub_id": sub_id,
            "progress": sub.current_progress,
            "is_rollback": is_rollback,
            "is_no_change": no_change,
            "on_behalf_of": on_behalf_of,
        }

    def list_records(self, sub_id: int, limit: int = 100) -> list[ProgRecord]:
        return list(
            self.db.scalars(
                select(ProgRecord)
                .where(ProgRecord.sub_id == sub_id)
                .order_by(ProgRecord.created_at.desc())
                .limit(limit)
            )
        )

    # ============================================================ 单条进度视图
    def sub_view(self, sub: ProjSub, today: date | None = None, cal: CalendarService | None = None) -> dict:
        today = today or date.today()
        cal = cal or CalendarService.load(self.db)

        tp = time_progress(sub.plan_start, sub.plan_end, today)
        dev = deviation(sub.current_progress, tp)

        # 距最后更新天数（有效日）
        days_since = None
        if sub.last_update_at:
            last_date = sub.last_update_at.astimezone(UTC).date()
            days_since = cal.effective_days_between(last_date, today)

        return {
            "id": sub.id,
            "name": sub.name,
            "major_id": sub.major_id,
            "college_id": sub.college_id,
            "owner_id": sub.owner_id,
            "status": sub.status,
            "current_progress": sub.current_progress,
            "time_progress": tp,
            "deviation": dev,
            "plan_start": sub.plan_start,
            "plan_end": sub.plan_end,
            "weight": float(sub.weight) if sub.weight is not None else None,
            "last_update_at": sub.last_update_at,
            "days_since_update": days_since,
            "is_stagnant": days_since is not None and days_since > 21,
            "is_near_deadline": self._is_near_deadline(sub, today),
            "has_plan": tp is not None,
        }

    @staticmethod
    def _is_near_deadline(sub: ProjSub, today: date) -> bool:
        """项目临期：距结束不足 30 天且进度低于 80%（AL-05）"""
        if sub.plan_end is None:
            return False
        remaining = (sub.plan_end - today).days
        return 0 <= remaining < 30 and sub.current_progress < 80

    # ============================================================ 进度总表
    def table(
        self,
        user: Any,
        *,
        major_id: int | None = None,
        college_id: int | None = None,
        status: str | None = None,
        owner_id: int | None = None,
        keyword: str | None = None,
        only_abnormal: bool = False,
        page: int = 1,
        page_size: int = 50,
        sort: str = "deviation",
    ) -> tuple[list[dict], int]:
        stmt: Select = select(ProjSub).where(ProjSub.deleted_at.is_(None))
        stmt = apply_scope(stmt, user, ProjSub)

        if major_id:
            stmt = stmt.where(ProjSub.major_id == major_id)
        if college_id:
            stmt = stmt.where(ProjSub.college_id == college_id)
        if status:
            stmt = stmt.where(ProjSub.status == status)
        if owner_id:
            stmt = stmt.where(ProjSub.owner_id == owner_id)
        if keyword:
            stmt = stmt.where(ProjSub.name.ilike(f"%{keyword}%"))

        subs = list(self.db.scalars(stmt))
        today = date.today()
        cal = CalendarService.load(self.db)

        rows = []
        colleges = {c.id: c.name for c in self.db.scalars(select(SysCollege))}
        majors = {m.id: m.name for m in self.db.scalars(select(ProjMajor))}
        for s in subs:
            d = self.sub_view(s, today, cal)
            d["college_name"] = colleges.get(s.college_id)
            d["major_name"] = majors.get(s.major_id)
            d["owner_name"] = s.owner.name if s.owner else None
            rows.append(d)

        if only_abnormal:
            rows = [r for r in rows if (r["deviation"] is not None and r["deviation"] < -10)
                    or r["is_stagnant"] or not r["has_plan"]]

        # 排序
        if sort == "deviation":
            # 偏差从大到小（最滞后在前）；无偏差的排后
            rows.sort(key=lambda r: (r["deviation"] is None, r["deviation"] if r["deviation"] is not None else 0))
        elif sort == "update":
            rows.sort(key=lambda r: (r["last_update_at"] is None, r["last_update_at"] or datetime.min))
        elif sort == "progress":
            rows.sort(key=lambda r: r["current_progress"])

        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    # ============================================================ 汇总
    def major_summary(self, major_id: int) -> dict:
        agg = AggregationService(self.db)
        major = self.db.get(ProjMajor, major_id)
        if major is None or major.deleted_at is not None:
            raise NotFoundError("大项目不存在")
        data = agg.breakdown(major_id)
        data["school_progress"] = agg.school_progress()
        data["major_name"] = major.name
        return data

    def school_summary(self) -> dict:
        agg = AggregationService(self.db)
        majors = list(
            self.db.scalars(
                select(ProjMajor).where(
                    ProjMajor.deleted_at.is_(None), ProjMajor.status == "ACTIVE"
                )
            )
        )
        return {
            "school_progress": agg.school_progress(),
            "major_count": len(majors),
            "majors": [
                {"id": m.id, "name": m.name, "progress": agg.major_progress(m.id)}
                for m in majors
            ],
        }

    # ============================================================ 快照
    def generate_snapshots(self, day: date | None = None) -> dict:
        """生成指定日期的每日快照（供预警规则使用）"""
        day = day or date.today()
        cal = CalendarService.load(self.db)
        is_free = cal.is_free_day(day)

        subs = list(
            self.db.scalars(select(ProjSub).where(ProjSub.deleted_at.is_(None)))
        )
        created = 0
        skipped = 0
        for s in subs:
            # 只对进行中的子项目生成快照
            if s.status not in ("IN_PROGRESS", "NOT_STARTED"):
                skipped += 1
                continue
            tp = time_progress(s.plan_start, s.plan_end, day)
            if tp is not None:
                dev = s.current_progress - tp
            else:
                dev = 0

            days_since = 0
            if s.last_update_at:
                last = s.last_update_at.astimezone(UTC).date()
                days_since = cal.effective_days_between(last, day)

            existing = self.db.get(ProgSnapshot, (s.id, day))
            if existing:
                existing.time_progress = tp or 0
                existing.actual_progress = s.current_progress
                existing.deviation = dev
                existing.days_since_update = days_since
                existing.is_free_day = is_free
            else:
                self.db.add(
                    ProgSnapshot(
                        sub_id=s.id,
                        snap_date=day,
                        time_progress=tp or 0,
                        actual_progress=s.current_progress,
                        deviation=dev,
                        days_since_update=days_since,
                        is_free_day=is_free,
                    )
                )
                created += 1
        self.db.flush()
        return {"date": day.isoformat(), "created": created, "skipped": skipped, "is_free_day": is_free}
