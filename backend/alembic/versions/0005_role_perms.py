"""按角色矩阵补齐权限（修正"填报人没有填报权限"）

Revision ID: 0005_role_perms
Revises: 0004_attachment
Create Date: 2026-10-21

## 背景

0001_init 给 6 个非管理员角色灌的是**同一套 14 项只读权限**：

    viewer_perms = [dashboard, dashboard:view, project, project:major,
                    project:major:view, project:sub, project:sub:view,
                    progress, progress:table, alert, alert:list,
                    export, export:ledger, export:progress]

导致：
- REPORTER（填报人）没有 report:fill —— 角色名叫"填报人"却看不到
  「报表填报」入口，全系统只有 SYS_ADMIN 能填报表
- 所有角色都没有 alert:claim / progress:update / risk:list 等操作权限，
  登录后只能看，什么也做不了

## 本迁移做什么

按 app/core/permissions.py 的 ROLE_PERMISSIONS 逐角色补齐。
只增不减 —— 已授予的权限不动，避免删掉管理员在界面上手工加的授权。

## 权限梯度

    VIEWER          14 项  只读
    REPORTER        19 项  + 报表填报、进度填报
    SUB_OWNER       20 项  + 认领预警
    COLLEGE_OWNER   23 项  + 代填进度、关闭预警
    MAJOR_OWNER     28 项  + 催办、编辑风险、转交预警
    PROJECT_OWNER   30 项  + 作废报表、编辑项目
    SYS_ADMIN       38 项  全部
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_role_perms"
down_revision = "0004_attachment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 权威定义（单一来源，避免多处漂移）
    from app.core.permissions import ROLE_PERMISSIONS

    conn = op.get_bind()

    # 角色 code -> id
    rows = conn.execute(sa.text("SELECT id, code FROM sys_role")).fetchall()
    role_ids = {code: rid for rid, code in rows}
    if not role_ids:
        return

    # 权限 code -> id
    rows = conn.execute(sa.text("SELECT id, code FROM sys_permission")).fetchall()
    perm_ids = {code: pid for pid, code in rows}
    if not perm_ids:
        return

    total_added = 0
    for role_code, perm_codes in ROLE_PERMISSIONS.items():
        rid = role_ids.get(role_code)
        if rid is None:
            continue

        # 已授予的权限（只增不减）
        existing = {
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT permission_id FROM sys_role_permission WHERE role_id = :rid"
                ),
                {"rid": rid},
            ).fetchall()
        }

        for pc in perm_codes:
            pid = perm_ids.get(pc)
            if pid is None or pid in existing:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO sys_role_permission (role_id, permission_id) "
                    "VALUES (:rid, :pid)"
                ),
                {"rid": rid, "pid": pid},
            )
            total_added += 1

    print(f"[0005_role_perms] 已补齐角色权限 {total_added} 条")


def downgrade() -> None:
    """降级：把非管理员角色恢复成 0001 时期的 14 项只读基线

    只删增量，保留基线。管理员权限不动。
    """
    conn = op.get_bind()

    viewer_perms = (
        "dashboard", "dashboard:view",
        "project", "project:major", "project:major:view",
        "project:sub", "project:sub:view",
        "progress", "progress:table",
        "alert", "alert:list",
        "export", "export:ledger", "export:progress",
    )
    non_admin = (
        "PROJECT_OWNER", "MAJOR_OWNER", "COLLEGE_OWNER",
        "SUB_OWNER", "REPORTER", "VIEWER",
    )

    rows = conn.execute(sa.text("SELECT id, code FROM sys_role")).fetchall()
    role_ids = {code: rid for rid, code in rows}
    rows = conn.execute(sa.text("SELECT id, code FROM sys_permission")).fetchall()
    perm_ids = {code: pid for pid, code in rows}

    removed = 0
    for role_code in non_admin:
        rid = role_ids.get(role_code)
        if rid is None:
            continue
        keep = {perm_ids[c] for c in viewer_perms if c in perm_ids}
        granted = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT permission_id FROM sys_role_permission WHERE role_id = :rid"
                ),
                {"rid": rid},
            ).fetchall()
        ]
        for pid in granted:
            if pid not in keep:
                conn.execute(
                    sa.text(
                        "DELETE FROM sys_role_permission "
                        "WHERE role_id = :rid AND permission_id = :pid"
                    ),
                    {"rid": rid, "pid": pid},
                )
                removed += 1

    print(f"[0005_role_perms] 已回收角色权限 {removed} 条")