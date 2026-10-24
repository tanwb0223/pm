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
    # 代填：记录「替谁交的」。及时率按 on_behalf_of or submitted_by 归属，
    # 否则学院负责人代填的报表会算到负责人头上（需求 6.2 第 12 项）。
    on_behalf_of: Mapped[int | None] = mapped_column(
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
    """补填 / 更正放行（文档 4.3 与 3.4）

    早期实现把「补填期」和「更正期」混在同一个参数 report.correct_days 里，
    且超期更正用 project:sub:edit 冒充「学院负责人开权限」——学院负责人
    恰恰没有这个权限点，导致超期更正对所有人都是死路。

    本表用 kind 区分两种放行：
    - BACKFILL：逾期补填（原本就有的能力）
    - CORRECT ：已提交报表的更正（新增，需求 3.4）
    """

    __tablename__ = "report_grace"
    __table_args__ = (
        CheckConstraint("type IN ('DAILY','WEEKLY','MONTHLY')", name="ck_grace_type"),
        CheckConstraint("kind IN ('BACKFILL','CORRECT')", name="ck_grace_kind"),
        Index("ix_report_grace_sub_type", "sub_id", "type"),
        Index("ix_report_grace_kind", "kind", "expires_on"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    sub_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("proj_sub.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    # BACKFILL=补填放行，CORRECT=更正放行
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="BACKFILL")
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
