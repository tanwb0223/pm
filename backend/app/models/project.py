"""大项目、学院分工、子项目、进度留痕、每日快照"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import BIGINT_PK, JSON_TYPE, aware_timestamp


class ProjMajor(Base):
    """大项目（学校立项的产教融合项目）"""

    __tablename__ = "proj_major"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','CLOSED','ABORTED')", name="ck_major_status"),
        CheckConstraint(
            "acceptance_status IN ('NOT_REQUIRED','PENDING','PASSED','RECTIFYING','REJECTED')",
            name="ck_major_acceptance",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    partner_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    build_period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")

    # 第 38 项：原始计划（立项时锁定）
    original_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    # 第 32/33 项预留：验收
    acceptance_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_REQUIRED")
    acceptance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acceptance_org: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 第 34 项预留：经费
    budget_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    budget_used: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # 第 42 项预留：后评价
    evaluation_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    evaluation_note: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)

    # 完整性检查结果缓存
    incomplete_reason: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)

    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = aware_timestamp(nullable=True)

    owner: Mapped["SysUser | None"] = relationship(foreign_keys=[owner_id], lazy="joined")


class ProjMajorCollege(Base):
    """大项目 ↔ 参与学院（多对多）"""

    __tablename__ = "proj_major_college"

    major_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("proj_major.id"), primary_key=True
    )
    college_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_college.id"), primary_key=True
    )
    college_owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())


class ProjSub(Base):
    """★ 子项目：进度与预警的最小单元"""

    __tablename__ = "proj_sub"
    __table_args__ = (
        CheckConstraint(
            "status IN ('NOT_STARTED','IN_PROGRESS','ABORTED','CLOSED')", name="ck_sub_status"
        ),
        CheckConstraint("current_progress BETWEEN 0 AND 100", name="ck_sub_progress"),
        CheckConstraint(
            "acceptance_status IN ('NOT_REQUIRED','PENDING','PASSED','RECTIFYING','REJECTED')",
            name="ck_sub_acceptance",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    major_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("proj_major.id"), nullable=False)
    college_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_college.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=True)
    plan_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_STARTED")
    current_progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_update_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    closed_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    acceptance_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOT_REQUIRED")

    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = aware_timestamp(nullable=True)

    major: Mapped[ProjMajor] = relationship(lazy="joined")
    owner: Mapped["SysUser | None"] = relationship(foreign_keys=[owner_id], lazy="joined")


class ProgRecord(Base):
    """★ 进度留痕（只增不改）"""

    __tablename__ = "prog_record"
    __table_args__ = (CheckConstraint("progress BETWEEN 0 AND 100", name="ck_record_progress"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    sub_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("proj_sub.id"), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_no_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollback_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    operator_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=False)
    on_behalf_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())


class ProgSnapshot(Base):
    """★ 每日快照：让"连续 N 天"判定变成纯 SQL"""

    __tablename__ = "prog_snapshot"

    sub_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("proj_sub.id"), primary_key=True
    )
    snap_date: Mapped[date] = mapped_column(Date, primary_key=True)
    time_progress: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_progress: Mapped[int] = mapped_column(Integer, nullable=False)
    deviation: Mapped[int] = mapped_column(Integer, nullable=False)
    days_since_update: Mapped[int] = mapped_column(Integer, nullable=False)
    is_free_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# 延迟导入避免循环
from app.models.system import SysUser  # noqa: E402
