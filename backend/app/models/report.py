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
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Report(Base):
    __tablename__ = "report"
    __table_args__ = (
        CheckConstraint("type IN ('DAILY','WEEKLY','MONTHLY')", name="ck_report_type"),
        CheckConstraint("status IN ('DRAFT','SUBMITTED','VOIDED')", name="ck_report_status"),
        UniqueConstraint("sub_id", "type", "period_start", name="uq_report_period"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sub_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("proj_sub.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    voided_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sub: Mapped["ProjSub"] = relationship(lazy="joined")


from app.models.project import ProjSub  # noqa: E402
