"""通知投递服务：站内消息 / 邮件 / 短信

设计要点：
- 所有投递结果都落 notify_log，失败写原因，便于运维排查与重发
- 邮件走 SMTP（支持 SSL 与 STARTTLS），配置来自环境变量 PM_SMTP_*
- 短信仅红色预警触发，受参数 notify.sms.enabled 控制
- 通道未配置时**不报错**，记为 SKIPPED 并说明原因，避免影响主流程
- 发送失败按 retry_pending() 由后台任务重试，超过 MAX_RETRY 放弃
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.system import NotifyLog, SysUser

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


class NotifyService:
    """通知投递：把 notify_log 里的待发送记录真正发出去"""

    def __init__(self, db: Session):
        self.db = db

    # ============================================================ 入口
    def deliver(self, log_id: int) -> str:
        """投递单条通知，返回最终状态

        站内消息无需外部通道，入队即视为成功；
        邮件/短信走实际发送，失败时把原因写回 error 便于重试。
        """
        row = self.db.get(NotifyLog, log_id)
        if row is None:
            return STATUS_FAILED

        if row.channel == "INNER":
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

    def retry_pending(self, limit: int = 100) -> dict[str, int]:
        """重投待发送的邮件/短信

        只处理 error 为「待发送」或以「发送失败」开头且未超重试次数的记录。
        """
        stmt = (
            select(NotifyLog)
            .where(
                NotifyLog.channel.in_(("EMAIL", "SMS")),
                NotifyLog.status != STATUS_SUCCESS,
            )
            .order_by(NotifyLog.id)
            .limit(limit)
        )
        rows = list(self.db.scalars(stmt))
        stats = {"success": 0, "failed": 0, "skipped": 0}
        for row in rows:
            attempts = self._attempts(row.error)
            if attempts >= MAX_RETRY:
                stats["skipped"] += 1
                continue
            result = self.deliver(row.id)
            if result == STATUS_SUCCESS:
                stats["success"] += 1
            elif result == STATUS_SKIPPED:
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
        return stats

    # ============================================================ 邮件
    def _send_email(self, row: NotifyLog) -> str:
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

        if not settings.sms_enabled or not settings.sms_provider:
            row.status = STATUS_SKIPPED
            row.error = "未启用短信通道（PM_SMS_ENABLED / PM_SMS_PROVIDER）"
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
        """组装邮件标题与正文"""
        name = user.name if user else "老师"
        if row.biz_type == "ALERT":
            return self._compose_alert(row, name)
        if row.biz_type == "DIGEST":
            return self._compose_digest(row, name)
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

    def _compose_digest(self, row: NotifyLog, name: str) -> tuple[str, str]:
        """每日摘要正文：从 error 字段携带的 JSON 里取统计（见 scheduler）"""
        import json

        stats: dict[str, Any] = {}
        if row.error:
            try:
                stats = json.loads(row.error)
            except (ValueError, TypeError):
                stats = {}
        title = f"【{settings.app_name}】每日预警摘要"
        lines = [
            f"{name}，您好：",
            "",
            "以下为今日预警汇总：",
            "",
            f"  昨日新增：{stats.get('new_yesterday', 0)} 条",
            f"  当前待处置：{stats.get('pending', 0)} 条",
            f"  其中已超时：{stats.get('overdue', 0)} 条",
            f"  红色预警：{stats.get('red', 0)} 条",
        ]
        lines += ["", "请登录系统处理：站内「预警中心」。", "", f"—— {settings.app_name}"]
        return title, "\n".join(lines)

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
