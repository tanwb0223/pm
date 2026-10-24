"""通知投递服务：站内消息 / 邮件 / 短信

设计要点：
- 所有投递结果都落 notify_log，失败写原因，便于运维排查与重发
- 邮件走 SMTP（支持 SSL 与 STARTTLS），配置来自环境变量 PM_SMTP_*
- 短信仅红色预警触发，受参数 notify.sms.enabled 控制
- 通道未配置时**不报错**，记为 SKIPPED 并说明原因，避免影响主流程
- 发送失败按 retry_pending() 由后台任务重试，超过 MAX_RETRY 放弃

## 本次修复（对应 .workbuddy/audit/backend-system.md P0-2/3/4 与
## doc/闭环缺口汇总-20261024.md 第 3.1 节）

1. **站内消息落地（P0-2）**：`deliver()` 处理 INNER 时不再「写库即成功」，
   而是同时落一条 `SysMessage`（标题/正文复用 `_compose`，level 取预警等级）。
   另外用 `before_flush` 钩子兜底：任何代码路径（预警/催办/摘要/转交）
   只要新建了 INNER 的 NotifyLog，就自动补一条收件箱消息——
   催办写在 progress_service 里，不依赖它显式调用 deliver。
2. **摘要正文独立（P0-3）**：新增 `deliver_digest()`，正文由调用方直接传入，
   不再复用 `NotifyLog.error`（error 只放失败原因）。
3. **重试不再饥饿（P0-4）**：`retry_pending()` 只查 `FAILED`，SKIPPED 单独计数；
   按 id 游标推进，避免前 N 条反复失败把后面的记录饿死。
4. **邮件开关统一收口（P1-17）**：`_send_email()` 发送前统一检查
   `notify.email.enabled`，关闭时记 SKIPPED 并写明原因。
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import NotifyLog, SysMessage, SysUser

logger = logging.getLogger(__name__)

# 状态取值（notify_log.status）
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

# 待发送标记：_notify 入队时写入 error 字段，发送成功后清空
PENDING_MARK = "待发送"

# 后台重试的最大次数（按 error 里累计的尝试次数判断）
MAX_RETRY = 3

LEVEL_LABEL = {"RED": "红色", "ORANGE": "橙色", "YELLOW": "黄色"}

# 防重复标记：直接挂在 NotifyLog 实例上（不是 session.info）。
# 用实例属性而不是 id(obj)，避免对象被回收后 id 复用导致误判。
_DONE_ATTR = "_pm_inner_message_done"   # 已生成过收件箱消息
_SKIP_ATTR = "_pm_skip_auto_inner"      # 调用方要自定义文案，跳过默认消息

# retry_pending 的 id 游标：跨调用保留，避免每轮都从最小 id 重扫，
# 让「前面几条持续失败」不会把后面的记录永久饿死（P0-4）。
# 多 worker 各自持有游标也安全：只是多扫几轮，不会漏。
_retry_cursor = 0


def suppress_auto_inner(row: NotifyLog) -> None:
    """标记该 INNER 通知不要自动生成默认站内消息

    转交等场景要发自定义文案，调用方会自己写 SysMessage；
    如果不标记，before_flush 钩子会先补一条默认文案，造成重复提醒。
    """
    setattr(row, _SKIP_ATTR, True)


def _mark_inner_done(row: NotifyLog) -> None:
    setattr(row, _DONE_ATTR, True)


def _is_inner_done(row: NotifyLog) -> bool:
    return bool(getattr(row, _DONE_ATTR, False))


class NotifyService:
    """通知投递：把 notify_log 里的待发送记录真正发出去"""

    def __init__(self, db: Session):
        self.db = db

    # ============================================================ 入口
    def deliver(self, log_id: int) -> str:
        """投递单条通知，返回最终状态

        - INNER：落一条 SysMessage（用户收件箱）后记 SUCCESS。
          标题/正文复用 `_compose` 的结果，level 取预警等级。
          「写进 notify_log 就成功」是本次修复前的核心断点：
          notify_log 是运维日志，被通知人读不到（P0-2）。
        - EMAIL / SMS：走实际发送，失败时把原因写回 error 便于重试。
        """
        row = self.db.get(NotifyLog, log_id)
        if row is None:
            return STATUS_FAILED

        if row.channel == "INNER":
            self._ensure_inner_message(row)
            row.status = STATUS_SUCCESS
            row.error = None
            return STATUS_SUCCESS

        if row.channel == "EMAIL":
            return self._send_email(row)
        if row.channel == "SMS":
            return self._send_sms(row)

        row.status = STATUS_SKIPPED
        row.error = f"未知通道 {row.channel}"
        return STATUS_SKIPPED

    def push_message(
        self,
        *,
        receiver_id: int,
        biz_type: str,
        biz_id: int | None,
        title: str,
        body: str,
        level: str | None = None,
    ) -> SysMessage:
        """直接往收件箱推一条站内消息（不经过 notify_log）

        用途：每日摘要这类「正文由调用方拼好」的消息。
        走 `deliver()` 的话正文要重新从业务对象反推，摘要没有业务对象
        （biz_id 为空），只能靠 error 字段携带，正是先前的 bug 来源。
        """
        msg = SysMessage(
            receiver_id=receiver_id,
            biz_type=biz_type,
            biz_id=biz_id,
            level=level,
            title=(title or "")[:255],
            body=(body or "")[:2000],
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def deliver_digest(self, receiver_id: int, subject: str, body: str) -> str:
        """发送每日摘要邮件（正文由调用方直接传入）

        为什么单独开一个入口：早期摘要把统计 JSON 塞进 `NotifyLog.error`
        当正文数据源，`deliver()` 一旦失败就用错误文案覆盖 error，
        重投时 `_compose_digest` 只能解析出全 0（审计 P0-3）。
        正文必须独立传递，error 只放失败原因。
        """
        row = NotifyLog(
            biz_type="DIGEST",
            biz_id=None,
            channel="EMAIL",
            receiver_id=receiver_id,
            status=STATUS_FAILED,
            error=PENDING_MARK,
        )
        self.db.add(row)
        self.db.flush()
        return self._send_email(row, subject=subject, body=body)

    def _ensure_inner_message(self, row: NotifyLog) -> None:
        """确保一条 INNER 通知在收件箱里有对应消息（幂等）"""
        if _is_inner_done(row):
            return
        user = self.db.get(SysUser, row.receiver_id)
        subject, body = self._compose(row, user)
        level = None
        if row.biz_type == "ALERT" and row.biz_id:
            from app.models.alert import Alert

            alert = self.db.get(Alert, row.biz_id)
            level = alert.level if alert else None
        self.db.add(
            SysMessage(
                receiver_id=row.receiver_id,
                biz_type=row.biz_type,
                biz_id=row.biz_id,
                level=level,
                title=(subject or "")[:255],
                body=(body or "")[:2000],
            )
        )
        _mark_inner_done(row)

    def retry_pending(self, limit: int = 100) -> dict[str, int]:
        """重投失败的邮件/短信

        修复点（P0-4）：
        1. 只查 `status == FAILED`。早期把 SKIPPED 也算进来，而 SKIPPED
           （未配邮箱/SMTP）的尝试次数恒为 0，永远「处理不完」，
           前 100 条 SKIPPED 会把真正 FAILED 的记录永久堵在队列后面。
        2. 按 id 游标推进：一次取 `id > cursor` 的下一批，扫到末尾回到 0。
           这样即使前几条一直失败，也不会让后面的记录永远排不上。
        3. SKIPPED 单独计数（不重试），便于运维区分「发失败」与「没配置」。
        """
        global _retry_cursor

        stmt = (
            select(NotifyLog)
            .where(
                NotifyLog.channel.in_(("EMAIL", "SMS")),
                NotifyLog.status == STATUS_FAILED,
                NotifyLog.id > _retry_cursor,
            )
            .order_by(NotifyLog.id)
            .limit(limit)
        )
        rows = list(self.db.scalars(stmt))
        if rows:
            _retry_cursor = rows[-1].id
        else:
            # 已扫到末尾：下一轮从头开始，保证后续新增的失败记录能被重试
            _retry_cursor = 0

        stats = {"success": 0, "failed": 0, "skipped": 0, "exhausted": 0}
        for row in rows:
            attempts = self._attempts(row.error)
            if attempts >= MAX_RETRY:
                stats["exhausted"] += 1
                continue
            result = self.deliver(row.id)
            if result == STATUS_SUCCESS:
                stats["success"] += 1
            elif result == STATUS_SKIPPED:
                stats["skipped"] += 1
            else:
                stats["failed"] += 1

        # SKIPPED 单独计数（不重试）：这些记录缺配置/缺邮箱，按设计不进重试队列。
        # 单独报出来只是让运维知道「有多少条根本没发出去」，而不是反复重试。
        stats["skipped_total"] = (
            self.db.scalar(
                select(func.count())
                .select_from(NotifyLog)
                .where(
                    NotifyLog.channel.in_(("EMAIL", "SMS")),
                    NotifyLog.status == STATUS_SKIPPED,
                )
            )
            or 0
        )
        return stats

    # ============================================================ 邮件
    def _send_email(
        self, row: NotifyLog, subject: str | None = None, body: str | None = None
    ) -> str:
        """发送邮件；subject/body 为空时按 biz_type 组装"""
        # 统一开关（P1-17）：早期 notify.email.enabled 只在预警入队时判断，
        # 摘要/催办/重投都不受控；现在所有邮件发送都经过这里。
        from app.services.config_service import ConfigService

        if not ConfigService(self.db).get_bool("notify.email.enabled", True):
            row.status = STATUS_SKIPPED
            row.error = "邮件通道已关闭（参数 notify.email.enabled=false）"
            return STATUS_SKIPPED

        user = self.db.get(SysUser, row.receiver_id)
        to_addr = (user.email or "").strip() if user else ""
        if not to_addr:
            row.status = STATUS_SKIPPED
            row.error = "接收人未配置邮箱"
            return STATUS_SKIPPED

        if not settings.smtp_host:
            row.status = STATUS_SKIPPED
            row.error = "未配置 SMTP（PM_SMTP_HOST）"
            return STATUS_SKIPPED

        if not subject or not body:
            subject, body = self._compose(row, user)
        try:
            msg = self._build_message(to_addr, subject, body)
            self._smtp_send(msg, to_addr)
        except Exception as exc:  # 网络、认证、收件人拒收等
            attempts = self._attempts(row.error) + 1
            row.status = STATUS_FAILED
            row.error = f"发送失败(第{attempts}次)：{type(exc).__name__}: {exc}"[:500]
            logger.warning("邮件发送失败 → %s: %s", to_addr, exc)
            return STATUS_FAILED

        row.status = STATUS_SUCCESS
        row.error = None
        logger.info("邮件已发送 → %s (%s)", to_addr, subject)
        return STATUS_SUCCESS

    def _build_message(self, to_addr: str, subject: str, body: str) -> EmailMessage:
        sender = settings.mail_from or settings.smtp_user
        msg = EmailMessage()
        # 邮件头必须按 RFC 2047 编码，否则中文主题会让 smtplib 在
        # ascii 编码阶段直接抛 UnicodeEncodeError，邮件永远发不出去。
        # EmailMessage 只接受 str，Header 需先 str() 成已编码形式。
        msg["Subject"] = str(Header(subject, "utf-8"))
        # formataddr 会校验地址部分必须为 ASCII：sender 为空时它会把
        # 中文显示名当作地址，抛 UnicodeEncodeError。因此仅在地址有效时
        # 才拼「显示名 <地址>」，否则退化为纯地址。
        if sender and "@" in sender:
            try:
                msg["From"] = formataddr((settings.app_name, sender))
            except UnicodeEncodeError:
                msg["From"] = sender
        else:
            msg["From"] = sender or f"noreply@{settings.smtp_host or 'localhost'}"
        msg["To"] = to_addr
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        msg.set_content(body, subtype="plain", charset="utf-8")
        return msg

    def _smtp_send(self, msg: EmailMessage, to_addr: str) -> None:
        host, port = settings.smtp_host, settings.smtp_port
        user, password = settings.smtp_user, settings.smtp_password
        context = ssl.create_default_context()

        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            # 服务器支持 STARTTLS 才升级；不支持时保持明文（内网邮件中继常见）。
            # 早期实现无条件 starttls，遇到不支持扩展的服务器直接抛
            # SMTPNotSupportedError，邮件发不出去。
            if smtp.has_extn("starttls"):
                smtp.starttls(context=context)
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)

    # ============================================================ 短信
    def _send_sms(self, row: NotifyLog) -> str:
        """短信通道：仅红色预警

        学校短信网关差异大（阿里云/腾讯云/自建网关），这里留出统一出口：
        PM_SMS_PROVIDER 为 http 时按 {PM_SMS_ACCESS_KEY} 作为地址 POST。
        未配置时不报错，记 SKIPPED，避免拖垮预警主流程。
        """
        user = self.db.get(SysUser, row.receiver_id)
        phone = (user.phone or "").strip() if user else ""
        if not phone:
            row.status = STATUS_SKIPPED
            row.error = "接收人未配置手机号"
            return STATUS_SKIPPED

        # 短信要真正发出，需同时满足两层开关：
        #   1) 参数 notify.sms.enabled（决定是否入队，在 alert_service 判断）
        #   2) 环境变量 PM_SMS_ENABLED + 网关地址（决定是否真发）
        # 这里失败时把两层都说清楚，否则运维只看到「未启用」却不知道差哪一层。
        if not settings.sms_enabled or not settings.sms_provider:
            row.status = STATUS_SKIPPED
            missing = []
            if not settings.sms_enabled:
                missing.append("PM_SMS_ENABLED=false")
            if not settings.sms_provider:
                missing.append("PM_SMS_PROVIDER 未配置")
            row.error = (
                "短信未发送：" + "、".join(missing)
                + "（参数 notify.sms.enabled 已允许入队，但服务端未配置网关）"
            )
            return STATUS_SKIPPED

        try:
            self._http_sms(phone, self._compose_text(row))
        except Exception as exc:
            attempts = self._attempts(row.error) + 1
            row.status = STATUS_FAILED
            row.error = f"短信发送失败(第{attempts}次)：{type(exc).__name__}: {exc}"[:500]
            logger.warning("短信发送失败 → %s: %s", phone, exc)
            return STATUS_FAILED

        row.status = STATUS_SUCCESS
        row.error = None
        return STATUS_SUCCESS

    def _http_sms(self, phone: str, text: str) -> None:
        import json
        import urllib.request

        payload = json.dumps(
            {"phone": phone, "content": text}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            settings.sms_provider,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.sms_access_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"网关返回 {resp.status}")

    # ============================================================ 文案
    def _compose(self, row: NotifyLog, user: SysUser | None) -> tuple[str, str]:
        """组装标题与正文（站内消息与邮件共用）"""
        name = user.name if user else "老师"
        if row.biz_type == "ALERT":
            return self._compose_alert(row, name)
        if row.biz_type == "DIGEST":
            return self._compose_digest(row, name)
        if row.biz_type == "URGE":
            return self._compose_urge(row, name)
        return (
            f"【{settings.app_name}】通知",
            f"{name}，您好：\n\n您有一条系统通知（{row.biz_type}#{row.biz_id}）。\n",
        )

    def _compose_alert(self, row: NotifyLog, name: str) -> tuple[str, str]:
        from app.models.alert import Alert

        alert = self.db.get(Alert, row.biz_id) if row.biz_id else None
        if alert is None:
            return (
                f"【{settings.app_name}】预警提醒",
                f"{name}，您好：\n\n您有一条待处置预警，请登录系统查看。\n",
            )

        level = LEVEL_LABEL.get(alert.level, alert.level)
        title = f"【{settings.app_name}】{level}预警：{alert.alert_type}"
        lines = [
            f"{name}，您好：",
            "",
            f"系统产生一条{level}预警，请及时处理。",
            "",
            f"预警类型：{alert.alert_type}",
            f"预警等级：{level}",
            f"触发时间：{self._fmt(alert.trigger_at)}",
        ]
        if alert.deadline_at:
            lines.append(f"处置时限：{self._fmt(alert.deadline_at)}")
        lines += [
            "",
            "请登录系统查看详情并处置：站内「预警中心」。",
            "",
            f"—— {settings.app_name}",
        ]
        return title, "\n".join(lines)

    def _compose_urge(self, row: NotifyLog, name: str) -> tuple[str, str]:
        """催办文案（P0-2：催办必须进收件箱）

        早期 INNER 催办只写 notify_log，负责人看不到；补上站内消息后
        至少要知道「哪个子项目被催了」。
        """
        sub_name = None
        if row.biz_id:
            try:
                from app.models.project import ProjSub

                sub = self.db.get(ProjSub, row.biz_id)
                sub_name = sub.name if sub else None
            except Exception:  # 子项目已被删等，退回通用文案
                sub_name = None
        target = f"「{sub_name}」" if sub_name else "您负责的子项目"
        return (
            f"【{settings.app_name}】进度催办提醒",
            f"{name}，您好：\n\n{target}收到一条进度催办，请尽快更新进度。\n\n"
            f"请登录系统处理：站内「进度总表」。\n\n—— {settings.app_name}",
        )

    def _compose_digest(self, row: NotifyLog, name: str) -> tuple[str, str]:
        """每日摘要正文（兜底文案）

        新链路走 `deliver_digest(receiver_id, subject, body)` 直接传正文；
        这里只服务历史遗留的 DIGEST 记录（早期正文曾塞在 error 里，
        已被失败原因覆盖，无法恢复），给出可读的兜底文案。
        """
        return (
            f"【{settings.app_name}】每日预警摘要",
            f"{name}，您好：\n\n今日预警统计已生成，请登录系统查看：站内「预警中心」"
            f" → 预警统计。\n\n—— {settings.app_name}",
        )

    def _compose_text(self, row: NotifyLog) -> str:
        from app.models.alert import Alert

        alert = self.db.get(Alert, row.biz_id) if row.biz_id else None
        if alert is None:
            return f"【{settings.app_name}】您有一条待处置预警"
        level = LEVEL_LABEL.get(alert.level, alert.level)
        return (
            f"【{settings.app_name}】{level}预警 {alert.alert_type}，"
            f"触发于 {self._fmt(alert.trigger_at)}，请登录系统处置"
        )

    @staticmethod
    def _fmt(dt: Any) -> str:
        if dt is None:
            return "-"
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except AttributeError:
            return str(dt)

    @staticmethod
    def _attempts(error: str | None) -> int:
        """从 error 文案里解析已尝试次数"""
        if not error:
            return 0
        import re

        m = re.search(r"第(\d+)次", error)
        return int(m.group(1)) if m else 0


# ================================================================ 自动兜底
@event.listens_for(Session, "before_flush")
def _auto_inner_message(session: Session, flush_context, instances) -> None:
    """任何新建的 INNER 通知都自动补一条站内消息（P0-2 兜底）

    为什么放在 flush 钩子里：预警（alert_service）会显式调 deliver()，
    但催办写在 progress_service.urge() 里、只建 NotifyLog 不投递；
    转交、升级等新增路径也可能忘记调 deliver。统一在这里兜底，
    保证「凡 INNER 必进收件箱」，而不是每个调用方各写一遍。
    """
    for obj in list(session.new):
        if not isinstance(obj, NotifyLog):
            continue
        if obj.channel != "INNER":
            continue
        if getattr(obj, _SKIP_ATTR, False) or getattr(obj, _DONE_ATTR, False):
            continue
        try:
            NotifyService(session)._ensure_inner_message(obj)
        except Exception:  # 兜底失败不能阻断业务写入
            logger.exception("自动生成站内消息失败：log_id=%s", getattr(obj, "id", None))
