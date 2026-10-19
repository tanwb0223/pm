"""预警与风险模型"""
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
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Alert(Base):
    """预警主表（11 类规则）"""

    __tablename__ = "alert"
    __table_args__ = (
        CheckConstraint("level IN ('RED','ORANGE','YELLOW')", name="ck_alert_level"),
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','CLOSED','IGNORED')", name="ck_alert_status"
        ),
        CheckConstraint(
            "alert_type IN ('AL-01','AL-02','AL-03','AL-04','AL-05','AL-06',"
            "'AL-07','AL-08','AL-09','AL-10','AL-11')",
            name="ck_alert_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(8), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    sub_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("proj_sub.id"), nullable=True)
    major_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proj_major.id"), nullable=True
    )
    college_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_college.id"), nullable=True
    )
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sub: Mapped["ProjSub | None"] = relationship(lazy="joined")


class AlertAction(Base):
    """预警处置流水"""

    __tablename__ = "alert_action"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CLAIM','TRANSFER','CLOSE','IGNORE','ESCALATE')", name="ck_action_type"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    alert_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("alert.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    operator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Risk(Base):
    """风险登记"""

    __tablename__ = "risk"
    __table_args__ = (
        CheckConstraint("level IN ('HIGH','MEDIUM','LOW')", name="ck_risk_level"),
        CheckConstraint("status IN ('OPEN','CLOSED')", name="ck_risk_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    major_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("proj_major.id"), nullable=False)
    sub_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("proj_sub.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="MEDIUM")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=True)
    plan_close_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


from app.models.project import ProjSub  # noqa: E402
