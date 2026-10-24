"""项目变更留痕：计划时间 / 权重 / 负责人 / 状态的改前改后值与原因

背景（需求 6.3 第 20 项「项目变更需留痕」）：
    早期 `update_major` / `update_sub` 直接 `setattr`，审计日志只记「发生过
    一次 PUT」，查不到「谁在什么时候把计划结束时间从 A 改成 B、为什么改」。
    项目台账是考核与审计的依据，计划/权重/负责人这些关键字段被悄悄改掉
    会让所有进度偏差与汇总口径失真，因此单独建表记录字段级变更。

与 audit_log 的分工：
    audit_log 记「请求级」操作（谁调了哪个接口），本表记「字段级」变更
    （具体改了什么、改成什么、原因），两者互补，前端在项目详情页展示。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import BIGINT_PK, aware_timestamp


class ProjChangeLog(Base):
    """项目关键字段变更流水（只增不改）"""

    __tablename__ = "proj_change_log"
    __table_args__ = (
        # 按对象查变更历史是最高频查询（详情页抽屉）
        Index("idx_change_target", "target_type", "target_id", "created_at"),
        # 按操作人查「谁改过」用于追责
        Index("idx_change_operator", "operator_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # MAJOR=大项目 / SUB=子项目（后续若扩展到其他对象，复用同一张表）
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 变更字段名（与模型字段同名，前端可映射为中文标签）
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    # 改前/改后值统一转字符串存储：字段类型不同（日期/Decimal/状态）也能落一张表
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 变更原因（body 里的可选 change_reason；前端补输入框由前端组负责）
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
