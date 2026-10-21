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
from app.db.types import BIGINT_PK, JSON_TYPE, aware_timestamp


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

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(8), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    sub_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("proj_sub.id"), nullable=True)
    major_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proj_major.id"), nullable=True
    )
    college_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_college.id"), nullable=True
    )
    trigger_at: Mapped[datetime] = aware_timestamp(nullable=False)
    deadline_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    claimed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    closed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    escalated_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())

    sub: Mapped["ProjSub | None"] = relationship(lazy="joined")


class AlertAction(Base):
    """预警处置流水"""

    __tablename__ = "alert_action"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CLAIM','TRANSFER','CLOSE','IGNORE','ESCALATE')", name="ck_action_type"
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("alert.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    # 系统自动动作（自动关闭、超时升级）没有操作人，必须允许为空。
    # 与 alembic 0003_alert_nullable 保持一致；SQLite 走 create_all 不执行迁移，
    # 早期库靠 app/db/alembic_sync.repair_sqlite_schema() 兜底修复。
    operator_id: Mapped[int | None] = mapped_column(
        BIGINT_PK, ForeignKey("sys_user.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())


class Risk(Base):
    """风险登记"""

    __tablename__ = "risk"
    __table_args__ = (
        CheckConstraint("level IN ('HIGH','MEDIUM','LOW')", name="ck_risk_level"),
        CheckConstraint("status IN ('OPEN','CLOSED')", name="ck_risk_status"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    major_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("proj_major.id"), nullable=False)
    sub_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("proj_sub.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="MEDIUM")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=True)
    plan_close_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
    )


from app.models.project import ProjSub  # noqa: E402
