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
from app.core.scope import apply_scope, assert_sub_access
from app.models.project import ProgRecord, ProjSub
from app.models.report import Report, ReportGrace
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
        """该期报表的提交截止时间

        周报/月报的截止星期与时刻都读参数，可在「参数配置」页调整：
        - report.weekly.deadline_weekday / report.weekly.deadline_hour
        - report.monthly.deadline_workday / report.monthly.deadline_hour
        原来这里是硬编码（次周一 12:00、次月第 3 个工作日 18:00），
        导致这 4 个参数改了不生效。
        """
        if rtype == "DAILY":
            return datetime.combine(period_start + timedelta(days=1), time(0, 0), tzinfo=UTC)
        if rtype == "WEEKLY":
            # 默认次周一 12:00；weekday 1=周一 ... 7=周日
            weekday = self.config.get_int("report.weekly.deadline_weekday", 1)
            hour = self.config.get_int("report.weekly.deadline_hour", 12)
            week_end = period_start + timedelta(days=6)
            # 从周结束次日起找目标星期
            target = max(1, min(7, weekday))
            d = week_end + timedelta(days=1)
            for _ in range(7):
                if d.isoweekday() == target:
                    break
                d += timedelta(days=1)
            return datetime.combine(d, time(max(0, min(23, hour)), 0), tzinfo=UTC)
        if rtype == "MONTHLY":
            # 默认次月第 3 个工作日 18:00
            workday = max(1, self.config.get_int("report.monthly.deadline_workday", 3))
            hour = max(0, min(23, self.config.get_int("report.monthly.deadline_hour", 18)))
            nxt = self.month_end(period_start) + timedelta(days=1)
            counted = 0
            d = nxt
            while True:
                if d.weekday() < 5 and not self.cal.is_free_day(d):
                    counted += 1
                    if counted >= workday:
                        break
                d += timedelta(days=1)
            return datetime.combine(d, time(hour, 0), tzinfo=UTC)
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
        # 数据范围：只能给有权访问的子项目提交报表（防水平越权）
        assert_sub_access(operator, sub)
        if sub.status == "CLOSED":
            raise BusinessError("已结项的子项目不能再提交报表")

        if rtype not in REPORT_TYPES:
            raise BusinessError(f"未知报表类型：{rtype}")

        base_day = period_start or date.today()
        # 不允许提交未来周期（周期尚未开始）
        if base_day > date.today():
            raise BusinessError("不能提交未来周期的报表")
        p_start, p_end = self.period_of(rtype, base_day)

        # 截止 + 补填期校验：超过「截止时间 + report.correct_days 天」不再接收。
        # 早期不校验，既能补交很久以前的报表，也能提交未来周期。
        # 超期后若学院负责人开了补填（report_grace），仍可提交。
        deadline = self.deadline_of(rtype, p_start)
        grace_days = max(0, self.config.get_int("report.correct_days", 3))
        if datetime.now(UTC) > deadline + timedelta(days=grace_days):
            if not self._has_grace(sub_id, rtype, p_start):
                raise BusinessError(
                    f"已超过补填期（截止 {deadline.strftime('%Y-%m-%d %H:%M')}，"
                    f"可补填 {grace_days} 天），如需提交请联系学院负责人开启补填"
                )

        existing = self.db.scalar(
            select(Report).where(
                Report.sub_id == sub_id,
                Report.type == rtype,
                Report.period_start == p_start,
            )
        )
        # 已作废的报表允许重新提交：业务上「作废」就是为了重填。
        # 原来这里直接 raise，导致作废后该期永久缺报 —— AL-06/AL-07 被撤销后
        # 又因缺报重新生成，形成「作废 → 缺报 → 告警 → 补不了」的死循环。
        is_revive = existing is not None and existing.status == "VOIDED"
        if is_revive:
            existing.status = "SUBMITTED"
            existing.void_reason = None
            existing.voided_by = None
            existing.voided_at = None
            existing.submitted_at = datetime.now(UTC)
            existing.submitted_by = operator.id

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
            # 已提交：走更正逻辑（作废后重填同样走这里，只是不再校验更正时限）
            if not is_revive:
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

    # ============================================================ 补填放行
    def _has_grace(self, sub_id: int, rtype: str, period_start: date) -> bool:
        """该周期是否已被放行补填（未撤销且未过期）"""
        from app.models.report import ReportGrace

        today = date.today()
        stmt = select(ReportGrace).where(
            ReportGrace.sub_id == sub_id,
            ReportGrace.type == rtype,
            ReportGrace.revoked_at.is_(None),
            ReportGrace.expires_on >= today,
            # period_start 为空表示放行该类型所有历史周期
            (ReportGrace.period_start.is_(None))
            | (ReportGrace.period_start == period_start),
        )
        return self.db.scalar(stmt) is not None

    def grant_grace(
        self,
        sub_id: int,
        rtype: str,
        *,
        period_start: date | None,
        reason: str,
        days: int,
        operator: Any,
    ) -> dict:
        """开启逾期补填（文档 4.3：月报超 3 天需学院负责人开）

        Args:
            period_start: 为空表示放行该子项目该类型的所有历史周期
            days: 放行有效天数
        """
        from app.models.report import ReportGrace

        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")
        assert_sub_access(operator, sub)

        if rtype not in REPORT_TYPES:
            raise BusinessError(f"未知报表类型：{rtype}")
        if not reason or not reason.strip():
            raise BusinessError("开启补填必须填写理由")
        days = max(1, min(90, days))

        # 同一范围已有有效放行则不重复创建
        today = date.today()
        exist = self.db.scalar(
            select(ReportGrace).where(
                ReportGrace.sub_id == sub_id,
                ReportGrace.type == rtype,
                ReportGrace.period_start.is_(None)
                if period_start is None
                else ReportGrace.period_start == period_start,
                ReportGrace.revoked_at.is_(None),
                ReportGrace.expires_on >= today,
            )
        )
        if exist is not None:
            raise BusinessError("该周期已有生效中的补填放行")

        row = ReportGrace(
            sub_id=sub_id,
            type=rtype,
            period_start=period_start,
            reason=reason.strip(),
            granted_by=operator.id,
            expires_on=today + timedelta(days=days),
        )
        self.db.add(row)
        self.db.flush()
        return {
            "id": row.id,
            "sub_id": sub_id,
            "sub_name": sub.name,
            "type": rtype,
            "period_start": period_start,
            "expires_on": row.expires_on,
            "days": days,
        }

    def revoke_grace(self, grace_id: int, operator: Any) -> None:
        """撤销补填放行"""
        from app.models.report import ReportGrace

        row = self.db.get(ReportGrace, grace_id)
        if row is None:
            raise NotFoundError("放行记录不存在")
        if row.revoked_at is not None:
            raise BusinessError("该放行已撤销")
        sub = self.db.get(ProjSub, row.sub_id)
        if sub is not None:
            assert_sub_access(operator, sub)
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = operator.id
        self.db.flush()

    def list_graces(self, user: Any, sub_id: int | None = None) -> list[dict]:
        """补填放行记录（生效中的排前面）"""
        from app.models.report import ReportGrace
        from app.models.system import SysUser

        stmt = select(ReportGrace).order_by(ReportGrace.id.desc())
        if sub_id:
            stmt = stmt.where(ReportGrace.sub_id == sub_id)

        # 数据范围：只列自己有权访问的子项目
        subs_stmt = select(ProjSub.id).where(ProjSub.deleted_at.is_(None))
        subs_stmt = apply_scope(subs_stmt, user, ProjSub)
        visible = set(self.db.scalars(subs_stmt))
        sub_names = {
            s.id: s.name
            for s in self.db.scalars(select(ProjSub).where(ProjSub.deleted_at.is_(None)))
        }
        user_names = {u.id: u.name for u in self.db.scalars(select(SysUser))}

        today = date.today()
        out = []
        for r in self.db.scalars(stmt):
            if r.sub_id not in visible:
                continue
            active = r.revoked_at is None and r.expires_on >= today
            out.append(
                {
                    "id": r.id,
                    "sub_id": r.sub_id,
                    "sub_name": sub_names.get(r.sub_id, f"#{r.sub_id}"),
                    "type": r.type,
                    "type_label": TYPE_LABEL.get(r.type, r.type),
                    "period_start": r.period_start,
                    "reason": r.reason,
                    "granted_by": r.granted_by,
                    "granted_by_name": user_names.get(r.granted_by),
                    "granted_at": r.granted_at,
                    "expires_on": r.expires_on,
                    "is_active": active,
                }
            )
        out.sort(key=lambda x: (not x["is_active"], x["expires_on"]))
        return out

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

    def periods_between(
        self, rtype: str, start: date, end: date
    ) -> list[tuple[date, date]]:
        """枚举 [start, end] 区间内的所有报表周期（按周期起始日升序）

        用于回溯历史逾期：从子项目计划开始起，逐周期检查是否漏填。
        对齐到周期边界，避免 start 落在周期中间时漏掉该周期。
        """
        if rtype == "DAILY":
            step_days = 1
        elif rtype == "WEEKLY":
            step_days = 7
        elif rtype == "MONTHLY":
            step_days = 0  # 按月推进，单独处理
        else:
            raise BusinessError(f"未知报表类型：{rtype}")

        out: list[tuple[date, date]] = []
        cur = self.period_of(rtype, start)[0]
        guard = 0
        # 上限保护：日报回溯 10 年也不过 3650 条，这里给 5000 兜底
        while cur <= end and guard < 5000:
            guard += 1
            p_start, p_end = self.period_of(rtype, cur)
            out.append((p_start, p_end))
            if step_days:
                cur = p_start + timedelta(days=step_days)
            else:
                # 月报：跳到下月 1 号
                cur = self.month_end(p_start) + timedelta(days=1)
        return out

    # ============================================================ 待填清单
    def pending_list(
        self,
        user: Any,
        day: date | None = None,
        include_upcoming: bool = True,
        upcoming_days: int = 3,
    ) -> list[dict]:
        """我该填但还没填的报表

        两类都返回，用 status 区分：

        - OVERDUE    已过截止时间仍未提交（需要催）
        - UPCOMING   已进入填报窗口、还没到截止（提前提醒，避免逾期）

        回溯范围：从子项目计划开始日起，逐周期检查到当前周期。
        早期实现只看「今天所在的周期」，导致逾期报表一旦跨过周期就从
        清单里彻底消失——教师漏交一次，之后再也看不到，也就永远补不上。

        Args:
            include_upcoming: 是否包含尚未到截止的（默认包含）
            upcoming_days: 提前多少天开始提醒（默认 3 天）
        """
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

        now = datetime.now(UTC)
        upcoming_cutoff = now + timedelta(days=max(0, upcoming_days))

        # 已提交的（子项目, 类型, 周期）集合，一次性查出来避免 N+1
        submitted: set[tuple[int, str, date]] = {
            (r.sub_id, r.type, r.period_start)
            for r in self.db.scalars(
                select(Report).where(Report.status == "SUBMITTED")
            )
        }

        out = []
        for sub in subs:
            # 回溯起点：优先用计划开始日；没有计划则只看当前周期
            # （避免给没有计划期的项目生成一大堆历史逾期）
            for rtype in types:
                if sub.plan_start:
                    periods = self.periods_between(rtype, sub.plan_start, day)
                else:
                    periods = [self.period_of(rtype, day)]

                for p_start, p_end in periods:
                    if not self.is_due(rtype, sub, p_start, day):
                        continue

                    deadline = self.deadline_of(rtype, p_start)
                    is_overdue = now >= deadline
                    if not is_overdue:
                        # 未到截止：只在 include_upcoming 且已进入提醒窗口时返回
                        if not include_upcoming or deadline > upcoming_cutoff:
                            continue

                    if (sub.id, rtype, p_start) in submitted:
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
                            # OVERDUE 已逾期 / UPCOMING 临近截止
                            "status": "OVERDUE" if is_overdue else "UPCOMING",
                            "overdue_days": (now - deadline).days if is_overdue else 0,
                            # 未逾期时为距截止还剩几天，便于前端显示「还剩 N 天」
                            "days_left": 0 if is_overdue else (deadline - now).days,
                        }
                    )

        # 逾期在前；同状态内按截止时间升序（最急的排最前）
        out.sort(key=lambda x: (x["status"] != "OVERDUE", x["deadline"]))
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

        # 批量取出期间内所有报表（避免 子项目×期次 的 N+1 查询）。
        # 下界放宽 40 天：周/月期的 period_start 可能早于统计起始日
        # （如起始日在周中，该周周一早于 start）。
        reports: dict[tuple[int, str, date], Report] = {}
        if subs:
            for r in self.db.scalars(
                select(Report).where(
                    Report.sub_id.in_([s.id for s in subs]),
                    Report.type.in_(types),
                    Report.period_start >= start - timedelta(days=40),
                    Report.period_start <= end,
                )
            ):
                reports[(r.sub_id, r.type, r.period_start)] = r

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
                        rep = reports.get((s.id, rt, p_start))
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
