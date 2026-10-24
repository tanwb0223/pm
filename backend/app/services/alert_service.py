"""预警服务：扫描、去重、状态机、升级、通知

## 本次修复（对应 doc/闭环缺口汇总-20261024.md 与
## .workbuddy/audit/backend-business.md / backend-system.md）

P0
- 橙色预警补发邮件（受参数 notify.orange.email 控制，P0-2）
- `scan(day, only_codes)` 支持按规则子集扫描：小时任务只扫 AL-06，
  不再因为「当天没生成快照」把 AL-01/AL-03/AL-11 的预警批量误关（P0-5）

P1
- 转交给新接收人发 INNER + EMAIL（P1-7）
- 手工关闭后 7 天内同源不再新建（dedup 纳入 CLOSED，P1-8）
- 预警触发详情落库 detail；AL-08 的 dedup_key 加 risk_id（P1-9）
- 黄色预警不进待办；待办按等级+时限排序（P1-10）
- 红色升级补推项目总负责人（P1-11）
- 列表显式排序：状态优先级 + 等级 RED>ORANGE>YELLOW + 触发时间倒序（P1-12）
- 自动关闭写 [AUTO] 前缀，按时处置率排除自动关闭（P1-13）
- handle_rollback 检查 alert.AL-04.enabled（P1-14）
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, NotFoundError
from app.core.scope import apply_scope
from app.models.alert import Alert, AlertAction
from app.models.project import ProgSnapshot, ProjMajor, ProjMajorCollege, ProjSub
from app.models.system import NotifyLog, SysMessage, SysUser
from app.services.alert_rules import (
    IMMUTABLE_TYPES,
    LEVEL_ORDER,
    SNAPSHOT_RULE_CODES,
    AlertCandidate,
    RuleContext,
    build_rules,
    sort_candidates,
)
from app.services.calendar_service import CalendarService
from app.services.config_service import ConfigService
from app.services.notify_service import PENDING_MARK, suppress_auto_inner

logger = logging.getLogger(__name__)

# 等级 → 处置时限（天）
DEADLINE_DAYS = {"RED": 3, "ORANGE": 7, "YELLOW": 0}
# 同源合并窗口（天）
DEDUP_WINDOW_DAYS = 7
# 自动关闭的 close_reason 前缀：stats() 的按时处置率据此排除自动关闭
AUTO_CLOSE_PREFIX = "[AUTO]"


class AlertService:
    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService(db)

    # ============================================================ 扫描
    def scan(
        self,
        day: date | None = None,
        only_codes: set[str] | None = None,
    ) -> dict:
        """执行扫描规则：生成/合并预警，并自动关闭条件已消失的预警

        Args:
            day: 扫描基准日（默认今天）
            only_codes: 只执行这些规则（如小时任务传 {"AL-06"}）。
                为什么需要：小时任务原本调 `scan()` 跑全部规则，但此时
                当天快照还没生成（01:00 才生成），AL-01/AL-03/AL-11
                全部「未命中」，`_auto_close` 就把在办预警批量误关，
                理由写成「触发条件已消失」。限制规则子集后，
                自动关闭也只作用于本次真正执行的规则（P0-5）。
        """
        day = day or date.today()
        cal = CalendarService.load(self.db)
        ctx = RuleContext(db=self.db, day=day, calendar=cal, config=self.config)

        # P0-5 双重保护：除了用 only_codes 限定规则子集，还要确认当天快照
        # 真的存在。快照规则（AL-01/02/03/11）全部依赖 prog_snapshot；
        # 若当天快照尚未生成（小时任务 01:05 前跑、手动扫描、快照任务失败），
        # 这些规则必然「未命中」，_auto_close 会把在办预警批量误关，
        # 理由还写成「触发条件已消除」。宁可不判，也不能误关。
        snapshot_ready = self._has_snapshot(day)

        all_candidates: list[AlertCandidate] = []
        rule_stats: dict[str, int] = {}
        evaluated: set[str] = set()

        for rule in build_rules():
            if rule.trigger_mode == "event":
                continue
            if only_codes is not None and rule.code not in only_codes:
                # 未在本次范围内：既不算「已执行」，也不参与自动关闭
                continue
            if not snapshot_ready and rule.code in SNAPSHOT_RULE_CODES:
                # 当天无快照：跳过判定，同时不让 _auto_close 处理该规则
                rule_stats[rule.code] = 0
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

    def _has_snapshot(self, day: date) -> bool:
        """当天是否已生成进度快照（P0-5 保护判定）"""
        row = self.db.scalar(
            select(ProgSnapshot.sub_id).where(ProgSnapshot.snap_date == day).limit(1)
        )
        return row is not None

    def _auto_close(self, candidates: list[AlertCandidate], evaluated: set[str]) -> int:
        """自动关闭：本次扫描未再命中的预警（触发条件已消失）

        AL-09 / AL-10 被设计为「只能通过补齐数据自动关闭、不能手工关闭」，
        这里就是那条自动关闭路径。

        只处理本次**成功执行**的规则，某条规则抛异常时不动它的预警，
        避免因为一次扫描失败把大批预警误关。
        事件驱动规则（AL-04）不参与：它不在扫描候选里，天然会「未命中」。

        自动关闭写 `[AUTO]` 前缀（P1-13）：stats() 的按时处置率必须能
        区分「人工按期关闭」与「系统自动关闭」，否则后者会把处置率虚高。
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
        reason = f"{AUTO_CLOSE_PREFIX}触发条件已消除，系统自动关闭"
        for a in rows:
            if a.dedup_key in triggered:
                continue
            a.status = "CLOSED"
            a.closed_at = datetime.now(UTC)
            a.close_reason = reason
            self._log_action(a.id, "CLOSE", None, reason=reason)
            closed += 1
        return closed

    def handle_rollback(self, sub: ProjSub, reason: str | None) -> None:
        """事件驱动：进度回调时生成 AL-04

        P1-14：必须检查 `alert.AL-04.enabled`，否则参数页关掉规则后
        回调仍会生成预警（其他规则都在 scan 里读 enabled，唯独这里漏了）。
        """
        if not self.config.get_bool("alert.AL-04.enabled", True):
            return

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

        CLOSED 也参与去重（P1-8）：手工关闭后如果触发条件还在，下一次扫描
        立刻又建一条同源预警，等于「关闭」按钮没用。7 天内同源只合并计数。
        """
        since = datetime.now(UTC) - timedelta(days=DEDUP_WINDOW_DAYS)
        exist = self.db.scalar(
            select(Alert)
            .where(
                Alert.dedup_key == cand.dedup_key,
                or_(
                    # 在办 / 已忽略：窗口内不重复建
                    (
                        Alert.status.in_(("PENDING", "CLAIMED", "IGNORED"))
                        & (Alert.created_at >= since)
                    ),
                    # 已关闭：关闭后 7 天内同源也不再新建（P1-8 去重抑制）
                    (
                        (Alert.status == "CLOSED")
                        & (Alert.closed_at.is_not(None))
                        & (Alert.closed_at >= since)
                    ),
                ),
            )
            .order_by(Alert.id.desc())
        )
        if exist is not None:
            exist.merged_count += 1
            # P1-9：合并时把新详情并入已有记录（可覆盖），
            # 否则处置人看到的永远是第一次触发时的旧数字。
            if cand.detail:
                merged = dict(exist.detail or {})
                merged.update(cand.detail)
                exist.detail = merged
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
            # P1-9：规则算出的触发详情必须落库，处置人才能知道「为什么」
            # （连续滞后几天、阈值多少、哪条风险超期）。
            detail=cand.detail or None,
        )
        self.db.add(alert)
        self.db.flush()

        # 通知（黄色仅提示，不进待办）
        # 邮件/短信通道受参数控制（早期 notify.email.enabled 改了不生效）
        email_enabled = self.config.get_bool("notify.email.enabled", True)
        # P0-2：橙色也要发邮件（橙色占 11 条规则里的 6 条，是主要触达手段）。
        # 受新参数 notify.orange.email 控制，可单独关闭。
        orange_email = self.config.get_bool("notify.orange.email", True)
        sms_enabled = self.config.get_bool("notify.sms.enabled", False)
        if cand.level in ("RED", "ORANGE"):
            for uid in receiver:
                self._notify(alert, uid, "INNER", cand)
                send_email = cand.level == "RED" or (
                    cand.level == "ORANGE" and orange_email
                )
                if email_enabled and send_email:
                    self._notify(alert, uid, "EMAIL", cand)
                # 需求 3.3(3)：短信只发红色预警，管理员可开关
                if cand.level == "RED" and sms_enabled:
                    self._notify(alert, uid, "SMS", cand)
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
        """写通知日志并投递

        notify_log.status 只能是 SUCCESS / FAILED / SKIPPED：
        - 站内消息：deliver() 会落一条 SysMessage 到收件箱，再记成功
        - 邮件/短信：先落一条「待发送」，随即尝试实际发送。
          需求要求红色预警「当时就推」，因此这里同步发送；
          失败不影响预警主流程（异常吞掉并留痕），由后台任务重试。
        """
        row = NotifyLog(
            biz_type="ALERT",
            biz_id=alert.id,
            channel=channel,
            receiver_id=receiver_id,
            status="SUCCESS" if channel == "INNER" else "FAILED",
            error=None if channel == "INNER" else PENDING_MARK,
        )
        self.db.add(row)
        self.db.flush()

        if channel == "INNER":
            # P0-2：INNER 不再是「写库即成功」，必须落收件箱消息。
            # 显式调用 deliver()（before_flush 钩子也有兜底，此处保证时序）。
            from app.services.notify_service import NotifyService

            NotifyService(self.db).deliver(row.id)
            return

        # 邮件/短信实际投递；任何异常都不应中断预警扫描
        try:
            from app.services.notify_service import NotifyService

            NotifyService(self.db).deliver(row.id)
        except Exception:
            logger.exception("通知投递异常：log_id=%s channel=%s", row.id, channel)

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
            # "只看我的" = 我认领的 + 我负责的子项目产生的。
            # 早期写成 or_(claimed_by == 我, status == 'PENDING')，
            # 结果勾选后反而把范围内所有人的待处置预警都返回了。
            stmt = stmt.where(
                or_(
                    Alert.claimed_by == user.id,
                    Alert.sub_id.in_(
                        select(ProjSub.id).where(
                            ProjSub.owner_id == user.id, ProjSub.deleted_at.is_(None)
                        )
                    ),
                )
            )

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

        # P1-12：显式排序。早期用 status.desc()/level 按字符串排，
        # 实际顺序是 PENDING→IGNORED→CLOSED→CLAIMED（认领后掉出第一页），
        # 等级按字母序 ORANGE<RED（红色反而不靠前）。
        status_order = case(
            {"PENDING": 0, "CLAIMED": 1, "IGNORED": 2, "CLOSED": 3},
            value=Alert.status,
            else_=9,
        )
        level_order = case(
            {"RED": 0, "ORANGE": 1, "YELLOW": 2},
            value=Alert.level,
            else_=9,
        )
        rows = list(
            self.db.scalars(
                stmt.order_by(
                    status_order,
                    level_order,
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

    def measure(self, alert_id: int, user: Any, measure: str) -> Alert:
        """填写处置措施（需求 3.3(4)：已认领 → 填写处置措施、关闭、转交）

        只有 CLAIMED 状态才能填：预警还没人认领时填措施没有责任主体，
        关闭后也不需要再补措施。措施写 AlertAction(action='MEASURE')，
        详情页按时间线展示。
        """
        a = self.get(alert_id)
        if a.status != "CLAIMED":
            raise BusinessError(f"当前状态（{a.status}）不能填写处置措施，请先认领")
        text = (measure or "").strip()
        if not text:
            raise BusinessError("处置措施不能为空")
        self._log_action(a.id, "MEASURE", user.id, reason=text[:500])
        self.db.flush()
        return a

    def transfer(self, alert_id: int, user: Any, to_user_id: int) -> Alert:
        """转交预警给其他负责人

        P1-7：转交后新接收人必须收到通知，否则「转交」等于把预警丢进黑洞。
        站内消息用自定义文案（含「预警已转交给您」），
        邮件在有邮箱且开关打开时同步发。
        """
        a = self.get(alert_id)
        if a.status != "CLAIMED":
            raise BusinessError("只有已认领的预警可以转交")
        to_user = self.db.get(SysUser, to_user_id)
        if to_user is None:
            raise BusinessError("接收人不存在")
        a.claimed_by = to_user_id
        # 转交后时限不重算（第 23 项建议）
        self._log_action(a.id, "TRANSFER", user.id, reason=f"转交给用户 {to_user_id}")
        self.db.flush()

        # ---- P1-7 通知新接收人（站内 + 邮件）
        level_label = {"RED": "红色", "ORANGE": "橙色", "YELLOW": "黄色"}.get(a.level, a.level)
        title = f"【预警转交】{level_label}预警 {a.alert_type} 已转交给您"
        body = (
            f"{to_user.name}，您好：\n\n"
            f"{user.name} 将一条{level_label}预警转交给您处置，请及时处理。\n\n"
            f"预警类型：{a.alert_type}\n"
            f"预警等级：{level_label}\n"
        )
        if a.deadline_at:
            body += f"处置时限：{a.deadline_at.strftime('%Y-%m-%d %H:%M')}\n"
        body += "\n请登录系统查看详情并处置：站内「预警中心」。\n"

        from app.services.notify_service import NotifyService

        svc = NotifyService(self.db)
        inner_row = NotifyLog(
            biz_type="ALERT",
            biz_id=a.id,
            channel="INNER",
            receiver_id=to_user_id,
            status="SUCCESS",
            error=None,
        )
        self.db.add(inner_row)
        # 自定义文案：跳过 before_flush 的默认自动消息，避免重复两条
        suppress_auto_inner(inner_row)
        self.db.flush()
        # 直接写收件箱（标题/正文是转交专用文案）
        self.db.add(
            SysMessage(
                receiver_id=to_user_id,
                biz_type="ALERT",
                biz_id=a.id,
                level=a.level,
                title=title[:255],
                body=body[:2000],
            )
        )
        self.db.flush()

        # 邮件：有邮箱且 notify.email.enabled 打开时发（开关在 _send_email 内统一校验）
        if (to_user.email or "").strip():
            email_row = NotifyLog(
                biz_type="ALERT",
                biz_id=a.id,
                channel="EMAIL",
                receiver_id=to_user_id,
                status="FAILED",
                error=PENDING_MARK,
            )
            self.db.add(email_row)
            self.db.flush()
            try:
                svc.deliver(email_row.id)
            except Exception:
                logger.exception("转交通知邮件发送异常：alert_id=%s", a.id)
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
        """超时未认领的预警自动升级：只加通知对象，不改状态

        P1-11：红色预警除大项目负责人外，还要推给项目总负责人
        （PROJECT_OWNER 角色用户）。早期分支里只写了一个空 `pass`，
        红色升级实际与橙色没有区别。
        """
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
            # 追加通知：大项目负责人（红色同时推项目总负责人）
            major = self.db.get(ProjMajor, a.major_id) if a.major_id else None
            receivers: list[int] = []
            if major and major.owner_id:
                receivers.append(major.owner_id)
            if a.level == "RED":
                # 项目总负责人 = 拥有 PROJECT_OWNER 角色的启用用户
                # （一期与 major.owner 可能是同一人，这里按 id 去重）
                for uid in self._project_owner_ids():
                    if uid not in receivers:
                        receivers.append(uid)
            for uid in receivers:
                self._notify(a, uid, "INNER", AlertCandidate(
                    alert_type=a.alert_type, level=a.level, sub_id=a.sub_id,
                    major_id=a.major_id, college_id=a.college_id))
            a.escalated_at = now
            self._log_action(a.id, "ESCALATE", None, reason="超时未认领自动升级")
            count += 1
        self.db.flush()
        return {"escalated": count}

    def _project_owner_ids(self) -> list[int]:
        """启用状态的 PROJECT_OWNER 角色用户 id"""
        return list(
            self.db.scalars(
                text(
                    "SELECT DISTINCT u.id FROM sys_user u "
                    "JOIN sys_user_role ur ON ur.user_id=u.id "
                    "JOIN sys_role r ON r.id=ur.role_id "
                    "WHERE r.code='PROJECT_OWNER' AND u.status='ACTIVE'"
                )
            ).all()
        )

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
        # P1-13：自动关闭（close_reason 以 [AUTO] 开头）不计入分子。
        # 自动关闭只代表「触发条件消失了」，不代表责任人按期处置，
        # 混进来会把处置率虚高到接近 100%。
        def _is_auto_closed(a: Alert) -> bool:
            return bool(a.close_reason and a.close_reason.startswith(AUTO_CLOSE_PREFIX))

        should = [
            a for a in alerts
            if a.level in ("RED", "ORANGE")
            and a.status != "IGNORED"
            and not _is_auto_closed(a)
        ]
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

        P1-10：
        - 黄色预警不进待办（需求：黄色仅提示，不进督办清单）
        - 排序：RED > ORANGE，同等级按处置时限升序（最急的在前）
        """
        stmt = select(Alert).where(Alert.status.in_(("PENDING", "CLAIMED")))
        scope_cond = self._scope_cond(user)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)

        pending_alerts = list(self.db.scalars(stmt))
        # 黄色不进待办（与 pending_alerts 计数口径一致）
        actionable = [a for a in pending_alerts if a.level != "YELLOW"]
        # 待办清单：本人已认领的 + 范围内尚未认领的
        todo_alerts = [
            a for a in actionable
            if a.claimed_by == user.id or a.status == "PENDING"
        ]
        # 排序：等级优先（RED 0 < ORANGE 1），同等级按处置时限升序，
        # 无时限的排最后；最后用触发时间兜底保证稳定顺序。
        todo_alerts.sort(
            key=lambda a: (
                LEVEL_ORDER.get(a.level, 9),
                a.deadline_at is None,
                a.deadline_at or datetime.max.replace(tzinfo=UTC),
                -a.trigger_at.timestamp() if a.trigger_at else 0,
            )
        )
        todo_alerts = todo_alerts[:20]

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
            # P1-10：黄色预警不计入待办数字
            "pending_alerts": len(actionable),
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
