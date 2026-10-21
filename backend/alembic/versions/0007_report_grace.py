"""逾期补填放行：新增 report_grace 表与 report:grant 权限

Revision ID: 0007_report_grace
Revises: 0006_password_selfservice
Create Date: 2026-10-21

## 背景

需求 4.3：

> 日报、周报逾期 3 天内可以自己补；**月报超过 3 天，要学院负责人开了才能补**。

早期实现是超期直接拒绝，任何人（包括管理员）都补不了——
注释写着「需学院负责人开启」，但「开启」的入口根本不存在。

## 本迁移做什么

1. 新建 report_grace 表：记录「谁在什么时候、为哪个子项目的哪个周期、
   出于什么理由」开了补填，以及放行有效期
2. 新增权限点 report:grant，授予 COLLEGE_OWNER / MAJOR_OWNER /
   PROJECT_OWNER（学院负责人及以上）

放行有有效期，到期自动失效，避免一次性永久开闸。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_report_grace"
down_revision = "0006_password_selfservice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1) 建表
    if "report_grace" not in insp.get_table_names():
        # SQLite 只认 INTEGER PRIMARY KEY 自增（BIGINT 不行），
        # 与 app.db.types.BIGINT_PK 的做法保持一致。
        pk_type = (
            sa.BigInteger().with_variant(sa.Integer, "sqlite")
            if bind.dialect.name == "sqlite"
            else sa.BigInteger()
        )
        op.create_table(
            "report_grace",
            sa.Column("id", pk_type, primary_key=True, autoincrement=True),
            sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), nullable=False),
            sa.Column("type", sa.String(length=8), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=True),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column(
                "granted_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False
            ),
            sa.Column(
                "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("expires_on", sa.Date(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "revoked_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True
            ),
            sa.CheckConstraint(
                "type IN ('DAILY','WEEKLY','MONTHLY')", name="ck_grace_type"
            ),
        )
        op.create_index("ix_report_grace_sub_type", "report_grace", ["sub_id", "type"])

    # 2) 权限点（sys_permission 的列是 type，不是 kind）
    conn = op.get_bind()
    exists = conn.execute(
        sa.text("SELECT id FROM sys_permission WHERE code = 'report:grant'")
    ).first()
    if exists is None:
        max_id = conn.execute(sa.text("SELECT COALESCE(MAX(id), 0) FROM sys_permission")).scalar()
        max_sort = conn.execute(sa.text("SELECT COALESCE(MAX(sort), 0) FROM sys_permission")).scalar()
        conn.execute(
            sa.text(
                "INSERT INTO sys_permission (id, code, name, type, sort) "
                "VALUES (:id, 'report:grant', '开启逾期补填', 'ACTION', :sort)"
            ),
            {"id": (max_id or 0) + 1, "sort": (max_sort or 0) + 1},
        )
        exists = conn.execute(
            sa.text("SELECT id FROM sys_permission WHERE code = 'report:grant'")
        ).first()

    # 3) 授予学院负责人及以上
    if exists is not None:
        pid = exists[0]
        rows = conn.execute(
            sa.text(
                "SELECT id, code FROM sys_role WHERE code IN "
                "('COLLEGE_OWNER','MAJOR_OWNER','PROJECT_OWNER','SYS_ADMIN')"
            )
        ).fetchall()
        added = 0
        for rid, _code in rows:
            has = conn.execute(
                sa.text(
                    "SELECT 1 FROM sys_role_permission "
                    "WHERE role_id = :rid AND permission_id = :pid"
                ),
                {"rid": rid, "pid": pid},
            ).first()
            if has is None:
                conn.execute(
                    sa.text(
                        "INSERT INTO sys_role_permission (role_id, permission_id) "
                        "VALUES (:rid, :pid)"
                    ),
                    {"rid": rid, "pid": pid},
                )
                added += 1
        print(f"[0007_report_grace] 已授予 report:grant 给 {added} 个角色")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "report_grace" in insp.get_table_names():
        op.drop_table("report_grace")

    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id FROM sys_permission WHERE code = 'report:grant'")
    ).first()
    if row:
        conn.execute(
            sa.text("DELETE FROM sys_role_permission WHERE permission_id = :pid"),
            {"pid": row[0]},
        )
        conn.execute(sa.text("DELETE FROM sys_permission WHERE id = :pid"), {"pid": row[0]})
