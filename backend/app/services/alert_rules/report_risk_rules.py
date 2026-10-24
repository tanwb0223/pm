"""报表类与风险类预警规则（AL-06 / AL-07 / AL-08）

## 本次修复（P1-9 / P1-16）

- **AL-06 只盯上一周**（P1-16）：早期只检查「上一个自然周」一份周报，
  再往前的漏报永远检测不到；日报/月报也没有覆盖。现在改为遍历
  「计划开始以来所有已过补填期、且未提交」的周期（周报 + 月报，
  日报按项目开关），并且只要对应周期出现 SUBMITTED 就不再命中，
  这样 `_auto_close` 才能在补齐后自动关闭。
- **AL-08 去重键缺 risk_id**（P1-9）：早期所有无子项目的风险
  dedup_key 都是 `-:AL-08`，多条风险被合并成一条，处置人不知道是哪条。
  现在 candidate 带 `dedup_suffix=risk:{id}`，一条风险一条预警。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.alert import Risk
from app.models.project import ProjSub
from app.models.report import Report
from app.services.alert_rules.base import AlertCandidate, AlertRule, RuleContext

# 补填期默认天数（与 report.backfill_days 默认值一致）。
# 逾期判定要等补填期结束：刚过截止日就报 AL-06 会与「3 天内可补填」冲突。
DEFAULT_BACKFILL_DAYS = 3

# 回溯上限：最多往前看多少个周期，避免老项目一次生成上千条候选
MAX_PERIODS_BACK = 60

# 业务时区：截止时间是「北京时间某时刻」，换算成 UTC 后 date() 会跨天，
# 判定「补填期是否结束」必须按业务时区的自然日。
try:
    from zoneinfo import ZoneInfo

    _SHANGHAI = ZoneInfo("Asia/Shanghai")
except Exception:  # 极端环境缺 tzdata 时退化为固定偏移
    _SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def _local_date(dt: datetime) -> date:
    """带时区的时间 → 业务时区（北京）的自然日"""
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(_SHANGHAI).date()


class AL06ReportOverdue(AlertRule):
    """报表逾期：补填期结束后仍未提交（黄色，补填完成自动关闭）

    P1-16：遍历「计划开始以来所有已过补填期且未提交的周期」，
    周报与月报都要覆盖（月报是结项必需材料，漏报更严重）。
    """

    code = "AL-06"
    level = "YELLOW"
    name = "报表逾期"
    trigger_mode = "hourly"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        from app.services.report_service import ReportService

        out: list[AlertCandidate] = []
        svc = ReportService(db)
        backfill_days = ctx.get_int("report.backfill_days", DEFAULT_BACKFILL_DAYS)

        # 报表类型：周报、月报必查；日报仅在全局或项目开关打开时查
        daily_enabled = ctx.get_bool("report.daily.enabled", False)
        types = ["WEEKLY", "MONTHLY"]
        if daily_enabled:
            types.insert(0, "DAILY")

        subs = list(
            db.scalars(
                select(ProjSub).where(
                    ProjSub.deleted_at.is_(None),
                    ProjSub.status == "IN_PROGRESS",
                    ProjSub.plan_start.is_not(None),
                    ProjSub.plan_start <= ctx.day,
                )
            )
        )

        # 已提交的（子项目, 类型, 周期）集合，一次性查出避免 N+1
        submitted: set[tuple[int, str, date]] = {
            (r.sub_id, r.type, r.period_start)
            for r in db.scalars(
                select(Report).where(
                    Report.status == "SUBMITTED",
                    Report.period_start <= ctx.day,
                )
            )
        }

        for sub in subs:
            for rtype in types:
                if rtype == "DAILY" and not self._daily_enabled_for(sub, ctx):
                    continue
                # 枚举从计划开始到当前周期的所有周期（复用 ReportService 的周期计算）
                periods = svc.periods_between(rtype, sub.plan_start, ctx.day)
                if len(periods) > MAX_PERIODS_BACK:
                    periods = periods[-MAX_PERIODS_BACK:]
                for p_start, p_end in periods:
                    # 未到补填期结束：还没到「逾期」标准，不报
                    deadline = svc.deadline_of(rtype, p_start)
                    backfill_end = deadline + timedelta(days=backfill_days)
                    if ctx.day < backfill_end.date():
                        continue
                    # 该期不要求填报（免填日/结项后/未启动等）
                    if not svc.is_due(rtype, sub, p_start, ctx.day):
                        continue
                    if (sub.id, rtype, p_start) in submitted:
                        continue
                    out.append(
                        AlertCandidate(
                            alert_type=self.code,
                            level=self.level,
                            sub_id=sub.id,
                            major_id=sub.major_id,
                            college_id=sub.college_id,
                            detail={
                                "报表类型": {"DAILY": "日报", "WEEKLY": "周报", "MONTHLY": "月报"}[rtype],
                                "期间": p_start.isoformat(),
                                "补填截止": backfill_end.date().isoformat(),
                            },
                            owner_id=sub.owner_id,
                            # 同一子项目同一类型只保留一条预警（合并计数），
                            # 否则历史漏报会刷出几十条同源预警
                            dedup_suffix=rtype,
                        )
                    )
        return out

    @staticmethod
    def _daily_enabled_for(sub: ProjSub, ctx: RuleContext) -> bool:
        """日报是否对该子项目启用（全局开关 or 项目显式开关，项目 False 优先）"""
        major = getattr(sub, "major", None)
        if major is not None and major.daily_report_enabled is not None:
            return bool(major.daily_report_enabled)
        return ctx.get_bool("report.daily.enabled", False)


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
                    detail={
                        "超期天数": overdue_days,
                        "风险": risk.description[:50],
                        "risk_id": risk.id,
                    },
                    owner_id=risk.owner_id or (sub.owner_id if sub else None),
                    # P1-9：dedup_key 加 risk_id，避免多条风险合并成一条
                    dedup_suffix=f"risk:{risk.id}",
                )
            )
        return out
