"""报表模型（日/周/月报合一张表，content 存各自特有字段）"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import BIGINT_PK, JSON_TYPE, aware_timestamp


class Report(Base):
    __tablename__ = "report"
    __table_args__ = (
        CheckConstraint("type IN ('DAILY','WEEKLY','MONTHLY')", name="ck_report_type"),
        CheckConstraint("status IN ('DRAFT','SUBMITTED','VOIDED')", name="ck_report_status"),
        UniqueConstraint("sub_id", "type", "period_start", name="uq_report_period"),
        Index("idx_report_period", "type", "period_start"),
        Index("idx_report_sub", "sub_id", "type", "period_start"),
        Index("idx_report_status", "status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    sub_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("proj_sub.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    submitted_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    submitted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    voided_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    voided_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())

    sub: Mapped["ProjSub"] = relationship(lazy="joined")


class ReportGrace(Base):
    """逾期补填放行（文档 4.3：月报超 3 天需学院负责人开了才能补）

    早期实现超期直接拒绝，任何人都补不了——注释写着「需学院负责人开启」，
    但没有任何「开启」的入口。本表记录放行：

    - 一条放行对应「子项目 + 报表类型 + 周期起始日」
    - period_start 为空表示放行该子项目该类型的**所有**历史周期
    - 放行有截止日期，过期自动失效，避免一次性永久开闸
    """

    __tablename__ = "report_grace"
    __table_args__ = (
        CheckConstraint("type IN ('DAILY','WEEKLY','MONTHLY')", name="ck_grace_type"),
        Index("ix_report_grace_sub_type", "sub_id", "type"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    sub_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("proj_sub.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    # 为空 = 该类型下所有历史周期都可补
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_by: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=False)
    granted_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    # 放行有效期截止日（含当天），过期自动失效
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    revoked_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )


from app.models.project import ProjSub  # noqa: E402
