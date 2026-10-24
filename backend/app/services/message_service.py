"""站内消息服务：用户收件箱的写入与读取

## 为什么新增（对应 .workbuddy/audit/backend-system.md P0-2）

早期 INNER 通道只往 `notify_log` 写一行状态就记 SUCCESS，而 `notify_log`
是「投递日志」（管理员运维视角），没有标题/正文/已读状态，被通知人
根本读不到。预警、催办、每日摘要三条链路因此全部「发而不达」。

本服务补上用户视角的那一半：

- `push()`：写入一条收件箱消息（NotifyService 的 INNER 通道与每日摘要复用）
- `list_mine()` / `unread_count()` / `mark_read()` / `read_all()`：
  「我的消息」接口，只能读/改自己的消息（越权 403）

约定：`biz_type` 指回业务对象，前端据此跳转
（ALERT→预警中心，URGE→进度总表，DIGEST→预警统计）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.system import SysMessage


class MessageService:
    def __init__(self, db: Session):
        self.db = db

    # ============================================================ 写入
    def push(
        self,
        receiver_id: int,
        biz_type: str,
        biz_id: int | None = None,
        *,
        title: str,
        body: str = "",
        level: str | None = None,
    ) -> SysMessage:
        """写入一条站内消息（收件箱）

        标题/正文由调用方组装：预警/催办复用 NotifyService._compose 的结果，
        每日摘要由 scheduler 直接构造。写库即视为「已投递到收件箱」，
        是否已读由 read_at 控制。
        """
        msg = SysMessage(
            receiver_id=receiver_id,
            biz_type=biz_type,
            biz_id=biz_id,
            level=level,
            # 列宽有限，超长截断而不是让整条通知写失败
            title=(title or "")[:255],
            body=(body or "")[:2000],
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    # ============================================================ 查询
    def list_mine(
        self,
        user: Any,
        *,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SysMessage], int]:
        """本人收件箱（分页，按时间倒序）"""
        stmt = select(SysMessage).where(SysMessage.receiver_id == user.id)
        if unread_only:
            stmt = stmt.where(SysMessage.read_at.is_(None))

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.db.scalars(
                stmt.order_by(SysMessage.created_at.desc(), SysMessage.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def unread_count(self, user: Any) -> int:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(SysMessage)
                .where(
                    SysMessage.receiver_id == user.id,
                    SysMessage.read_at.is_(None),
                )
            )
            or 0
        )

    # ============================================================ 已读
    def mark_read(self, user: Any, message_id: int) -> SysMessage:
        """标记单条已读：只能操作自己的消息，越权 403"""
        msg = self.db.get(SysMessage, message_id)
        if msg is None:
            raise NotFoundError("消息不存在")
        if msg.receiver_id != user.id:
            # 明确区分「不存在」与「不是你的」：越权访问必须 403，
            # 不能静默返回成功，否则等于允许遍历他人收件箱。
            raise ForbiddenError("无权操作他人消息")
        if msg.read_at is None:
            msg.read_at = datetime.now(UTC)
            self.db.flush()
        return msg

    def read_all(self, user: Any) -> int:
        """全部标记已读，返回本次更新条数"""
        result = self.db.execute(
            update(SysMessage)
            .where(
                SysMessage.receiver_id == user.id,
                SysMessage.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        self.db.flush()
        return int(result.rowcount or 0)
