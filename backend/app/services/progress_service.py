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

# 「进度更新及时率」的更新间隔（天）
# 需求 4.3 / 6.3 第 18 项：每 14 天至少更新 1 次。早期代码写死 21 天。
# 参数 progress.update_interval_days 由 Lead 在 session.py 基线里加；
# 这里读不到时回退常量 14（不因参数缺失把口径退回 21 天）。
_UPDATE_INTERVAL_DAYS = 14


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
        event_type: str = "FILL",
    ) -> dict:
        """更新进度（写留痕 + 更新子项目当前值）

        event_type 用于区分留痕来源（P1-7/P1-8）：
        - FILL  ：正常填报（人工在进度总表/详情页填的）
        - URGE  ：催办记录（不是填报，不能污染 last_note / 周报初稿）
        - REPORT：报表提交时同步进度（同理，不是人工填报说明）
        早期所有记录都写进 ProgRecord，`last_note` 取最近一条，
        催办一次后负责人下次填报的说明框默认变成「【催办】…」。
        """
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

        # 代填判定（P1-12）：操作人不是负责人时，必须显式持有 progress:onbehalf，
        # 否则 403「无代填权限」。
        # 早期只要「有 project:sub:edit」就默认代填，导致「代填」权限点从未被校验，
        # 任何能改进度的人都能悄悄替负责人填。
        on_behalf_of = None
        if sub.owner_id and operator.id != sub.owner_id:
            if not operator.has_perm("progress:onbehalf"):
                raise ForbiddenError("无代填权限：只能由负责人本人填报，或由学院负责人代填")
            on_behalf_of = sub.owner_id

        # 回调判定
        is_rollback = progress < sub.current_progress
        if is_rollback and not (rollback_reason and rollback_reason.strip()):
            raise BusinessError("进度回调必须填写原因")

        rec = ProgRecord(
            sub_id=sub_id,
            progress=progress,
            note=note,
            # 报表同步/催办等非人工填报记录，不能当「上次说明」用（P1-7/P1-8）
            event_type=event_type,
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
            "event_type": event_type,
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
        # ★ 只取 event_type='FILL' 的记录（P1-7）：
        # 催办(URGE)与报表同步(REPORT)也会写 ProgRecord，若不过滤，
        # 负责人下次填报时说明框会被自动带成「【催办】…」。
        if last_record is _UNSET:
            last_record = self.db.scalar(
                select(ProgRecord)
                .where(
                    ProgRecord.sub_id == sub.id,
                    ProgRecord.event_type == "FILL",
                )
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

        # 批量取每个子项目最近一次**人工填报**（避免 N+1：原来每个子项目一条 SQL）
        # 只取 FILL：URGE/REPORT 记录不是「上次说明」，否则表格里显示的说明
        # 会是「【催办】…」或「周报同步」（P1-7/P1-8）。
        last_records: dict[int, ProgRecord] = {}
        if subs:
            for r in self.db.scalars(
                select(ProgRecord)
                .where(
                    ProgRecord.sub_id.in_([s.id for s in subs]),
                    ProgRecord.event_type == "FILL",
                )
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
    def major_summary(self, major_id: int, user: Any | None = None) -> dict:
        agg = AggregationService(self.db)
        major = self.db.get(ProjMajor, major_id)
        if major is None or major.deleted_at is not None:
            raise NotFoundError("大项目不存在")
        # user 非空时按数据范围收敛（P0-3）：接口层已做 get_major 范围校验，
        # 这里再收敛子项目，避免学院负责人看到其他学院的进度构成。
        data = agg.breakdown(major_id, user=user)
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
    def _update_interval_days(self) -> int:
        """读取「进度更新间隔」参数（默认 14 天）

        需求 4.3 / 6.3 第 18 项：每 14 天至少更新 1 次。早期代码写死 21 天，
        与需求不符，导致「及时率」被系统性高估。
        参数 progress.update_interval_days 若未配置（session.py 基线由 Lead 维护），
        回退常量 14，绝不退回 21。
        """
        from app.services.config_service import ConfigService

        try:
            days = ConfigService(self.db).get_int(
                "progress.update_interval_days", _UPDATE_INTERVAL_DAYS
            )
        except Exception:  # 参数表缺失等异常不应影响统计
            days = _UPDATE_INTERVAL_DAYS
        return max(1, days)

    def college_compare(self, user: Any, major_id: int | None = None) -> list[dict]:
        """学院横向对比（文档 3.2(6)）

        每个学院一行：
        - avg_deviation   平均偏差（按子项目简单平均）
        - lagging_count   滞后子项目数（偏差 < -10%）
        - severe_count    严重滞后数（偏差 <= -25%）
        - total_subs      计入统计的子项目数
        - update_rate     更新及时率（interval 个有效日内更新过的占比）

        口径修正（P1-6）：21 天 → 参数 progress.update_interval_days（默认 14）。
        """
        interval_days = self._update_interval_days()
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
            # 21 → interval_days（P1-6）：口径与需求「每 14 天至少更新一次」对齐
            fresh = sum(1 for x in updates if x <= interval_days)
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
                    "update_interval_days": interval_days,
                }
            )

        # 平均偏差从小到大（最滞后在前）
        out.sort(key=lambda x: (x["avg_deviation"] is None, x["avg_deviation"] or 0))
        return out

    def update_timeliness(
        self,
        user: Any,
        start: date,
        end: date,
        *,
        major_id: int | None = None,
        college_id: int | None = None,
    ) -> dict:
        """进度更新及时率（P1-6，需求 4.3 口径：每 14 天至少更新 1 次）

        分母 = 统计期内「应更新」的子项目数：
              剔除 ABORTED / NOT_STARTED / 计划开始晚于 end / 已结项（closed_at < start）
        分子 = 统计期内至少更新过一次的子项目数：
              以 `last_update_at` 判断——只要最近一次更新落在统计期内即可
              （若最近一次更新在 start 之前，说明整个统计期一次没更新）
        返回 {rate, should_update, updated, detail}
        """
        interval_days = self._update_interval_days()
        # 参数校验：start/end 反了直接按交换处理（前端日期区间组件偶发）
        if start > end:
            start, end = end, start

        stmt = select(ProjSub).where(ProjSub.deleted_at.is_(None))
        stmt = apply_scope(stmt, user, ProjSub)
        if major_id:
            stmt = stmt.where(ProjSub.major_id == major_id)
        if college_id:
            stmt = stmt.where(ProjSub.college_id == college_id)
        subs = list(self.db.scalars(stmt))

        colleges = {c.id: c.name for c in self.db.scalars(select(SysCollege))}
        majors = {m.id: m.name for m in self.db.scalars(select(ProjMajor))}

        detail: list[dict] = []
        should_update = 0
        updated = 0
        for s in subs:
            # 剔除不参与统计的状态：中止/未开始不计入分母
            if s.status in ("ABORTED", "NOT_STARTED"):
                continue
            # 计划还没开始（plan_start 晚于统计期末）→ 本期不应更新
            if s.plan_start and s.plan_start > end:
                continue
            # 统计期开始前就结项 → 本期无需更新（结项后本来就不许再改进度）
            if s.status == "CLOSED" and s.closed_at and s.closed_at.date() < start:
                continue

            should_update += 1
            last = s.last_update_at.astimezone(UTC).date() if s.last_update_at else None
            is_updated = last is not None and start <= last <= end
            if is_updated:
                updated += 1
            detail.append(
                {
                    "sub_id": s.id,
                    "sub_name": s.name,
                    "major_id": s.major_id,
                    "major_name": majors.get(s.major_id),
                    "college_id": s.college_id,
                    "college_name": colleges.get(s.college_id),
                    "last_update_at": s.last_update_at,
                    "updated": is_updated,
                }
            )

        detail.sort(key=lambda x: (x["updated"], x["sub_name"] or ""))
        return {
            "rate": round(updated / should_update * 100, 1) if should_update else None,
            "should_update": should_update,
            "updated": updated,
            "update_interval_days": interval_days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "detail": detail,
        }

    # ============================================================ 催办
    def urge(self, sub_id: int, *, operator: Any, message: str | None = None) -> dict:
        """行内催办（文档 3.2(5)：在行里就能直接改进度、直接催办）

        向子项目负责人发送催办通知：
        - 站内消息必发（负责人登录就能看到）
        - 负责人配置了邮箱则同时发邮件
        - 写一条 ProgRecord 留痕，event_type='URGE'（P1-7）

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

        # 留痕：写进进度变更记录，便于追溯谁催过。
        # ★ event_type='URGE'（P1-7）：催办不是填报，不能被 last_note / 周报初稿
        # 当成「上次说明」。早期这里不写 event_type，默认落 FILL 造成污染。
        self.db.add(
            ProgRecord(
                sub_id=sub_id,
                progress=sub.current_progress,
                note=f"【催办】{operator.name}：{text}"[:500],
                event_type="URGE",
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

    def list_urges(self, sub_id: int, limit: int = 100) -> list[dict]:
        """子项目催办记录（P1-10，需求 6.3 第 25 项）

        从 ProgRecord 里取 event_type='URGE' 的记录：催办也写留痕，
        但只有 URGE 类型才是「催办历史」，正常填报(FILL)不能混进来。
        """
        rows = list(
            self.db.scalars(
                select(ProgRecord)
                .where(
                    ProgRecord.sub_id == sub_id,
                    ProgRecord.event_type == "URGE",
                )
                .order_by(ProgRecord.created_at.desc(), ProgRecord.id.desc())
                .limit(limit)
            )
        )
        # 批量取操作人姓名，避免 N+1
        op_ids = {r.operator_id for r in rows if r.operator_id}
        users = (
            {u.id: u.name for u in self.db.scalars(select(SysUser).where(SysUser.id.in_(op_ids)))}
            if op_ids
            else {}
        )
        return [
            {
                "id": r.id,
                "note": r.note,
                "operator_id": r.operator_id,
                "operator_name": users.get(r.operator_id),
                "created_at": r.created_at,
            }
            for r in rows
        ]

    def urge_stats(self, user: Any, days: int = 30) -> list[dict]:
        """催办统计（P1-10）：按子项目聚合近 N 天催办次数

        数据范围：沿用 apply_scope（只统计本人有权看到的子项目）。
        返回 [{sub_id, sub_name, count, last_at}]，按次数倒序。
        """
        days = max(1, min(days, 3650))
        since = datetime.now(UTC) - timedelta(days=days)

        stmt = (
            select(
                ProgRecord.sub_id,
                func.count(ProgRecord.id).label("cnt"),
                func.max(ProgRecord.created_at).label("last_at"),
            )
            .where(
                ProgRecord.event_type == "URGE",
                ProgRecord.created_at >= since,
            )
            .group_by(ProgRecord.sub_id)
        )
        rows = list(self.db.execute(stmt).all())
        if not rows:
            return []

        sub_ids = [r[0] for r in rows]
        # 只保留本人范围内的子项目（数据范围校验，避免越权看到别人项目的催办数）
        scope_stmt = select(ProjSub.id).where(ProjSub.id.in_(sub_ids))
        scope_stmt = apply_scope(scope_stmt, user, ProjSub)
        allowed = set(self.db.scalars(scope_stmt))
        subs = {
            s.id: s
            for s in self.db.scalars(select(ProjSub).where(ProjSub.id.in_(allowed)))
        }
        out = []
        for sub_id, cnt, last_at in rows:
            s = subs.get(sub_id)
            if s is None:
                continue
            out.append(
                {
                    "sub_id": sub_id,
                    "sub_name": s.name,
                    "count": int(cnt),
                    "last_at": last_at,
                }
            )
        out.sort(key=lambda x: (-x["count"], x["sub_id"]))
        return out
