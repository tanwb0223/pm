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
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import BIGINT_PK, JSON_TYPE, aware_timestamp


class SysCollege(Base):
    __tablename__ = "sys_college"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    parent_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_college.id"), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
    )


class SysUser(Base):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    staff_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    college_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_college.id"), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 首次登录/管理员重置后置 true：前端强制弹出改密框，改完清 false。
    # 批量导入的初始密码是统一值，不加这个标记等于全校同密码。
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    password_updated_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    last_login_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
    )

    college: Mapped[SysCollege | None] = relationship(lazy="joined")


class SysRole(Base):
    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
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
        Index("idx_user_role_user", "user_id"),
        Index("idx_user_role_scope", "scope_type", "scope_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_role.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SELF")
    scope_id: Mapped[int | None] = mapped_column(BIGINT_PK, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())

    role: Mapped[SysRole] = relationship(lazy="joined")


class SysPermission(Base):
    __tablename__ = "sys_permission"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_permission.id"), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SysRolePermission(Base):
    __tablename__ = "sys_role_permission"

    role_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_role.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_permission.id"), primary_key=True
    )


class SysConfig(Base):
    __tablename__ = "sys_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="STRING")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), onupdate=func.now()
    )


class SysHoliday(Base):
    __tablename__ = "sys_holiday"
    __table_args__ = (Index("idx_holiday_date", "date"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_time", "created_at"),
        Index("idx_audit_op", "operator_id", "created_at"),
        Index("idx_audit_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    operator_id: Mapped[int | None] = mapped_column(BIGINT_PK, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(BIGINT_PK, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = aware_timestamp(server_default=func.now())


class NotifyLog(Base):
    __tablename__ = "notify_log"
    __table_args__ = (
        Index("idx_notify_receiver", "receiver_id", "sent_at"),
        Index("idx_notify_biz", "biz_type", "biz_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False)
    biz_id: Mapped[int | None] = mapped_column(BIGINT_PK, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    receiver_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = aware_timestamp(server_default=func.now())
