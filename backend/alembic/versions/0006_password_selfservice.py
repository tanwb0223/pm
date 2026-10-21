"""用户自助改密：新增 must_change_password / password_updated_at

Revision ID: 0006_password_selfservice
Revises: 0005_role_perms
Create Date: 2026-10-21

## 背景

系统此前**没有用户自助修改密码的入口**：

- 只有管理员在「系统管理 → 用户管理 → 编辑」里重置他人密码
- 批量导入用户的初始密码固定为 Init@123456，前端提示
  「请提醒教师首次登录后修改」——但用户登录后根本无处可改

结果是：要么全校共用一个初始密码，要么每次改密都得找管理员，
而管理员还得把新密码明文告诉用户。

## 本迁移做什么

sys_user 增加两列：

- must_change_password  首次登录/被重置后置 1，改密成功后清 0
- password_updated_at   上次改密时间，便于审计「多久没换密码」

存量数据：已有密码的用户一律置 0（不打扰现有账号），
仅导入/新建的用户由业务代码显式置 1。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_password_selfservice"
down_revision = "0005_role_perms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("sys_user")}

    if "must_change_password" not in cols:
        op.add_column(
            "sys_user",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    if "password_updated_at" not in cols:
        op.add_column(
            "sys_user",
            sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("sys_user")}

    if "password_updated_at" in cols:
        op.drop_column("sys_user", "password_updated_at")
    if "must_change_password" in cols:
        op.drop_column("sys_user", "must_change_password")
