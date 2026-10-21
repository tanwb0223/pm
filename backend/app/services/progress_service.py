"""进度服务：填报、留痕、汇总、偏差、进度总表、快照"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, ForbiddenError, NotFoundError
from app.core.scope import apply_scope, assert_sub_access
from app.db.dialect import ilike
from app.models.project import ProgRecord, ProgSnapshot, ProjMajor, ProjSub
from app.models.system import SysCollege, SysUser
from app.services.aggregation_service import AggregationService
from app.services.calendar_service import CalendarService, deviation, time_progress

logger = logging.getLogger(__name__)

# 未传「最近一次填报」时用于区分的哨兵（None 表示"确实没有填报记录"）
_UNSET = object()


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
        # 数据范围：只能更新自己有权访问的子项目（功能权限之外的水平越权防护）
        assert_sub_access(operator, sub)
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

        # 事件驱动预警：进度回调时同步生成 AL-04
        # 注意：必须在 flush 之后调用，此时留痕记录已落库
        if is_rollback:
            from app.services.alert_service import AlertService

            AlertService(self.db).handle_rollback(sub, rollback_reason)

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
    def sub_view(
        self,
        sub: ProjSub,
        today: date | None = None,
        cal: CalendarService | None = None,
        last_record: Any = _UNSET,
    ) -> dict:
        today = today or date.today()
        cal = cal or CalendarService.load(self.db)

        tp = time_progress(sub.plan_start, sub.plan_end, today)
        dev = deviation(sub.current_progress, tp)

        # 距最后更新天数（有效日）
        days_since = None
        if sub.last_update_at:
            last_date = sub.last_update_at.astimezone(UTC).date()
            days_since = cal.effective_days_between(last_date, today)

        # 上次填报的说明（供前端自动带出，减少重复输入 —— 文档 3.2(1)）
        # 调用方已批量取好则直接用；未传哨兵时才单查（兼容单条视图的调用方式）
        if last_record is _UNSET:
            last_record = self.db.scalar(
                select(ProgRecord)
                .where(ProgRecord.sub_id == sub.id)
                .order_by(ProgRecord.created_at.desc())
                .limit(1)
            )
        last = last_record

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
            # 上次说明：前端填报弹窗默认填入，改几个字即可提交
            "last_note": last.note if last else None,
            "last_record_at": last.created_at if last else None,
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
            stmt = stmt.where(ilike(ProjSub.name, keyword))

        subs = list(self.db.scalars(stmt))
        today = date.today()
        cal = CalendarService.load(self.db)

        # 批量取每个子项目最近一次填报（避免 N+1：原来每个子项目一条 SQL）
        last_records: dict[int, ProgRecord] = {}
        if subs:
            for r in self.db.scalars(
                select(ProgRecord)
                .where(ProgRecord.sub_id.in_([s.id for s in subs]))
                .order_by(ProgRecord.sub_id, ProgRecord.created_at.desc())
            ):
                # 按 (sub_id, 时间倒序) 排列，每个子项目第一条即最近一次
                last_records.setdefault(r.sub_id, r)

        rows = []
        colleges = {c.id: c.name for c in self.db.scalars(select(SysCollege))}
        majors = {m.id: m.name for m in self.db.scalars(select(ProjMajor))}
        for s in subs:
            d = self.sub_view(s, today, cal, last_record=last_records.get(s.id))
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

    # ============================================================ 进度趋势曲线
    def sub_trend(self, sub_id: int, days: int = 90) -> dict:
        """单个子项目的进度曲线（文档 3.2(6)）

        返回两条线：
        - actual：每日实际进度（取当天快照）
        - expected：按时间应有的进度

        两条线一交叉就能看出「从哪天开始慢了」。
        同时算出「开始滞后的日期」与「滞后持续天数」，便于页面直接提示。
        """
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")

        today = date.today()
        start = today - timedelta(days=days - 1)

        # 若计划开始早于统计窗口，从计划开始算起（让曲线完整）
        if sub.plan_start and sub.plan_start < start:
            start = sub.plan_start

        cal = CalendarService.load(self.db)

        # 快照（按日期升序）
        snaps = {
            s.snap_date: s
            for s in self.db.scalars(
                select(ProgSnapshot)
                .where(
                    ProgSnapshot.sub_id == sub_id,
                    ProgSnapshot.snap_date >= start,
                    ProgSnapshot.snap_date <= today,
                )
                .order_by(ProgSnapshot.snap_date)
            )
        }

        # 进度留痕（用于没有快照的日子补实际值）
        records = list(
            self.db.scalars(
                select(ProgRecord)
                .where(ProgRecord.sub_id == sub_id)
                .order_by(ProgRecord.created_at)
            )
        )

        # 首次填报日期：此前的实际进度视为 0（项目还没开始填）
        first_record_date = (
            records[0].created_at.astimezone(UTC).date() if records else None
        )

        def actual_at(d: date) -> int | None:
            """某天的实际进度

            优先级：填报记录 > 快照 > 推算

            - 该日有填报记录：取当天最后一次填报值
            - 该日无填报但有快照：取快照值
            - 无快照但在首次填报之后：取该日及之前最后一次填报值
            - 首次填报之前：0（项目还没开始，进度自然是 0）
            - 完全没有填报记录：None（不画线）

            早期实现是「快照优先」，导致当天补填后曲线不更新：
            快照是每日 01:00 生成的，当天再改进度，图上仍显示凌晨的旧值
            （典型现象：列表显示 100%，趋势末点还是 10%）。
            """
            # 1) 当天是否有填报记录（可能有多次，取最后一条）
            latest_today = None
            for r in records:
                rd = r.created_at.astimezone(UTC).date()
                if rd == d:
                    latest_today = r.progress
                elif rd > d:
                    break
            if latest_today is not None:
                return latest_today

            # 2) 无当天填报，退回快照
            snap = snaps.get(d)
            if snap is not None:
                return snap.actual_progress

            if first_record_date is None:
                return None
            if d < first_record_date:
                # 计划开始之前不算（避免长直线拉低观感）
                if sub.plan_start and d < sub.plan_start:
                    return None
                return 0
            val = None
            for r in records:
                rd = r.created_at.astimezone(UTC).date()
                if rd <= d:
                    val = r.progress
                else:
                    break
            return val

        points: list[dict] = []
        first_lag_date: date | None = None
        consecutive_start: date | None = None
        lag_days = 0

        d = start
        while d <= today:
            exp = time_progress(sub.plan_start, sub.plan_end, d)
            act = actual_at(d)
            dev = (act - exp) if (act is not None and exp is not None) else None

            points.append(
                {
                    "date": d.isoformat(),
                    "expected": exp,
                    "actual": act,
                    "deviation": dev,
                    "is_free_day": cal.is_free_day(d),
                }
            )

            # 滞后判定：偏差 < -10%（与 AL-01 同口径）
            # first_lag_date 记「最早一次滞后」，lag_days 记「当前连续滞后天数」
            if dev is not None and dev < -10:
                if first_lag_date is None:
                    first_lag_date = d
                if consecutive_start is None:
                    consecutive_start = d
                lag_days = (d - consecutive_start).days + 1
            else:
                consecutive_start = None
                lag_days = 0

            d += timedelta(days=1)

        return {
            "sub_id": sub.id,
            "sub_name": sub.name,
            "plan_start": sub.plan_start,
            "plan_end": sub.plan_end,
            "current_progress": sub.current_progress,
            "points": points,
            "first_lag_date": first_lag_date.isoformat() if first_lag_date else None,
            "lag_days": lag_days,
            "has_plan": sub.plan_start is not None and sub.plan_end is not None,
        }

    # ============================================================ 学院对比
    def college_compare(self, user: Any, major_id: int | None = None) -> list[dict]:
        """学院横向对比（文档 3.2(6)）

        每个学院一行：
        - avg_deviation   平均偏差（按子项目简单平均）
        - lagging_count   滞后子项目数（偏差 < -10%）
        - severe_count    严重滞后数（偏差 <= -25%）
        - total_subs      计入统计的子项目数
        - update_rate     更新及时率（21 个有效日内更新过的占比）
        """
        stmt = select(ProjSub).where(ProjSub.deleted_at.is_(None))
        stmt = apply_scope(stmt, user, ProjSub)
        if major_id:
            stmt = stmt.where(ProjSub.major_id == major_id)
        subs = list(self.db.scalars(stmt))

        today = date.today()
        cal = CalendarService.load(self.db)
        colleges = {c.id: c.name for c in self.db.scalars(select(SysCollege))}

        buckets: dict[int, list[dict]] = {}
        for s in subs:
            if s.status in ("ABORTED",):
                continue
            tp = time_progress(s.plan_start, s.plan_end, today)
            dev = (s.current_progress - tp) if tp is not None else None
            days_since = None
            if s.last_update_at:
                days_since = cal.effective_days_between(
                    s.last_update_at.astimezone(UTC).date(), today
                )
            buckets.setdefault(s.college_id, []).append(
                {"dev": dev, "days_since": days_since}
            )

        out = []
        for cid, items in buckets.items():
            devs = [x["dev"] for x in items if x["dev"] is not None]
            updates = [x["days_since"] for x in items if x["days_since"] is not None]
            total = len(items)
            fresh = sum(1 for x in updates if x <= 21)
            out.append(
                {
                    "college_id": cid,
                    "college_name": colleges.get(cid, f"#{cid}"),
                    "total_subs": total,
                    "avg_deviation": round(sum(devs) / len(devs), 1) if devs else None,
                    "lagging_count": sum(1 for x in devs if x < -10),
                    "severe_count": sum(1 for x in devs if x <= -25),
                    "updated_count": fresh,
                    "update_rate": round(fresh / total * 100, 1) if total else None,
                }
            )

        # 平均偏差从小到大（最滞后在前）
        out.sort(key=lambda x: (x["avg_deviation"] is None, x["avg_deviation"] or 0))
        return out

    # ============================================================ 催办
    def urge(self, sub_id: int, *, operator: Any, message: str | None = None) -> dict:
        """行内催办（文档 3.2(5)：在行里就能直接改进度、直接催办）

        向子项目负责人发送催办通知：
        - 站内消息必发（负责人登录就能看到）
        - 负责人配置了邮箱则同时发邮件
        - 写一条 ProgRecord 留痕（is_urge 标记通过 note 前缀体现）

        去重：同一子项目 24 小时内不重复催办，避免管理者连点刷屏。
        """
        from app.models.system import NotifyLog

        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")

        assert_sub_access(operator, sub)

        if not sub.owner_id:
            raise BusinessError("该子项目未设置负责人，无法催办")

        # 24 小时内已催过则不重复
        since = datetime.now(UTC) - timedelta(hours=24)
        recent = self.db.scalar(
            select(func.count()).select_from(NotifyLog).where(
                NotifyLog.biz_type == "URGE",
                NotifyLog.biz_id == sub_id,
                NotifyLog.sent_at >= since,
            )
        )
        if recent:
            raise BusinessError("24 小时内已催办过，请勿重复催办")

        owner = self.db.get(SysUser, sub.owner_id)
        text = message or "进度滞后，请尽快更新进度"

        # 留痕：写进进度变更记录，便于追溯谁催过
        self.db.add(
            ProgRecord(
                sub_id=sub_id,
                progress=sub.current_progress,
                note=f"【催办】{operator.name}：{text}"[:500],
                operator_id=operator.id,
                is_no_change=True,
                is_rollback=False,
            )
        )

        from app.services.notify_service import NotifyService

        svc = NotifyService(self.db)
        channels = ["INNER"]
        if owner and (owner.email or "").strip():
            channels.append("EMAIL")

        log_ids = []
        for ch in channels:
            row = NotifyLog(
                biz_type="URGE",
                biz_id=sub_id,
                channel=ch,
                receiver_id=sub.owner_id,
                status="SUCCESS" if ch == "INNER" else "FAILED",
                error=None if ch == "INNER" else "待发送",
            )
            self.db.add(row)
            self.db.flush()
            log_ids.append(row.id)
            if ch != "INNER":
                svc.deliver(row.id)

        self.db.flush()
        return {
            "sub_id": sub_id,
            "sub_name": sub.name,
            "owner_id": sub.owner_id,
            "owner_name": owner.name if owner else None,
            "channels": channels,
            "message": text,
        }
