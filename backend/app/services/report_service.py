"""报表服务：填报、更正、作废、及时率统计、周报初稿

文档 4.3 时限：
- 日报：当日 24:00 前；逾期 3 日内可补填
- 周报：次周一 12:00 前；逾期 3 日内可补填
- 月报：次月第 3 个工作日 18:00 前；超过 3 日需学院负责人开启补填
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, NotFoundError
from app.core.scope import apply_scope
from app.models.project import ProgRecord, ProjSub
from app.models.report import Report
from app.services.calendar_service import CalendarService
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

REPORT_TYPES = ("DAILY", "WEEKLY", "MONTHLY")
TYPE_LABEL = {"DAILY": "日报", "WEEKLY": "周报", "MONTHLY": "月报"}


class ReportService:
    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService(db)
        self.cal = CalendarService.load(db)

    # ============================================================ 周期计算
    @staticmethod
    def week_start(d: date) -> date:
        return d - timedelta(days=d.weekday())

    @staticmethod
    def month_start(d: date) -> date:
        return d.replace(day=1)

    @staticmethod
    def month_end(d: date) -> date:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)

    def period_of(self, rtype: str, d: date) -> tuple[date, date]:
        if rtype == "DAILY":
            return d, d
        if rtype == "WEEKLY":
            s = self.week_start(d)
            return s, s + timedelta(days=6)
        if rtype == "MONTHLY":
            return self.month_start(d), self.month_end(d)
        raise BusinessError(f"未知报表类型：{rtype}")

    def deadline_of(self, rtype: str, period_start: date) -> datetime:
        """该期报表的提交截止时间"""
        if rtype == "DAILY":
            return datetime.combine(period_start + timedelta(days=1), time(0, 0), tzinfo=UTC)
        if rtype == "WEEKLY":
            # 次周一 12:00
            week_end = period_start + timedelta(days=6)
            next_monday = week_end + timedelta(days=1)
            return datetime.combine(next_monday, time(12, 0), tzinfo=UTC)
        if rtype == "MONTHLY":
            # 次月第 3 个工作日 18:00
            nxt = self.month_end(period_start) + timedelta(days=1)
            counted = 0
            d = nxt
            while counted < 3:
                if d.weekday() < 5 and not self.cal.is_free_day(d):
                    counted += 1
                    if counted == 3:
                        break
                d += timedelta(days=1)
            return datetime.combine(d, time(18, 0), tzinfo=UTC)
        raise BusinessError(f"未知报表类型：{rtype}")

    def is_due(self, rtype: str, sub: ProjSub, period_start: date, day: date) -> bool:
        """该期是否应该提交（文档 4.3 应填判定）"""
        period_end = self.period_of(rtype, period_start)[1]

        # 子项目必须处于进行中
        if sub.status not in ("IN_PROGRESS", "CLOSED"):
            return False
        if sub.status == "CLOSED" and sub.closed_at:
            # 结项后不再要求
            if sub.closed_at.date() < period_start:
                return False

        # 未开始的项目不要求
        if sub.plan_start and period_end < sub.plan_start:
            return False

        # 日报：节假日免填；周报/月报：当期工作日不少于 1 天
        if rtype == "DAILY":
            return not self.cal.is_free_day(period_start)

        workdays = sum(
            1 for d in self.cal.iter_days(period_start, period_end)
            if d.weekday() < 5 and not self.cal.is_free_day(d)
        )
        return workdays >= 1

    # ============================================================ 填报
    def submit(
        self,
        sub_id: int,
        rtype: str,
        content: dict,
        progress: int | None,
        operator: Any,
        period_start: date | None = None,
    ) -> Report:
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")
        if sub.status == "CLOSED":
            raise BusinessError("已结项的子项目不能再提交报表")

        if rtype not in REPORT_TYPES:
            raise BusinessError(f"未知报表类型：{rtype}")

        base_day = period_start or date.today()
        p_start, p_end = self.period_of(rtype, base_day)

        existing = self.db.scalar(
            select(Report).where(
                Report.sub_id == sub_id,
                Report.type == rtype,
                Report.period_start == p_start,
            )
        )
        if existing is not None and existing.status == "VOIDED":
            raise BusinessError("该期报表已作废，不能重复提交")

        now = datetime.now(UTC)
        if existing is None:
            rep = Report(
                sub_id=sub_id,
                type=rtype,
                period_start=p_start,
                period_end=p_end,
                progress=progress,
                content=content,
                submitted_at=now,
                submitted_by=operator.id,
                status="SUBMITTED",
            )
            self.db.add(rep)
        else:
            # 已提交：走更正逻辑
            self._check_correct_permission(existing, operator)
            rep = existing
            rep.content = content
            if progress is not None:
                rep.progress = progress
            rep.correct_count += 1

        # 报表中的进度同步到子项目（文档 3.4：共用字段）
        if progress is not None and not sub.status == "CLOSED":
            from app.services.progress_service import ProgressService

            ProgressService(self.db).update_progress(
                sub_id,
                progress=progress,
                note=f"{TYPE_LABEL[rtype]}同步",
                no_change=False,
                rollback_reason="报表填报同步" if progress < sub.current_progress else None,
                operator=operator,
            )

        self.db.flush()
        return rep

    def _check_correct_permission(self, rep: Report, operator: Any) -> None:
        """3 个自然日内可自更正；超过需学院负责人开权限（此处简化为需 project:sub:edit）"""
        days = self.config.get_int("report.correct_days", 3)
        if rep.submitted_at is None:
            return
        elapsed = (datetime.now(UTC) - rep.submitted_at).days
        if elapsed <= days:
            return
        # 超期：需要编辑权限（学院负责人及以上）
        if not operator.has_perm("project:sub:edit"):
            raise BusinessError(
                f"报表已超过 {days} 天更正期，需学院负责人开启修改权限"
            )

    # ============================================================ 作废
    def void(self, report_id: int, operator: Any, reason: str) -> Report:
        rep = self.db.get(Report, report_id)
        if rep is None:
            raise NotFoundError("报表不存在")
        if rep.status == "VOIDED":
            raise BusinessError("报表已作废")
        if not reason or not reason.strip():
            raise BusinessError("作废必须填写理由")
        if not operator.has_perm("report:void"):
            raise BusinessError("无作废权限（仅系统管理员）")

        rep.status = "VOIDED"
        rep.void_reason = reason
        rep.voided_by = operator.id
        rep.voided_at = datetime.now(UTC)

        # 撤销该报表触发的未处置 AL-06/AL-07 预警（文档 3.4）
        from app.models.alert import Alert

        alerts = list(
            self.db.scalars(
                select(Alert).where(
                    Alert.sub_id == rep.sub_id,
                    Alert.alert_type.in_(("AL-06", "AL-07")),
                    Alert.status.in_(("PENDING", "CLAIMED")),
                )
            )
        )
        for a in alerts:
            a.status = "CLOSED"
            a.closed_by = operator.id
            a.closed_at = datetime.now(UTC)
            a.close_reason = f"报表 {rep.id} 已作废，自动撤销"

        self.db.flush()
        return rep

    # ============================================================ 周报初稿
    def generate_weekly_draft(self, sub_id: int, week_start: date | None = None) -> dict:
        """周报初稿：由本周期进度记录 + 上期周报未完成计划拼成（文档 3.4）

        日报关闭时用进度记录拼；日报开启时用日报拼。
        """
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")

        ws = week_start or self.week_start(date.today())
        we = ws + timedelta(days=6)

        records = list(
            self.db.scalars(
                select(ProgRecord)
                .where(
                    ProgRecord.sub_id == sub_id,
                    ProgRecord.created_at >= datetime.combine(ws, time(0, 0), tzinfo=UTC),
                    ProgRecord.created_at < datetime.combine(we + timedelta(days=1), time(0, 0), tzinfo=UTC),
                )
                .order_by(ProgRecord.created_at)
            )
        )

        # 本周期进度变化
        changes = [
            f"{r.created_at.strftime('%m-%d')}：{r.progress}%" + (f"（{r.note}）" if r.note else "")
            for r in records
        ]

        # 上期周报的"下周计划"
        last_week = ws - timedelta(days=7)
        last = self.db.scalar(
            select(Report).where(
                Report.sub_id == sub_id,
                Report.type == "WEEKLY",
                Report.period_start == last_week,
                Report.status == "SUBMITTED",
            )
        )
        last_plan = ""
        if last and isinstance(last.content, dict):
            last_plan = last.content.get("next_week", "")

        # 进度变化幅度
        if records:
            start_p = records[0].progress
            end_p = records[-1].progress
            delta = end_p - start_p
        else:
            start_p = end_p = sub.current_progress
            delta = 0

        draft = {
            "summary": "\n".join(changes) if changes else "本周期无进度变化记录",
            "next_week": last_plan or "",
            "progress": sub.current_progress,
            "progress_delta": delta,
        }
        return draft

    # ============================================================ 查询
    def list_reports(
        self,
        user: Any,
        *,
        rtype: str | None = None,
        sub_id: int | None = None,
        major_id: int | None = None,
        status: str | None = None,
        submitted_from: date | None = None,
        submitted_to: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Report], int]:
        stmt: Select = select(Report).join(ProjSub, ProjSub.id == Report.sub_id).where(
            ProjSub.deleted_at.is_(None)
        )
        stmt = apply_scope(stmt, user, ProjSub)

        if rtype:
            stmt = stmt.where(Report.type == rtype)
        if sub_id:
            stmt = stmt.where(Report.sub_id == sub_id)
        if major_id:
            stmt = stmt.where(ProjSub.major_id == major_id)
        if status:
            stmt = stmt.where(Report.status == status)
        if submitted_from:
            stmt = stmt.where(Report.submitted_at >= datetime.combine(submitted_from, time(0, 0), tzinfo=UTC))
        if submitted_to:
            stmt = stmt.where(Report.submitted_at < datetime.combine(submitted_to + timedelta(days=1), time(0, 0), tzinfo=UTC))

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.db.scalars(
                stmt.order_by(Report.period_start.desc(), Report.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def get(self, report_id: int) -> Report:
        rep = self.db.get(Report, report_id)
        if rep is None:
            raise NotFoundError("报表不存在")
        return rep

    # ============================================================ 待填清单
    def pending_list(self, user: Any, day: date | None = None) -> list[dict]:
        """我该填但还没填的报表"""
        day = day or date.today()
        stmt = select(ProjSub).where(
            ProjSub.deleted_at.is_(None), ProjSub.status == "IN_PROGRESS"
        )
        stmt = apply_scope(stmt, user, ProjSub)
        subs = list(self.db.scalars(stmt))

        daily_enabled = self.config.get_bool("report.daily.enabled", False)
        types = ["WEEKLY", "MONTHLY"]
        if daily_enabled:
            types.insert(0, "DAILY")

        out = []
        for sub in subs:
            for rtype in types:
                p_start, p_end = self.period_of(rtype, day)
                if not self.is_due(rtype, sub, p_start, day):
                    continue
                deadline = self.deadline_of(rtype, p_start)
                if datetime.now(UTC) < deadline:
                    continue
                exists = self.db.scalar(
                    select(Report).where(
                        Report.sub_id == sub.id,
                        Report.type == rtype,
                        Report.period_start == p_start,
                        Report.status == "SUBMITTED",
                    )
                )
                if exists:
                    continue
                out.append(
                    {
                        "sub_id": sub.id,
                        "sub_name": sub.name,
                        "type": rtype,
                        "type_label": TYPE_LABEL[rtype],
                        "period_start": p_start,
                        "period_end": p_end,
                        "deadline": deadline,
                        "overdue_days": (datetime.now(UTC) - deadline).days,
                    }
                )
        return out

    # ============================================================ 及时率
    def timeliness(
        self,
        user: Any,
        *,
        start: date,
        end: date,
        rtype: str | None = None,
        major_id: int | None = None,
    ) -> dict:
        """报表及时率 = 按期提交数 ÷ 应提交数（文档 4.3）

        分子：按期提交，不含逾期补填
        分母：应提交，不含未启动/已结项、免填日期、已作废
        """
        subs_stmt = select(ProjSub).where(ProjSub.deleted_at.is_(None))
        subs_stmt = apply_scope(subs_stmt, user, ProjSub)
        if major_id:
            subs_stmt = subs_stmt.where(ProjSub.major_id == major_id)
        subs = list(self.db.scalars(subs_stmt))
        sub_map = {s.id: s for s in subs}

        daily_enabled = self.config.get_bool("report.daily.enabled", False)
        types = [rtype] if rtype else (["DAILY", "WEEKLY", "MONTHLY"] if daily_enabled else ["WEEKLY", "MONTHLY"])

        total_due = 0
        on_time = 0
        late = 0
        missing = 0
        detail: list[dict] = []

        for s in subs:
            for rt in types:
                # 逐期枚举
                p = self.period_of(rt, start)[0]
                while p <= end:
                    p_start, p_end = self.period_of(rt, p)
                    # 只统计「截止时间已过」的期次，未到期的不计入分母
                    deadline = self.deadline_of(rt, p_start)
                    deadline_passed = deadline <= datetime.now(UTC)
                    if deadline_passed and self.is_due(rt, s, p_start, end):
                        total_due += 1
                        rep = self.db.scalar(
                            select(Report).where(
                                Report.sub_id == s.id,
                                Report.type == rt,
                                Report.period_start == p_start,
                            )
                        )
                        row = {
                            "sub_id": s.id,
                            "sub_name": s.name,
                            "major_id": s.major_id,
                            "college_id": s.college_id,
                            "type": rt,
                            "type_label": TYPE_LABEL.get(rt, rt),
                            "period_start": p_start,
                            "period_end": p_end,
                            "deadline": deadline,
                            "report_id": rep.id if rep else None,
                        }
                        if rep is None or rep.status == "VOIDED":
                            missing += 1
                            row["state"] = "MISSING"
                            row["state_label"] = "缺交"
                            row["submitted_at"] = None
                            row["overdue_days"] = (datetime.now(UTC) - deadline).days
                        elif rep.submitted_at and rep.submitted_at <= deadline:
                            on_time += 1
                            row["state"] = "ON_TIME"
                            row["state_label"] = "按期"
                            row["submitted_at"] = rep.submitted_at
                            row["overdue_days"] = 0
                        else:
                            late += 1
                            row["state"] = "LATE"
                            row["state_label"] = "迟交"
                            row["submitted_at"] = rep.submitted_at
                            row["overdue_days"] = (
                                (rep.submitted_at - deadline).days if rep.submitted_at else 0
                            )
                        detail.append(row)
                    p = self.period_of(rt, p_end + timedelta(days=1))[0]

        rate = round(on_time / total_due * 100, 1) if total_due else None
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "types": types,
            "should_submit": total_due,
            "on_time": on_time,
            "late": late,
            "missing": missing,
            "rate": rate,
            "detail": detail[:100],
            # 完整明细供 timeliness_detail 分页使用（不直接暴露给 API）
            "detail_all": detail,
        }

    def timeliness_detail(
        self,
        user: Any,
        *,
        start: date,
        end: date,
        rtype: str | None = None,
        major_id: int | None = None,
        state: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """及时率明细（分页 + 按状态筛选），供前端逐条查看与导出"""
        full = self.timeliness(user, start=start, end=end, rtype=rtype, major_id=major_id)
        rows = full["detail_all"] if "detail_all" in full else full["detail"]

        if state:
            rows = [r for r in rows if r["state"] == state]

        # 排序：缺交 > 迟交 > 按期，同状态按期间倒序
        order = {"MISSING": 0, "LATE": 1, "ON_TIME": 2}

        def _sort_key(r: dict):
            ps = r.get("period_start")
            # period_start 在 timeliness() 里是 date 对象，此处兼容字符串
            ordinal = ps.toordinal() if hasattr(ps, "toordinal") else 0
            return (order.get(r.get("state"), 9), -ordinal)

        rows.sort(key=_sort_key)
        total = len(rows)
        offset = (page - 1) * page_size
        return rows[offset : offset + page_size], total
