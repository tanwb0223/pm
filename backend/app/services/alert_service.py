"""预警服务：扫描、去重、状态机、升级、通知"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, NotFoundError
from app.core.scope import apply_scope
from app.models.alert import Alert, AlertAction
from app.models.project import ProgSnapshot, ProjMajor, ProjMajorCollege, ProjSub
from app.models.system import NotifyLog, SysUser
from app.services.alert_rules import (
    IMMUTABLE_TYPES,
    LEVEL_ORDER,
    AlertCandidate,
    RuleContext,
    build_rules,
    sort_candidates,
)
from app.services.calendar_service import CalendarService
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

# 等级 → 处置时限（天）
DEADLINE_DAYS = {"RED": 3, "ORANGE": 7, "YELLOW": 0}
# 同源合并窗口（天）
DEDUP_WINDOW_DAYS = 7


class AlertService:
    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService(db)

    # ============================================================ 扫描
    def scan(self, day: date | None = None) -> dict:
        """执行全部扫描规则：生成/合并预警，并自动关闭条件已消失的预警"""
        day = day or date.today()
        cal = CalendarService.load(self.db)
        ctx = RuleContext(db=self.db, day=day, calendar=cal, config=self.config)

        all_candidates: list[AlertCandidate] = []
        rule_stats: dict[str, int] = {}
        evaluated: set[str] = set()

        for rule in build_rules():
            if rule.trigger_mode == "event":
                continue
            if not rule.enabled(ctx):
                rule_stats[rule.code] = 0
                continue
            try:
                cands = rule.evaluate(self.db, ctx)
                rule_stats[rule.code] = len(cands)
                all_candidates.extend(cands)
                evaluated.add(rule.code)
            except Exception:
                logger.exception("规则执行失败：%s", rule.code)
                rule_stats[rule.code] = -1

        all_candidates = sort_candidates(all_candidates)

        created = 0
        merged = 0
        for cand in all_candidates:
            result = self._create_or_merge(cand, day)
            if result == "created":
                created += 1
            elif result == "merged":
                merged += 1

        auto_closed = self._auto_close(all_candidates, evaluated)

        self.db.flush()
        return {
            "date": day.isoformat(),
            "rules": rule_stats,
            "candidates": len(all_candidates),
            "created": created,
            "merged": merged,
            "auto_closed": auto_closed,
        }

    def _auto_close(self, candidates: list[AlertCandidate], evaluated: set[str]) -> int:
        """自动关闭：本次扫描未再命中的预警（触发条件已消失）

        AL-09 / AL-10 被设计为「只能通过补齐数据自动关闭、不能手工关闭」，
        这里就是那条自动关闭路径。

        只处理本次**成功执行**的规则，某条规则抛异常时不动它的预警，
        避免因为一次扫描失败把大批预警误关。
        事件驱动规则（AL-04）不参与：它不在扫描候选里，天然会「未命中」。
        """
        event_codes = {r.code for r in build_rules() if r.trigger_mode == "event"}
        closeable = {code for code in evaluated if code not in event_codes}
        if not closeable:
            return 0

        triggered = {c.dedup_key for c in candidates}
        rows = list(
            self.db.scalars(
                select(Alert).where(
                    Alert.status.in_(("PENDING", "CLAIMED")),
                    Alert.alert_type.in_(closeable),
                )
            )
        )

        closed = 0
        for a in rows:
            if a.dedup_key in triggered:
                continue
            a.status = "CLOSED"
            a.closed_at = datetime.now(UTC)
            a.close_reason = "触发条件已消除，系统自动关闭"
            self._log_action(a.id, "CLOSE", None, reason="触发条件已消除，系统自动关闭")
            closed += 1
        return closed

    def handle_rollback(self, sub: ProjSub, reason: str | None) -> None:
        """事件驱动：进度回调时生成 AL-04"""
        from app.services.alert_rules.progress_rules import AL04Rollback

        cand = AL04Rollback.on_progress_rollback(sub, reason)
        self._create_or_merge(cand, date.today())
        self.db.flush()

    # ============================================================ 去重合并
    def _create_or_merge(self, cand: AlertCandidate, day: date) -> str:
        """7 天同源合并：同 dedup_key 且未关闭的预警，只加计数不重复推

        IGNORED 也参与去重：被明确忽略的预警在合并窗口内不再重新生成，
        否则次日扫描又会建一条新的，"忽略"失去意义（只做窗口内抑制，
        窗口过期后条件仍存在会重新提醒）。
        """
        since = datetime.now(UTC) - timedelta(days=DEDUP_WINDOW_DAYS)
        exist = self.db.scalar(
            select(Alert).where(
                Alert.dedup_key == cand.dedup_key,
                Alert.status.in_(("PENDING", "CLAIMED", "IGNORED")),
                Alert.created_at >= since,
            )
        )
        if exist is not None:
            exist.merged_count += 1
            return "merged"

        # 接收人兜底链
        receiver = self._resolve_receiver(cand)

        # 处置时限：优先读参数（alert.red/orange.deadline_days），未配置时用默认值。
        # 原来这里是 DEADLINE_DAYS 硬编码，导致参数配置页改这两个值不生效。
        deadline = None
        days = self._deadline_days(cand.level)
        if days:
            deadline = datetime.now(UTC) + timedelta(days=days)

        alert = Alert(
            alert_type=cand.alert_type,
            level=cand.level,
            status="PENDING",
            sub_id=cand.sub_id,
            major_id=cand.major_id,
            college_id=cand.college_id,
            trigger_at=datetime.now(UTC),
            deadline_at=deadline,
            dedup_key=cand.dedup_key,
            merged_count=1,
        )
        self.db.add(alert)
        self.db.flush()

        # 通知（黄色仅提示，不进待办）
        # 邮件通道受参数 notify.email.enabled 控制（早期该参数改了不生效）
        email_enabled = self.config.get_bool("notify.email.enabled", True)
        if cand.level in ("RED", "ORANGE"):
            for uid in receiver:
                self._notify(alert, uid, "INNER", cand)
                if cand.level == "RED" and email_enabled:
                    self._notify(alert, uid, "EMAIL", cand)
        else:
            for uid in receiver[:1]:
                self._notify(alert, uid, "INNER", cand)

        return "created"

    def _deadline_days(self, level: str) -> int:
        """某等级预警的处置时限（天）：参数优先，缺省回落到 DEADLINE_DAYS"""
        key = {"RED": "alert.red.deadline_days", "ORANGE": "alert.orange.deadline_days"}.get(level)
        if key:
            return self.config.get_int(key, DEADLINE_DAYS.get(level, 0))
        return DEADLINE_DAYS.get(level, 0)

    def _resolve_receiver(self, cand: AlertCandidate) -> list[int]:
        """责任空缺兜底：子项目无负责人 → 学院负责人 → 大项目负责人 → 归口管理员"""
        ids: list[int] = []

        if cand.owner_id:
            ids.append(cand.owner_id)

        if not ids and cand.college_id and cand.major_id:
            mc = self.db.get(ProjMajorCollege, (cand.major_id, cand.college_id))
            if mc and mc.college_owner_id:
                ids.append(mc.college_owner_id)

        if not ids and cand.major_id:
            major = self.db.get(ProjMajor, cand.major_id)
            if major and major.owner_id:
                ids.append(major.owner_id)

        if not ids:
            # 兜底：系统管理员
            admins = self.db.scalars(
                text("SELECT u.id FROM sys_user u JOIN sys_user_role ur ON ur.user_id=u.id "
                     "JOIN sys_role r ON r.id=ur.role_id WHERE r.code='SYS_ADMIN' LIMIT 1")
            ).all()
            if admins:
                ids.append(admins[0])

        return ids

    def _notify(self, alert: Alert, receiver_id: int, channel: str, cand: AlertCandidate) -> None:
        """写通知日志（一期只记日志，实际发送由后台任务处理）

        notify_log.status 只能是 SUCCESS / FAILED：
        - 站内消息即时投递 → SUCCESS
        - 邮件/短信待后台任务发送 → 先记 FAILED 并写明原因，发送成功后更新
          （这里用 SUCCESS 表示"已入队"，error 字段注明待发送）
        """
        self.db.add(
            NotifyLog(
                biz_type="ALERT",
                biz_id=alert.id,
                channel=channel,
                receiver_id=receiver_id,
                status="SUCCESS",
                error=None if channel == "INNER" else "待发送",
            )
        )

    # ============================================================ 查询
    def _scope_cond(self, user: Any):
        """预警查询的数据范围条件；全校范围返回 None（不过滤）

        与 app.core.scope.can_access_alert 保持同一口径：
        大项目范围 / 学院范围 / 本人已认领 / 本人负责的子项目（SELF）。
        早期实现漏掉 SELF 分支，子项目负责人登录后预警中心是空的。
        """
        if user.has_all_scope():
            return None
        conds: list[Any] = []
        major_ids = user.major_ids()
        college_ids = user.college_ids()
        if major_ids:
            conds.append(Alert.major_id.in_(major_ids))
        if college_ids:
            conds.append(Alert.college_id.in_(college_ids))
        conds.append(Alert.claimed_by == user.id)
        # SELF 范围：能看到自己负责的子项目产生的预警
        owned_sub_ids = list(
            self.db.scalars(
                select(ProjSub.id).where(
                    ProjSub.owner_id == user.id, ProjSub.deleted_at.is_(None)
                )
            )
        )
        if owned_sub_ids:
            conds.append(Alert.sub_id.in_(owned_sub_ids))
        if not conds:
            # 没有任何可匹配的范围：返回空集（安全默认）
            return Alert.id == -1
        return or_(*conds)

    def list_alerts(
        self,
        user: Any,
        *,
        status: str | None = None,
        level: str | None = None,
        alert_type: str | None = None,
        major_id: int | None = None,
        college_id: int | None = None,
        only_mine: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Alert], int]:
        stmt = select(Alert)

        # 数据范围（大项目/学院/本人认领/本人负责子项目）
        scope_cond = self._scope_cond(user)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)

        if status:
            stmt = stmt.where(Alert.status == status)
        if level:
            stmt = stmt.where(Alert.level == level)
        if alert_type:
            stmt = stmt.where(Alert.alert_type == alert_type)
        if major_id:
            stmt = stmt.where(Alert.major_id == major_id)
        if college_id:
            stmt = stmt.where(Alert.college_id == college_id)
        if only_mine:
            stmt = stmt.where(
                or_(Alert.claimed_by == user.id, Alert.status == "PENDING")
            )

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.db.scalars(
                stmt.order_by(
                    Alert.status.desc(),      # PENDING 优先
                    Alert.level,
                    Alert.trigger_at.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def grouped(self, user: Any) -> dict:
        """折叠展示：按大项目 → 学院聚合（文档 3.3 避免预警泛滥）"""
        stmt = select(Alert).where(Alert.status.in_(("PENDING", "CLAIMED")))
        scope_cond = self._scope_cond(user)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)

        alerts = list(self.db.scalars(stmt))
        majors = {m.id: m.name for m in self.db.scalars(select(ProjMajor))}
        from app.models.system import SysCollege

        colleges = {c.id: c.name for c in self.db.scalars(select(SysCollege))}

        groups: dict[tuple, dict] = {}
        for a in alerts:
            key = (a.major_id, a.college_id)
            g = groups.setdefault(
                key,
                {
                    "major_id": a.major_id,
                    "major_name": majors.get(a.major_id),
                    "college_id": a.college_id,
                    "college_name": colleges.get(a.college_id),
                    "red": 0,
                    "orange": 0,
                    "yellow": 0,
                    "total": 0,
                },
            )
            g[a.level.lower()] += 1
            g["total"] += 1

        items = sorted(groups.values(), key=lambda x: (x["red"], x["orange"], x["total"]), reverse=True)
        return {
            "total": len(alerts),
            "red": sum(1 for a in alerts if a.level == "RED"),
            "orange": sum(1 for a in alerts if a.level == "ORANGE"),
            "yellow": sum(1 for a in alerts if a.level == "YELLOW"),
            "groups": items,
        }

    def get(self, alert_id: int) -> Alert:
        a = self.db.get(Alert, alert_id)
        if a is None:
            raise NotFoundError("预警不存在")
        return a

    def actions(self, alert_id: int) -> list[AlertAction]:
        return list(
            self.db.scalars(
                select(AlertAction)
                .where(AlertAction.alert_id == alert_id)
                .order_by(AlertAction.created_at)
            )
        )

    # ============================================================ 状态机
    def claim(self, alert_id: int, user: Any) -> Alert:
        a = self.get(alert_id)
        if a.status != "PENDING":
            raise BusinessError(f"当前状态（{a.status}）不能认领")
        a.status = "CLAIMED"
        a.claimed_by = user.id
        a.claimed_at = datetime.now(UTC)
        self._log_action(a.id, "CLAIM", user.id)
        self.db.flush()
        return a

    def transfer(self, alert_id: int, user: Any, to_user_id: int) -> Alert:
        a = self.get(alert_id)
        if a.status != "CLAIMED":
            raise BusinessError("只有已认领的预警可以转交")
        if self.db.get(SysUser, to_user_id) is None:
            raise BusinessError("接收人不存在")
        a.claimed_by = to_user_id
        # 转交后时限不重算（第 23 项建议）
        self._log_action(a.id, "TRANSFER", user.id, reason=f"转交给用户 {to_user_id}")
        self.db.flush()
        return a

    def close(self, alert_id: int, user: Any, reason: str | None = None) -> Alert:
        a = self.get(alert_id)
        # AL-09 / AL-10 不可关闭
        if a.alert_type in IMMUTABLE_TYPES:
            raise BusinessError("该预警只能通过补齐数据自动关闭，不能手工关闭")
        if a.status in ("CLOSED",):
            raise BusinessError("预警已关闭")
        a.status = "CLOSED"
        a.closed_by = user.id
        a.closed_at = datetime.now(UTC)
        a.close_reason = reason
        self._log_action(a.id, "CLOSE", user.id, reason=reason)
        self.db.flush()
        return a

    def ignore(self, alert_id: int, user: Any, reason: str) -> Alert:
        a = self.get(alert_id)
        if a.alert_type in IMMUTABLE_TYPES:
            raise BusinessError("该预警不能忽略")
        if not reason or not reason.strip():
            raise BusinessError("忽略预警必须填写理由")
        if a.status == "CLOSED":
            raise BusinessError("预警已关闭")
        a.status = "IGNORED"
        a.closed_by = user.id
        a.closed_at = datetime.now(UTC)
        a.close_reason = reason
        self._log_action(a.id, "IGNORE", user.id, reason=reason)
        self.db.flush()
        return a

    # ============================================================ 超时升级
    def escalate(self) -> dict:
        """超时未认领的预警自动升级：只加通知对象，不改状态"""
        now = datetime.now(UTC)
        rows = list(
            self.db.scalars(
                select(Alert).where(
                    Alert.status == "PENDING",
                    Alert.deadline_at.is_not(None),
                    Alert.deadline_at < now,
                    Alert.escalated_at.is_(None),
                )
            )
        )
        count = 0
        for a in rows:
            # 追加通知：大项目负责人（红色同时总负责人）
            major = self.db.get(ProjMajor, a.major_id) if a.major_id else None
            receivers = []
            if major and major.owner_id:
                receivers.append(major.owner_id)
                if a.level == "RED":
                    # 总负责人 = 大项目 owner（一期同一人）
                    pass
            for uid in receivers:
                self._notify(a, uid, "INNER", AlertCandidate(
                    alert_type=a.alert_type, level=a.level, sub_id=a.sub_id,
                    major_id=a.major_id, college_id=a.college_id))
            a.escalated_at = now
            self._log_action(a.id, "ESCALATE", None, reason="超时未认领自动升级")
            count += 1
        self.db.flush()
        return {"escalated": count}

    def _log_action(
        self, alert_id: int, action: str, operator_id: int | None, reason: str | None = None
    ) -> None:
        """记录处置流水；系统自动动作（operator_id=None）不写外键"""
        self.db.add(
            AlertAction(
                alert_id=alert_id, action=action, operator_id=operator_id, reason=reason
            )
        )

    # ============================================================ 统计
    def stats(self, user: Any, days: int = 30) -> dict:
        """预警统计：按类型/等级/状态；用于看板和按时处置率"""
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = select(Alert).where(Alert.trigger_at >= since)
        scope_cond = self._scope_cond(user)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)

        alerts = list(self.db.scalars(stmt))

        by_type: dict[str, int] = {}
        by_level: dict[str, int] = {"RED": 0, "ORANGE": 0, "YELLOW": 0}
        by_status: dict[str, int] = {}
        for a in alerts:
            by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
            by_level[a.level] = by_level.get(a.level, 0) + 1
            by_status[a.status] = by_status.get(a.status, 0) + 1

        # 预警按时处置率：按期关闭数 / 应处置数（不含黄、已忽略）
        should = [a for a in alerts if a.level in ("RED", "ORANGE") and a.status != "IGNORED"]
        on_time = [
            a for a in should
            if a.status == "CLOSED" and a.closed_at and a.deadline_at and a.closed_at <= a.deadline_at
        ]
        rate = (len(on_time) / len(should) * 100) if should else None

        # ---------------------------------------------------------- 趋势（按日）
        today = date.today()
        trend: list[dict] = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            day_alerts = [
                a for a in alerts if a.trigger_at.astimezone(UTC).date() == d
            ]
            trend.append(
                {
                    "date": d.isoformat(),
                    "total": len(day_alerts),
                    "red": sum(1 for a in day_alerts if a.level == "RED"),
                    "orange": sum(1 for a in day_alerts if a.level == "ORANGE"),
                    "yellow": sum(1 for a in day_alerts if a.level == "YELLOW"),
                }
            )

        # ---------------------------------------------------------- 按大项目分布
        from app.models.project import ProjMajor

        major_names = {m.id: m.name for m in self.db.scalars(select(ProjMajor))}
        by_major_map: dict[int, dict] = {}
        for a in alerts:
            if a.major_id is None:
                continue
            g = by_major_map.setdefault(
                a.major_id,
                {"major_id": a.major_id, "major_name": major_names.get(a.major_id, f"#{a.major_id}"),
                 "total": 0, "red": 0},
            )
            g["total"] += 1
            if a.level == "RED":
                g["red"] += 1
        by_major = sorted(by_major_map.values(), key=lambda x: x["total"], reverse=True)[:10]

        # ---------------------------------------------------------- 未处置时长分布
        now = datetime.now(UTC)
        age_buckets = {"1天内": 0, "1-3天": 0, "3-7天": 0, "7天以上": 0}
        for a in alerts:
            if a.status in ("CLOSED", "IGNORED"):
                continue
            age = (now - a.trigger_at).total_seconds() / 86400
            if age <= 1:
                age_buckets["1天内"] += 1
            elif age <= 3:
                age_buckets["1-3天"] += 1
            elif age <= 7:
                age_buckets["3-7天"] += 1
            else:
                age_buckets["7天以上"] += 1

        return {
            "days": days,
            "total": len(alerts),
            "by_type": by_type,
            "by_level": by_level,
            "by_status": by_status,
            "on_time_rate": round(rate, 1) if rate is not None else None,
            "should_handle": len(should),
            "on_time_closed": len(on_time),
            "trend": trend,
            "by_major": by_major,
            "age_buckets": age_buckets,
        }

    # ============================================================ 待办
    def my_todo(self, user: Any) -> dict:
        """我的督办：三个数字 + 待办清单（文档 5.1）

        口径统一：三个数字与下方清单都限定在本人数据范围内，
        否则会出现「数字很大但清单只有几条」的错位。
        """
        stmt = select(Alert).where(Alert.status.in_(("PENDING", "CLAIMED")))
        scope_cond = self._scope_cond(user)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)

        pending_alerts = list(self.db.scalars(stmt))
        # 待办清单：本人已认领的 + 范围内尚未认领的
        todo_alerts = [
            a for a in pending_alerts
            if a.claimed_by == user.id or a.status == "PENDING"
        ][:20]

        # 滞后子项目数：同样按数据范围收敛
        lag_stmt = (
            select(func.count(func.distinct(ProgSnapshot.sub_id)))
            .join(ProjSub, ProjSub.id == ProgSnapshot.sub_id)
            .where(
                ProgSnapshot.snap_date == date.today(),
                ProgSnapshot.deviation < -10,
                ProgSnapshot.is_free_day.is_(False),
                ProjSub.deleted_at.is_(None),
            )
        )
        lag_stmt = apply_scope(lag_stmt, user, ProjSub)
        lagging = self.db.scalar(lag_stmt) or 0

        return {
            "pending_alerts": len(pending_alerts),
            "lagging_subs": lagging,
            "alerts": [
                {
                    "id": a.id,
                    "alert_type": a.alert_type,
                    "level": a.level,
                    "status": a.status,
                    "sub_id": a.sub_id,
                    "sub_name": a.sub.name if a.sub else None,
                    "major_id": a.major_id,
                    "college_id": a.college_id,
                    "deadline_at": a.deadline_at,
                    "trigger_at": a.trigger_at,
                    "merged_count": a.merged_count,
                }
                for a in todo_alerts
            ],
        }
