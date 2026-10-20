"""11 条预警规则

| 编号  | 类型     | 触发条件                                    | 等级  |
|-------|----------|---------------------------------------------|-------|
| AL-01 | 进度滞后 | 偏差 < -10% 且持续 3 个有效日               | 橙    |
| AL-02 | 严重滞后 | 偏差 <= -25%（建议加 1 天防抖）             | 红    |
| AL-03 | 进度停滞 | 超过 21 个有效日未更新                      | 橙    |
| AL-04 | 进度回调 | 本次进度低于上次（事件驱动）                | 黄    |
| AL-05 | 项目临期 | 距结束 < 30 天且进度 < 80%                  | 红    |
| AL-06 | 报表逾期 | 补填期结束后仍未提交                        | 黄    |
| AL-07 | 连续未填 | 连续 3 个应填周期未提交报表                 | 橙    |
| AL-08 | 风险超期 | 风险超过计划闭环时间未闭环                  | 橙    |
| AL-09 | 责任空缺 | 学院未指定负责人 或 子项目无负责人          | 红    |
| AL-10 | 计划缺失 | 进行中的子项目未设置计划起止时间            | 橙    |
| AL-11 | 长期零进度 | 连续 60 个有效日进度为 0                 | 橙    |
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.alert import Risk
from app.models.project import ProjMajorCollege, ProjSub
from app.services.alert_rules.base import AlertCandidate, AlertRule, RuleContext


# ================================================================ 进度类
class AL01ProgressLag(AlertRule):
    """进度滞后：偏差 < -10% 且持续 3 个有效日"""

    code = "AL-01"
    level = "ORANGE"
    name = "进度滞后"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        threshold = ctx.get_int("alert.AL-01.deviation", -10)
        days = ctx.get_int("alert.AL-01.days", 3)

        # 用快照表：连续 days 个有效日偏差 < threshold
        rows = db.execute(
            text(
                """
                SELECT sub_id, COUNT(*) AS cnt
                FROM prog_snapshot
                WHERE snap_date <= :day
                  AND snap_date > :since
                  AND deviation < :threshold
                  AND is_free_day = false
                GROUP BY sub_id
                HAVING COUNT(*) >= :days
                """
            ),
            {"day": ctx.day, "since": ctx.day - timedelta(days=30), "threshold": threshold, "days": days},
        ).all()

        out = []
        for sub_id, cnt in rows:
            sub = db.get(ProjSub, sub_id)
            if sub is None or sub.deleted_at is not None or sub.status not in ("IN_PROGRESS", "NOT_STARTED"):
                continue
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=sub.major_id,
                    college_id=sub.college_id,
                    detail={"连续滞后天数": cnt, "阈值": threshold},
                    owner_id=sub.owner_id,
                )
            )
        return out


class AL02SevereLag(AlertRule):
    """严重滞后：偏差 <= -25%（加 1 天防抖）"""

    code = "AL-02"
    level = "RED"
    name = "严重滞后"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        threshold = ctx.get_int("alert.AL-02.deviation", -25)

        rows = db.execute(
            text(
                """
                SELECT sub_id, MIN(deviation) AS dev, COUNT(*) AS cnt
                FROM prog_snapshot
                WHERE snap_date <= :day
                  AND snap_date > :since
                  AND deviation <= :threshold
                  AND is_free_day = false
                GROUP BY sub_id
                HAVING COUNT(*) >= 1
                """
            ),
            {"day": ctx.day, "since": ctx.day - timedelta(days=7), "threshold": threshold},
        ).all()

        out = []
        for sub_id, dev, cnt in rows:
            sub = db.get(ProjSub, sub_id)
            if sub is None or sub.deleted_at is not None or sub.status not in ("IN_PROGRESS", "NOT_STARTED"):
                continue
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=sub.major_id,
                    college_id=sub.college_id,
                    detail={"偏差": dev, "阈值": threshold},
                    owner_id=sub.owner_id,
                )
            )
        return out


class AL03Stagnant(AlertRule):
    """进度停滞：超过 21 个有效日未更新"""

    code = "AL-03"
    level = "ORANGE"
    name = "进度停滞"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        days = ctx.get_int("alert.AL-03.days", 21)
        rows = db.execute(
            text(
                """
                SELECT sub_id, days_since_update
                FROM prog_snapshot
                WHERE snap_date = :day AND days_since_update > :days AND is_free_day = false
                """
            ),
            {"day": ctx.day, "since": ctx.day - timedelta(days=120), "days": days},
        ).all()

        out = []
        for sub_id, dsu in rows:
            sub = db.get(ProjSub, sub_id)
            if sub is None or sub.deleted_at is not None or sub.status != "IN_PROGRESS":
                continue
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=sub.major_id,
                    college_id=sub.college_id,
                    detail={"未更新有效日": dsu, "阈值": days},
                    owner_id=sub.owner_id,
                )
            )
        return out


class AL04Rollback(AlertRule):
    """进度回调（事件驱动，填报时同步判定）"""

    code = "AL-04"
    level = "YELLOW"
    name = "进度回调"
    trigger_mode = "event"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        # 事件驱动规则不在扫描中执行
        return []

    @staticmethod
    def on_progress_rollback(sub: ProjSub, reason: str | None) -> AlertCandidate:
        return AlertCandidate(
            alert_type="AL-04",
            level="YELLOW",
            sub_id=sub.id,
            major_id=sub.major_id,
            college_id=sub.college_id,
            detail={"原因": reason},
            owner_id=sub.owner_id,
        )


class AL05NearDeadline(AlertRule):
    """项目临期：距计划结束不足 30 天且进度低于 80%"""

    code = "AL-05"
    level = "RED"
    name = "项目临期"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        days = ctx.get_int("alert.AL-05.days", 30)
        threshold = ctx.get_int("alert.AL-05.progress", 80)

        subs = db.scalars(
            select(ProjSub).where(
                ProjSub.deleted_at.is_(None),
                ProjSub.status.in_(("IN_PROGRESS", "NOT_STARTED")),
                ProjSub.plan_end.is_not(None),
                ProjSub.current_progress < threshold,
            )
        )
        out = []
        for sub in subs:
            remaining = (sub.plan_end - ctx.day).days
            if 0 <= remaining < days:
                out.append(
                    AlertCandidate(
                        alert_type=self.code,
                        level=self.level,
                        sub_id=sub.id,
                        major_id=sub.major_id,
                        college_id=sub.college_id,
                        detail={"剩余天数": remaining, "进度": sub.current_progress},
                        owner_id=sub.owner_id,
                    )
                )
        return out


class AL09NoOwner(AlertRule):
    """责任空缺：学院未指定负责人 或 子项目无负责人（不可关闭）"""

    code = "AL-09"
    level = "RED"
    name = "责任空缺"
    trigger_mode = "daily"
    immutable = True

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        out = []

        # 1) 子项目无负责人
        for sub in db.scalars(
            select(ProjSub).where(
                ProjSub.deleted_at.is_(None),
                ProjSub.owner_id.is_(None),
                ProjSub.status.in_(("IN_PROGRESS", "NOT_STARTED")),
            )
        ):
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=sub.major_id,
                    college_id=sub.college_id,
                    detail={"问题": "子项目未指定负责人"},
                    owner_id=None,
                )
            )

        # 2) 学院未指定负责人
        rows = db.execute(
            select(ProjMajorCollege, ProjSub)
            .join(
                ProjSub,
                (ProjSub.major_id == ProjMajorCollege.major_id)
                & (ProjSub.college_id == ProjMajorCollege.college_id),
            )
            .where(
                ProjMajorCollege.college_owner_id.is_(None),
                ProjSub.deleted_at.is_(None),
            )
            .distinct(ProjMajorCollege.major_id, ProjMajorCollege.college_id)
        ).all()
        for mc, sub in rows:
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=mc.major_id,
                    college_id=mc.college_id,
                    detail={"问题": "学院未指定负责人"},
                    owner_id=None,
                )
            )
        return out


class AL10NoPlan(AlertRule):
    """计划缺失：进行中的子项目未设置计划起止时间（不可关闭）"""

    code = "AL-10"
    level = "ORANGE"
    name = "计划缺失"
    trigger_mode = "daily"
    immutable = True

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        out = []
        for sub in db.scalars(
            select(ProjSub).where(
                ProjSub.deleted_at.is_(None),
                ProjSub.status == "IN_PROGRESS",
                (ProjSub.plan_start.is_(None)) | (ProjSub.plan_end.is_(None)),
            )
        ):
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=sub.major_id,
                    college_id=sub.college_id,
                    detail={"问题": "未设置计划起止时间"},
                    owner_id=sub.owner_id,
                )
            )
        return out


class AL11ZeroProgress(AlertRule):
    """长期零进度：连续 60 个有效日进度为 0"""

    code = "AL-11"
    level = "ORANGE"
    name = "长期零进度"
    trigger_mode = "daily"

    def evaluate(self, db: Session, ctx: RuleContext) -> list[AlertCandidate]:
        days = ctx.get_int("alert.AL-11.days", 60)
        rows = db.execute(
            text(
                """
                SELECT sub_id, COUNT(*) AS cnt
                FROM prog_snapshot
                WHERE snap_date <= :day
                  AND snap_date > :since
                  AND actual_progress = 0
                  AND is_free_day = false
                GROUP BY sub_id
                HAVING COUNT(*) >= :days
                """
            ),
            {"day": ctx.day, "since": ctx.day - timedelta(days=120), "days": days},
        ).all()

        out = []
        for sub_id, cnt in rows:
            sub = db.get(ProjSub, sub_id)
            if sub is None or sub.deleted_at is not None or sub.status != "IN_PROGRESS":
                continue
            out.append(
                AlertCandidate(
                    alert_type=self.code,
                    level=self.level,
                    sub_id=sub.id,
                    major_id=sub.major_id,
                    college_id=sub.college_id,
                    detail={"零进度天数": cnt, "阈值": days},
                    owner_id=sub.owner_id,
                )
            )
        return out
