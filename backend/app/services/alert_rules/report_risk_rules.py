"""报表类与风险类预警规则（AL-06 / AL-07 / AL-08）"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.alert import Risk
from app.models.project import ProjSub
from app.services.alert_rules.base import AlertCandidate, AlertRule, RuleContext


class AL06ReportOverdue(AlertRule):
    """报表逾期：补填期结束后仍未提交（黄色，补填完成自动关闭）"""

    code = "AL-06"
    level = "YELLOW"
    name = "报表逾期"
    trigger_mode = "hourly"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        out = []
        # 周报：本周一 12:00 截止，补填期 3 天
        # 月报：次月第 3 个工作日 18:00 截止
        # 日报：默认关闭，此处只处理已启用的项目
        daily_enabled = ctx.get_bool("report.daily.enabled", False)

        if daily_enabled:
            # 日报：当日 24:00 截止；第 4 个自然日仍未交 → 逾期
            target_date = ctx.day - timedelta(days=4)
            subs = self._active_subs(db, target_date)
            for sub in subs:
                exists = db.execute(
                    text(
                        """
                        SELECT 1 FROM report
                        WHERE sub_id = :sid AND type = 'DAILY'
                          AND period_start = :d AND status = 'SUBMITTED'
                        """
                    ),
                    {"sid": sub.id, "d": target_date},
                ).first()
                if not exists:
                    out.append(self._candidate(sub, "日报", target_date))

        # 周报：上周一 12:00 截止 → 本周一之后仍未交
        # 简化：检查上一个自然周
        last_week_start = self._week_start(ctx.day) - timedelta(days=7)
        for sub in self._active_subs(db, last_week_start):
            exists = db.execute(
                text(
                    """
                    SELECT 1 FROM report
                    WHERE sub_id = :sid AND type = 'WEEKLY'
                      AND period_start = :d AND status = 'SUBMITTED'
                    """
                ),
                {"sid": sub.id, "d": last_week_start},
            ).first()
            if not exists:
                out.append(self._candidate(sub, "周报", last_week_start))

        return out

    @staticmethod
    def _week_start(d: date) -> date:
        """本周一"""
        return d - timedelta(days=d.weekday())

    @staticmethod
    def _active_subs(db: Session, at: date):
        """在该日期处于进行中的子项目"""
        return list(
            db.scalars(
                select(ProjSub).where(
                    ProjSub.deleted_at.is_(None),
                    ProjSub.status == "IN_PROGRESS",
                    ProjSub.plan_start.is_not(None),
                    ProjSub.plan_start <= at,
                )
            )
        )

    def _candidate(self, sub: ProjSub, report_type: str, period: date) -> AlertCandidate:
        return AlertCandidate(
            alert_type=self.code,
            level=self.level,
            sub_id=sub.id,
            major_id=sub.major_id,
            college_id=sub.college_id,
            detail={"报表类型": report_type, "期间": period.isoformat()},
            owner_id=sub.owner_id,
        )


class AL07ConsecutiveMissing(AlertRule):
    """连续未填：连续 3 个应填周期未提交报表"""

    code = "AL-07"
    level = "ORANGE"
    name = "连续未填"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        periods = ctx.get_int("alert.AL-07.periods", 3)
        out = []

        # 检查最近 periods 周，每周是否都有已提交的周报
        week_starts = [self._week_start(ctx.day) - timedelta(days=7 * i) for i in range(1, periods + 1)]

        subs = list(
            db.scalars(
                select(ProjSub).where(
                    ProjSub.deleted_at.is_(None),
                    ProjSub.status == "IN_PROGRESS",
                    ProjSub.plan_start.is_not(None),
                )
            )
        )
        for sub in subs:
            # 只统计该子项目已开始的周期
            valid_weeks = [w for w in week_starts if sub.plan_start and sub.plan_start <= w]
            if len(valid_weeks) < periods:
                continue
            missing = 0
            for w in valid_weeks:
                exists = db.execute(
                    text(
                        """
                        SELECT 1 FROM report
                        WHERE sub_id = :sid AND type = 'WEEKLY'
                          AND period_start = :d AND status = 'SUBMITTED'
                        """
                    ),
                    {"sid": sub.id, "d": w},
                ).first()
                if not exists:
                    missing += 1
            if missing >= periods:
                out.append(
                    AlertCandidate(
                        alert_type=self.code,
                        level=self.level,
                        sub_id=sub.id,
                        major_id=sub.major_id,
                        college_id=sub.college_id,
                        detail={"连续未填周期": missing},
                        owner_id=sub.owner_id,
                    )
                )
        return out

    @staticmethod
    def _week_start(d: date) -> date:
        return d - timedelta(days=d.weekday())


class AL08RiskOverdue(AlertRule):
    """风险超期：超过计划闭环时间未闭环"""

    code = "AL-08"
    level = "ORANGE"
    name = "风险超期"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        out = []
        rows = db.scalars(
            select(Risk).where(
                Risk.status == "OPEN",
                Risk.plan_close_date < ctx.day,
            )
        )
        for risk in rows:
            sub = db.get(ProjSub, risk.sub_id) if risk.sub_id else None
            overdue_days = (ctx.day - risk.plan_close_date).days
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=risk.sub_id,
                    major_id=risk.major_id,
                    college_id=sub.college_id if sub else None,
                    detail={"超期天数": overdue_days, "风险": risk.description[:50]},
                    owner_id=risk.owner_id or (sub.owner_id if sub else None),
                )
            )
        return out
