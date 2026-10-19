"""组织、用户、角色、权限模型"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class SysCollege(Base):
    __tablename__ = "sys_college"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_college.id"), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SysUser(Base):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    staff_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    college_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_college.id"), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    college: Mapped[SysCollege | None] = relationship(lazy="joined")


class SysRole(Base):
    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SysUserRole(Base):
    """★ 授权：一人多角色 + 不同项目不同身份"""

    __tablename__ = "sys_user_role"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('ALL','MAJOR','COLLEGE','SELF','CUSTOM')",
            name="ck_user_role_scope",
        ),
        UniqueConstraint("user_id", "role_id", "scope_type", "scope_id", name="uq_user_role_scope"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SELF")
    scope_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role: Mapped[SysRole] = relationship(lazy="joined")


class SysPermission(Base):
    __tablename__ = "sys_permission"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_permission.id"), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SysRolePermission(Base):
    __tablename__ = "sys_role_permission"

    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_permission.id"), primary_key=True
    )


class SysConfig(Base):
    __tablename__ = "sys_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="STRING")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SysHoliday(Base):
    __tablename__ = "sys_holiday"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotifyLog(Base):
    __tablename__ = "notify_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False)
    biz_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    receiver_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
