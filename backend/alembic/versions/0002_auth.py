"""给认证配置建表（迁移 0002）"""
from __future__ import annotations

from alembic import op

from alembic_helpers import (
    cmt,
    col_cmt,
    json_column,
    now_default,
    pk_column,
    upsert_text,
)
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_auth"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_provider",
        pk_column(),
        sa.Column("kind", sa.String(32), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("display_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("issuer", sa.String(255), nullable=False, server_default=""),
        sa.Column("client_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("client_secret_enc", sa.Text(), nullable=True),
        sa.Column("scopes", sa.String(255), nullable=False, server_default=""),
        sa.Column("server_url", sa.String(255), nullable=False, server_default=""),
        json_column("extra", nullable=False, server_default=sa.text("'{}'")),
        sa.Column("auto_create_user", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("default_role_id", sa.BigInteger(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    )
    cmt("auth_provider", "认证提供方配置：一种协议一行，可同时启用多个")

    op.create_table(
        "auth_login_state",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("provider_id", sa.BigInteger(), sa.ForeignKey("auth_provider.id"), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False, server_default=""),
        sa.Column("code_verifier", sa.String(128), nullable=False, server_default=""),
        sa.Column("redirect_after", sa.String(500), nullable=False, server_default="/"),
        sa.Column("login_code", sa.String(64), nullable=True, unique=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("bind_user_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    )
    op.create_index("idx_login_state_expires", "auth_login_state", ["expires_at"])
    op.create_index("idx_login_state_code", "auth_login_state", ["login_code"])
    cmt("auth_login_state", "登录流程态：state+nonce+PKCE+一次性登录码")

    op.create_table(
        "auth_identity",
        pk_column(),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), sa.ForeignKey("auth_provider.id"), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        json_column("claims", nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.UniqueConstraint("provider_id", "subject", name="uq_identity_provider_subject"),
    )
    op.create_index("idx_identity_user", "auth_identity", ["user_id"])
    cmt("auth_identity", "第三方身份绑定：一个用户可绑定多个第三方账号")

    # 初始化 9 种认证方式，默认只启用本地密码
    kinds = [
        ("password", "账号密码", True, 0),
        ("oidc", "OIDC 单点登录", False, 1),
        ("oauth2", "OAuth2 登录", False, 2),
        ("cas", "CAS 单点登录", False, 3),
        ("saml2", "SAML 2.0 单点登录", False, 4),
        ("ldap", "LDAP / AD 域", False, 5),
        ("feishu", "飞书", False, 6),
        ("dingtalk", "钉钉", False, 7),
        ("wecom", "企业微信", False, 8),
    ]
    for kind, name, enabled, sort in kinds:
        op.execute(
            upsert_text("INSERT INTO auth_provider (kind, display_name, enabled, sort) "
                "VALUES (:k, :n, :e, :s) ON CONFLICT (kind) DO NOTHING"
            ).bindparams(k=kind, n=name, e=enabled, s=sort)
        )


def downgrade() -> None:
    op.drop_table("auth_identity")
    op.drop_table("auth_login_state")
    op.drop_table("auth_provider")
