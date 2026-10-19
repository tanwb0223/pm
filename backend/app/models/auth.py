"""认证配置与登录态模型（对齐 Octop 的 sso_providers / sso_login_states）"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AuthProvider(Base):
    """认证提供方配置：一种协议一行，可同时启用多个"""

    __tablename__ = "auth_provider"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # OIDC / OAuth2 / SAML 用
    issuer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # CAS / SAML 用
    server_url: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 其他协议特有配置（端点、字段映射、corp_id 等）
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 是否自动创建用户（首次登录）
    auto_create_user: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # 默认角色（自动创建用户时分配）
    default_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuthLoginState(Base):
    """登录流程态：state + nonce + PKCE verifier + 一次性登录码"""

    __tablename__ = "auth_login_state"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("auth_provider.id"), nullable=False
    )
    nonce: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    redirect_after: Mapped[str] = mapped_column(String(500), nullable=False, default="/")
    login_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 绑定场景：已登录用户绑定第三方账号
    bind_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthIdentity(Base):
    """第三方身份绑定：一个用户可绑定多个第三方账号"""

    __tablename__ = "auth_identity"
    __table_args__ = (
        UniqueConstraint("provider_id", "subject", name="uq_identity_provider_subject"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("auth_provider.id"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # 最后一次登录时第三方返回的声明
    claims: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
