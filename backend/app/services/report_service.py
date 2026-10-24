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
from app.core.scope import apply_scope, assert_report_access, assert_sub_access
from app.models.project import ProgRecord, ProjMajor, ProjSub
from app.models.report import Report, ReportGrace
from app.services.calendar_service import SHANGHAI, CalendarService, today_local
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

REPORT_TYPES = ("DAILY", "WEEKLY", "MONTHLY")
TYPE_LABEL = {"DAILY": "日报", "WEEKLY": "周报", "MONTHLY": "月报"}
# 放行类型：BACKFILL=逾期补填，CORRECT=已提交报表的超期更正
GRACE_KINDS = ("BACKFILL", "CORRECT")
GRACE_LABEL = {"BACKFILL": "补填", "CORRECT": "更正"}

# 报表内容必填项（与 api/v1/report.py 的 DailyContent/WeeklyContent/MonthlyContent 一致）。
# 放在服务层做一次兜底校验：早期 submit() 直接收 dict，绕过前端就能提交 {}。
CONTENT_REQUIRED: dict[str, tuple[tuple[str, str], ...]] = {
    "DAILY": (("today", "今天做了什么"),),
    "WEEKLY": (("summary", "本周进展"),),
    "MONTHLY": (("completed", "本月完成"),),
}


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
        """该期报表的提交截止时间（统一返回 UTC aware）

        周报/月报的截止星期与时刻都读参数，可在「参数配置」页调整：
        - report.weekly.deadline_weekday / report.weekly.deadline_hour
        - report.monthly.deadline_workday / report.monthly.deadline_hour

        【时区修复】配置里的 12:00 / 18:00 / 24:00 是**北京时间**（文档与
        前端界面口径），早期却用 `tzinfo=UTC` 构造，等于把北京时间当成 UTC，
        整体晚 8 小时：周报实际截止北京 20:00、月报次日 02:00、日报次日 08:00，
        及时率的 ON_TIME/LATE 边界、补填期、逾期提醒全部跟着偏移。
        现在统一用 ZoneInfo("Asia/Shanghai") 构造本地时刻，再转 UTC 返回，
        保证存储/比较仍是一套 UTC aware 值。
        """
        if rtype == "DAILY":
            # 日报：当地次日 00:00（北京）→ UTC 前一日 16:00
            return datetime.combine(
                period_start + timedelta(days=1), time(0, 0), tzinfo=SHANGHAI
            ).astimezone(UTC)
        if rtype == "WEEKLY":
            # 默认次周一 12:00（北京）→ UTC 04:00；weekday 1=周一 ... 7=周日
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
            return datetime.combine(
                d, time(max(0, min(23, hour)), 0), tzinfo=SHANGHAI
            ).astimezone(UTC)
        if rtype == "MONTHLY":
            # 默认次月第 3 个工作日 18:00（北京）→ UTC 10:00
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
            return datetime.combine(
                d, time(hour, 0), tzinfo=SHANGHAI
            ).astimezone(UTC)
        raise BusinessError(f"未知报表类型：{rtype}")

    # ============================================================ 日报开关（按项目）
    def global_daily_enabled(self) -> bool:
        """全局日报开关（参数 report.daily.enabled，默认关闭）"""
        return self.config.get_bool("report.daily.enabled", False)

    def daily_enabled_for(self, sub: ProjSub, major: ProjMajor | None = None) -> bool:
        """某个子项目是否要求交日报（需求 6.1 第 5 项：按项目开关）

        判定顺序：
        1. 项目显式 False → 关闭（项目级优先，允许试点项目单独关掉）
        2. 项目显式 True  → 开启（全局关着也能给试点项目开）
        3. 项目未设置(None) → 跟随全局参数 report.daily.enabled

        早期只有全局开关，做不到「默认关、试点项目打开」。
        """
        m = major
        if m is None:
            m = getattr(sub, "major", None)
        if m is None and getattr(sub, "major_id", None) is not None:
            m = self.db.get(ProjMajor, sub.major_id)
        flag = getattr(m, "daily_report_enabled", None) if m is not None else None
        if flag is not None:
            return bool(flag)
        return self.global_daily_enabled()

    def report_types_for(self, sub: ProjSub, major: ProjMajor | None = None) -> list[str]:
        """该子项目应填的报表类型列表（日报按项目开关决定是否包含）"""
        types = ["WEEKLY", "MONTHLY"]
        if self.daily_enabled_for(sub, major):
            types.insert(0, "DAILY")
        return types

    def is_due(self, rtype: str, sub: ProjSub, period_start: date, day: date) -> bool:
        """该期是否应该提交（文档 4.3 应填判定）

        【P1-5】日报是否应填改为「按项目」判定：该项目显式关闭时不要求，
        显式开启时要求，未设置时跟随全局参数。早期只看全局开关，
        试点项目单独开日报后待填清单/及时率都不认。
        """
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

        # 日报：项目未启用日报 → 不要求；免填日 → 不要求
        if rtype == "DAILY":
            if not self.daily_enabled_for(sub):
                return False
            return not self.cal.is_free_day(period_start)

        # 周报/月报：当期工作日不少于 1 天（免填日不单独豁免，
        # 与 submit() 口径一致：只要当期有工作日就应交）
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
        on_behalf_of: int | None = None,
    ) -> Report:
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")
        # 数据范围：只能给有权访问的子项目提交报表（防水平越权）
        assert_sub_access(operator, sub)
        if sub.status == "CLOSED":
            raise BusinessError("已结项的子项目不能再提交报表")
        # 已中止的子项目不能填报（需求：中止即停报）。早期只拦 CLOSED，
        # ABORTED 子项目仍能提交，产生一批永远不会被统计的报表。
        if sub.status == "ABORTED":
            raise BusinessError("已中止的子项目不能提交报表，请先重启子项目")

        if rtype not in REPORT_TYPES:
            raise BusinessError(f"未知报表类型：{rtype}")

        # 报表内容后端校验：早期直接收 dict，绕过前端可提交 {}，
        # 导致「已提交」的报表没有任何内容，导出/查看全是空。
        self._validate_content(rtype, content)

        # 月报必须同时更新进度（需求 3.4：月报进度更新为必填）
        if rtype == "MONTHLY" and progress is None:
            raise BusinessError("月报必须同时更新进度")

        base_day = period_start or today_local()
        # 不允许提交未来周期（周期尚未开始）
        if base_day > today_local():
            raise BusinessError("不能提交未来周期的报表")
        p_start, p_end = self.period_of(rtype, base_day)

        # 日报：免填日历里标记的日期不接收（寒暑假 / 法定节假日）。
        # 周报月报不拦 —— 只要当期有工作日就应交，由 is_due() 在待填清单里控制。
        if rtype == "DAILY" and self.cal.is_free_day(p_start):
            raise BusinessError(f"{p_start} 是免填日（见免填日历），无需提交日报")
        # 日报按项目开关：该项目未启用日报时不应接收（早期只隐藏入口不拦提交）
        if rtype == "DAILY" and not self.daily_enabled_for(sub):
            raise BusinessError("该项目未启用日报，不能提交日报")

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

        # 补填期校验（只对「本期第一次提交」生效）：
        # 1) 作废重填跳过 —— 作废是管理员动作，目的就是让人重填，不应再被
        #    补填期拦住（历史周期作废后重填的死循环就出在这里）；
        # 2) 更正（已提交）跳过 —— 更正由「更正期」单独判定，两套时限不能混用。
        # 参数也从 report.correct_days 拆成 report.backfill_days（P0-4）。
        if existing is None:
            deadline = self.deadline_of(rtype, p_start)
            backfill_days = max(0, self.config.get_int("report.backfill_days", 3))
            if datetime.now(UTC) > deadline + timedelta(days=backfill_days):
                if not self._has_grace(sub_id, rtype, p_start, kind="BACKFILL"):
                    raise BusinessError(
                        f"已超过补填期（截止 "
                        f"{deadline.astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M')} 北京时间，"
                        f"可补填 {backfill_days} 天），如需提交请联系学院负责人开启补填"
                    )

        # 代填归属：只有操作人不是子项目负责人、且显式传 on_behalf_of 才记录。
        # 及时率按 on_behalf_of or submitted_by 归属，否则学院负责人代填的
        # 报表会算到负责人头上，被代填人反而「缺交」。
        attribution_id: int | None = None
        if on_behalf_of is not None:
            if sub.owner_id is None:
                raise BusinessError("该子项目未指定负责人，无法代填")
            if on_behalf_of != sub.owner_id:
                raise BusinessError("代填对象必须是该子项目负责人")
            if operator.id != sub.owner_id:
                attribution_id = on_behalf_of

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
                on_behalf_of=attribution_id,
                status="SUBMITTED",
            )
            self.db.add(rep)
        else:
            rep = existing
            if is_revive:
                # 作废复活：清掉作废痕迹，重新记提交人与提交时间
                rep.status = "SUBMITTED"
                rep.void_reason = None
                rep.voided_by = None
                rep.voided_at = None
                rep.submitted_at = now
                rep.submitted_by = operator.id
            else:
                # 已提交：走更正逻辑（作废后重填不算更正，不校验更正时限）
                self._check_correct_permission(rep, operator)
            rep.content = content
            if progress is not None:
                rep.progress = progress
            rep.on_behalf_of = attribution_id
            if not is_revive:
                # 作废重填是重新提交，不算「更正」，不累加更正次数
                rep.correct_count += 1

        # 报表中的进度同步到子项目（文档 3.4：共用字段）
        if progress is not None and not sub.status == "CLOSED":
            from app.services.progress_service import ProgressService

            # 注意：C 组会给 update_progress 增加 event_type 参数；
            # 待其完成后此处需补传 event_type="REPORT"（避免污染 last_note）。
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

    @staticmethod
    def _validate_content(rtype: str, content: Any) -> None:
        """校验报表内容必填项（允许额外字段，保持向前兼容）

        早期 DailyContent/WeeklyContent/MonthlyContent 只在 API 层定义、
        从未被使用，submit() 直接收 dict，可提交空对象。
        """
        if not isinstance(content, dict):
            raise BusinessError("报表内容格式错误")
        for key, label in CONTENT_REQUIRED.get(rtype, ()):
            value = content.get(key)
            if not isinstance(value, str) or not value.strip():
                raise BusinessError(f"{TYPE_LABEL[rtype]}缺少必填内容：{label}")

    def _check_correct_permission(self, rep: Report, operator: Any) -> None:
        """提交后 report.correct_days 天内可自更正；超期需学院负责人开 CORRECT 放行

        早期实现用 project:sub:edit 冒充「学院负责人开权限」，而学院负责人
        恰恰没有这个权限点（且权限矩阵里该权限只给 MAJOR_OWNER/PROJECT_OWNER），
        导致超期更正对所有人都是死路；同时它与补填期共用 report.correct_days，
        提示语也串成「补填」。现在改为查 ReportGrace(kind='CORRECT')。

        Args:
            operator: 保留参数（历史调用方兼容）；更正资格由「时限 + 放行」决定，
                不再看操作人的功能权限。
        """
        days = max(0, self.config.get_int("report.correct_days", 3))
        if rep.submitted_at is None:
            return
        elapsed = (datetime.now(UTC) - rep.submitted_at).days
        if elapsed <= days:
            return
        # 超期：必须有一条未撤销、未过期、周期匹配的 CORRECT 放行
        if self._has_grace(rep.sub_id, rep.type, rep.period_start, kind="CORRECT"):
            return
        raise BusinessError(
            f"已超过 {days} 天更正期，请联系学院负责人开启更正权限"
        )

    def correctable(self, report_id: int, user: Any) -> dict:
        """该报表当前是否可更正（供前端决定是否显示「更正」按钮）

        返回 {can_correct, reason, correct_days, days_left}。
        """
        rep = self.get(report_id)
        assert_report_access(user, rep)
        days = max(0, self.config.get_int("report.correct_days", 3))
        result = {
            "can_correct": False,
            "reason": "",
            "correct_days": days,
            "days_left": 0,
        }
        if rep.status == "VOIDED":
            result["reason"] = "报表已作废，请直接重新填报"
            return result
        if rep.status != "SUBMITTED":
            result["reason"] = "只有已提交的报表才能更正"
            return result
        sub = self.db.get(ProjSub, rep.sub_id)
        if sub is not None and sub.status == "ABORTED":
            result["reason"] = "子项目已中止，不能更正"
            return result
        if sub is not None and sub.status == "CLOSED":
            result["reason"] = "子项目已结项，不能更正"
            return result
        if rep.submitted_at is None:
            result["reason"] = "报表没有提交时间，无法判断更正期"
            return result
        elapsed = (datetime.now(UTC) - rep.submitted_at).days
        if elapsed <= days:
            result["can_correct"] = True
            result["days_left"] = max(0, days - elapsed)
            return result
        if self._has_grace(rep.sub_id, rep.type, rep.period_start, kind="CORRECT"):
            result["can_correct"] = True
            result["reason"] = "已由学院负责人开启更正权限"
            return result
        result["reason"] = f"已超过 {days} 天更正期，请联系学院负责人开启更正权限"
        return result

    # ============================================================ 补填 / 更正放行
    def _has_grace(
        self,
        sub_id: int,
        rtype: str,
        period_start: date,
        *,
        kind: str = "BACKFILL",
    ) -> bool:
        """该周期是否已被放行（未撤销且未过期）

        kind=BACKFILL 用于逾期补填，kind=CORRECT 用于超期更正。
        早期只有一种放行，更正期超期后无论如何都过不了。
        """
        today = today_local()
        stmt = select(ReportGrace).where(
            ReportGrace.sub_id == sub_id,
            ReportGrace.type == rtype,
            ReportGrace.kind == kind,
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
        kind: str = "BACKFILL",
    ) -> dict:
        """开启逾期补填 / 超期更正（文档 4.3、3.4）

        Args:
            period_start: 为空表示放行该子项目该类型的所有历史周期
            days: 放行有效天数
            kind: BACKFILL=补填放行（默认），CORRECT=更正放行
        """
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")
        assert_sub_access(operator, sub)

        if rtype not in REPORT_TYPES:
            raise BusinessError(f"未知报表类型：{rtype}")
        kind = (kind or "BACKFILL").upper()
        if kind not in GRACE_KINDS:
            raise BusinessError(f"未知放行类型：{kind}（仅支持 BACKFILL / CORRECT）")
        if not reason or not reason.strip():
            raise BusinessError(f"开启{GRACE_LABEL[kind]}必须填写理由")
        days = max(1, min(90, days))

        # 同一范围已有同 kind 的有效放行则不重复创建（两种放行互不干扰）
        today = today_local()
        exist = self.db.scalar(
            select(ReportGrace).where(
                ReportGrace.sub_id == sub_id,
                ReportGrace.type == rtype,
                ReportGrace.kind == kind,
                ReportGrace.period_start.is_(None)
                if period_start is None
                else ReportGrace.period_start == period_start,
                ReportGrace.revoked_at.is_(None),
                ReportGrace.expires_on >= today,
            )
        )
        if exist is not None:
            raise BusinessError(f"该周期已有生效中的{GRACE_LABEL[kind]}放行")

        row = ReportGrace(
            sub_id=sub_id,
            type=rtype,
            kind=kind,
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
            "kind": kind,
            "period_start": period_start,
            "expires_on": row.expires_on,
            "days": days,
        }

    def revoke_grace(self, grace_id: int, operator: Any) -> None:
        """撤销补填 / 更正放行"""
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

    def list_graces(
        self, user: Any, sub_id: int | None = None, kind: str | None = None
    ) -> list[dict]:
        """补填 / 更正放行记录（生效中的排前面）

        Args:
            kind: BACKFILL / CORRECT 过滤；为空返回两类
        """
        from app.models.system import SysUser

        stmt = select(ReportGrace).order_by(ReportGrace.id.desc())
        if sub_id:
            stmt = stmt.where(ReportGrace.sub_id == sub_id)
        if kind:
            kind = kind.upper()
            if kind not in GRACE_KINDS:
                raise BusinessError(f"未知放行类型：{kind}")
            stmt = stmt.where(ReportGrace.kind == kind)

        # 数据范围：只列自己有权访问的子项目
        subs_stmt = select(ProjSub.id).where(ProjSub.deleted_at.is_(None))
        subs_stmt = apply_scope(subs_stmt, user, ProjSub)
        visible = set(self.db.scalars(subs_stmt))
        sub_names = {
            s.id: s.name
            for s in self.db.scalars(select(ProjSub).where(ProjSub.deleted_at.is_(None)))
        }
        user_names = {u.id: u.name for u in self.db.scalars(select(SysUser))}

        today = today_local()
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
                    # kind：前端据此区分「补填放行 / 更正放行」
                    "kind": r.kind,
                    "kind_label": GRACE_LABEL.get(r.kind, r.kind),
                    "period_start": r.period_start,
                    "reason": r.reason,
                    "granted_by": r.granted_by,
                    "granted_by_name": user_names.get(r.granted_by),
                    "granted_at": r.granted_at,
                    "expires_on": r.expires_on,
                    "revoked_at": r.revoked_at,
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
        # 数据范围：持 report:void 的项目总负责人不能按 id 跨学院作废任意报表
        assert_report_access(operator, rep)

        rep.status = "VOIDED"
        rep.void_reason = reason
        rep.voided_by = operator.id
        rep.voided_at = datetime.now(UTC)

        # 作废后自动放行重填（双保险）：
        # submit() 对 VOIDED 报表已经跳过补填期校验，但这条放行还有两个作用：
        # 1) 万一将来复活逻辑调整，作废记录本身仍留有「可重填」的凭据；
        # 2) 前端「放行记录」列表能看到这张报表为什么可以被重填。
        # 只在该期已超补填期时开，避免正常周期里堆一堆无意义的放行。
        self._auto_backfill_grace_after_void(rep, operator, reason)

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

    def _auto_backfill_grace_after_void(
        self, rep: Report, operator: Any, void_reason: str
    ) -> None:
        """作废后若该期已超补填期，自动开一条覆盖该周期的 BACKFILL 放行（30 天）

        背景（P0-2）：作废历史报表后重填被补填期校验拦住，形成
        「作废 → 缺报 → 告警 → 补不了」死循环。作废本身是管理员动作，
        目的就是重填，因此这里自动放行，不要求管理员再手工开一次。
        """
        deadline = self.deadline_of(rep.type, rep.period_start)
        backfill_days = max(0, self.config.get_int("report.backfill_days", 3))
        if datetime.now(UTC) <= deadline + timedelta(days=backfill_days):
            return  # 仍在补填期内，无需放行
        if self._has_grace(rep.sub_id, rep.type, rep.period_start, kind="BACKFILL"):
            return  # 已有生效放行，不重复创建
        row = ReportGrace(
            sub_id=rep.sub_id,
            type=rep.type,
            kind="BACKFILL",
            period_start=rep.period_start,
            reason=f"作废后自动放行重填（作废理由：{void_reason[:100]}）",
            granted_by=operator.id,
            expires_on=today_local() + timedelta(days=30),
        )
        self.db.add(row)
        logger.info(
            "报表 %s 作废，该期已超补填期，自动开启 BACKFILL 放行（至 %s）",
            rep.id,
            row.expires_on,
        )

    # ============================================================ 周报初稿
    def generate_weekly_draft(self, sub_id: int, week_start: date | None = None) -> dict:
        """周报初稿：由本周期进度记录 / 日报 + 上期周报未完成计划拼成（文档 3.4）

        【P1-7】原来只拼 ProgRecord，而 docstring 写「日报开启时用日报拼」却没实现。
        现在分两条路：
        - 日报启用（全局或该项目）：取本周所有 DAILY 且 SUBMITTED 的报表，
          按日期拼 content.today / tomorrow / issues，信息密度远高于进度数字；
        - 日报关闭：保留进度记录拼法，但**只拼 event_type='FILL'** 的记录，
          否则催办文案（URGE）和报表同步（REPORT）都会混进初稿。
        """
        sub = self.db.get(ProjSub, sub_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFoundError("子项目不存在")

        # 「本周」按北京时区取，避免服务器 UTC 时区下周界偏移
        ws = week_start or self.week_start(today_local())
        we = ws + timedelta(days=6)
        day_lo = datetime.combine(ws, time(0, 0), tzinfo=SHANGHAI).astimezone(UTC)
        day_hi = datetime.combine(we + timedelta(days=1), time(0, 0), tzinfo=SHANGHAI).astimezone(UTC)

        daily_on = self.daily_enabled_for(sub)
        daily_reports: list[Report] = []
        if daily_on:
            daily_reports = list(
                self.db.scalars(
                    select(Report)
                    .where(
                        Report.sub_id == sub_id,
                        Report.type == "DAILY",
                        Report.status == "SUBMITTED",
                        Report.period_start >= ws,
                        Report.period_start <= we,
                    )
                    .order_by(Report.period_start)
                )
            )

        records = list(
            self.db.scalars(
                select(ProgRecord)
                .where(
                    ProgRecord.sub_id == sub_id,
                    ProgRecord.created_at >= day_lo,
                    ProgRecord.created_at < day_hi,
                )
                .order_by(ProgRecord.created_at)
            )
        )

        if daily_on and daily_reports:
            # 用日报拼：日期 + 今天做了什么 + 明天计划 + 问题
            lines: list[str] = []
            issues: list[str] = []
            for r in daily_reports:
                c = r.content if isinstance(r.content, dict) else {}
                today_txt = str(c.get("today") or "").strip()
                tomorrow_txt = str(c.get("tomorrow") or "").strip()
                issue_txt = str(c.get("issues") or "").strip()
                line = f"{r.period_start.strftime('%m-%d')}：{today_txt}"
                if tomorrow_txt:
                    line += f"；次日计划：{tomorrow_txt}"
                if line.strip():
                    lines.append(line)
                if issue_txt:
                    issues.append(f"{r.period_start.strftime('%m-%d')}：{issue_txt}")
            summary = "\n".join(lines) if lines else "本周期无日报记录"
            if issues:
                summary += "\n\n问题与风险：\n" + "\n".join(issues)
        else:
            # 用进度记录拼：只取 FILL，排除 URGE（催办）与 REPORT（报表同步）
            fills = [r for r in records if getattr(r, "event_type", "FILL") == "FILL"]
            changes = [
                f"{r.created_at.astimezone(SHANGHAI).strftime('%m-%d')}：{r.progress}%"
                + (f"（{r.note}）" if r.note else "")
                for r in fills
            ]
            summary = "\n".join(changes) if changes else "本周期无进度变化记录"

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

        # 进度变化幅度（只按 FILL 计算，避免催办/同步记录制造虚假波动）
        fills = [r for r in records if getattr(r, "event_type", "FILL") == "FILL"]
        if fills:
            start_p = fills[0].progress
            end_p = fills[-1].progress
            delta = end_p - start_p
        else:
            start_p = end_p = sub.current_progress
            delta = 0

        return {
            "summary": summary,
            "next_week": last_plan or "",
            "progress": sub.current_progress,
            "progress_delta": delta,
            # 来源标注：前端可提示「初稿来自日报 / 进度记录」
            "source": "DAILY" if (daily_on and daily_reports) else "PROGRESS",
            "daily_count": len(daily_reports),
        }

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
        day = day or today_local()
        stmt = select(ProjSub).where(
            ProjSub.deleted_at.is_(None), ProjSub.status == "IN_PROGRESS"
        )
        stmt = apply_scope(stmt, user, ProjSub)
        subs = list(self.db.scalars(stmt))

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
            # 日报是否启用改为按项目判定（需求 6.1 第 5 项：试点项目单独开）
            types = self.report_types_for(sub)
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
        owner_id: int | None = None,
    ) -> dict:
        """报表及时率 = 按期提交数 ÷ 应提交数（文档 4.3）

        分子：按期提交，不含逾期补填
        分母：应提交，不含未启动/已结项、免填日期、已作废

        【P1-9】作废期次既不计入分母也不计入分子（早期算成「缺交」，
        与需求「作废后不再算进及时率」相反），单独用 voided 计数返回。

        【P1-8】归属人按 on_behalf_of or submitted_by 计算，支持 owner_id
        筛选（「这个人的及时率」）。
        """
        subs_stmt = select(ProjSub).where(ProjSub.deleted_at.is_(None))
        subs_stmt = apply_scope(subs_stmt, user, ProjSub)
        if major_id:
            subs_stmt = subs_stmt.where(ProjSub.major_id == major_id)
        subs = list(self.db.scalars(subs_stmt))

        # 批量预取大项目（用于按项目判定日报开关，避免 N+1）
        major_ids = {s.major_id for s in subs if s.major_id is not None}
        majors: dict[int, ProjMajor] = {}
        if major_ids:
            majors = {
                m.id: m
                for m in self.db.scalars(
                    select(ProjMajor).where(ProjMajor.id.in_(major_ids))
                )
            }

        # 报表类型：显式指定则只算该类；否则按「全局或任一项目」启用日报决定
        if rtype:
            types = [rtype]
        else:
            any_daily = self.global_daily_enabled() or any(
                self.daily_enabled_for(s, majors.get(s.major_id)) for s in subs
            )
            types = ["DAILY", "WEEKLY", "MONTHLY"] if any_daily else ["WEEKLY", "MONTHLY"]

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
        voided = 0
        detail: list[dict] = []
        now = datetime.now(UTC)

        for s in subs:
            sub_types = types
            if not rtype:
                # 未显式指定类型时，按该子项目自己的日报开关过滤
                sub_types = self.report_types_for(s, majors.get(s.major_id))
            for rt in sub_types:
                # 逐期枚举
                p = self.period_of(rt, start)[0]
                guard = 0
                while p <= end and guard < 5000:
                    guard += 1
                    p_start, p_end = self.period_of(rt, p)
                    rep = reports.get((s.id, rt, p_start))
                    # 只统计「截止时间已过」的期次，未到期的不计入分母
                    deadline = self.deadline_of(rt, p_start)
                    deadline_passed = deadline <= now
                    due = deadline_passed and self.is_due(rt, s, p_start, end)
                    # 【P1-9】作废期次：既不计入分母也不计入分子（跳过），
                    # 只单独统计 voided。早期把作废算成「缺交」，与需求
                    # 「作废后不再算进及时率」相反。
                    if due and rep is not None and rep.status == "VOIDED":
                        voided += 1
                        p = self.period_of(rt, p_end + timedelta(days=1))[0]
                        continue
                    if due:
                        # 归属人：代填算被代填人（on_behalf_of or submitted_by）
                        attributed = (rep.on_behalf_of or rep.submitted_by) if rep else None
                        if owner_id is not None and attributed != owner_id:
                            # 按人筛选：不匹配的期次不计入统计（分母也不计）
                            p = self.period_of(rt, p_end + timedelta(days=1))[0]
                            continue
                        total_due += 1
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
                            "owner_id": attributed,
                            "submitted_by": rep.submitted_by if rep else None,
                            "on_behalf_of": rep.on_behalf_of if rep else None,
                        }
                        if rep is None:
                            missing += 1
                            row["state"] = "MISSING"
                            row["state_label"] = "缺交"
                            row["submitted_at"] = None
                            row["overdue_days"] = (now - deadline).days
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
            # 【P1-9】作废期次数（不计入分母/分子）
            "voided": voided,
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
        owner_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """及时率明细（分页 + 按状态筛选），供前端逐条查看与导出"""
        full = self.timeliness(
            user, start=start, end=end, rtype=rtype, major_id=major_id, owner_id=owner_id
        )
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
