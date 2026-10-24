"""闭环缺口修复：消息中心、任务运行记录、代填、更正放行、结项异议、权限补齐

Revision ID: 0008_closure_fixes
Revises: 0007_report_grace
Create Date: 2026-10-24

## 背景

`doc/闭环缺口汇总-20261024.md` 与四份审计报告列出 11 个 P0 / 23 个 P1，
本迁移只做「结构」部分，业务逻辑在代码里同步修改。

## 本迁移做什么

1. sys_message：站内消息收件箱（早期 INNER 只写 notify_log 就记 SUCCESS，
   收件人根本读不到；notify_log 没有标题/正文/已读）
2. sys_job_run：定时任务运行记录（失败只有 logger.exception，管理员不可见）
3. sys_user.failed_login_count / locked_until：登录限流落库
   （原来是进程内字典，多 worker 阈值翻倍、重启清零、无解锁入口）
4. report.on_behalf_of：报表代填记录（及时率要算在被代填人头上）
5. report_grace.kind：区分补填放行(BACKFILL)与更正放行(CORRECT)
6. proj_major.daily_report_enabled：日报按项目开关（需求 6.1 第 5 项）
7. proj_major.close_objection_*：结项异议退回（需求 6.3 第 27 项）
8. prog_record.event_type：区分 FILL/URGE/REPORT，避免催办污染「上次说明」
9. alert.detail：落规则算出的触发详情（早期 candidate.detail 从不落库）
10. 权限点补齐：report:grant（全新库缺失）、project:sub:close（新增）
11. 参数补齐：AL-04/06/09/10 开关、report.backfill_days、
    report.close_*、notify.orange.email
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_closure_fixes"
down_revision = "0007_report_grace"
branch_labels = None
depends_on = None


def _pk_type(bind):
    return (
        sa.BigInteger().with_variant(sa.Integer, "sqlite")
        if bind.dialect.name == "sqlite"
        else sa.BigInteger()
    )


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    pk = _pk_type(bind)

    # ---------------------------------------------------------------- 1) 消息中心
    if "sys_message" not in tables:
        op.create_table(
            "sys_message",
            sa.Column("id", pk, primary_key=True, autoincrement=True),
            sa.Column("receiver_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
            sa.Column("biz_type", sa.String(length=32), nullable=False),
            sa.Column("biz_id", sa.BigInteger(), nullable=True),
            sa.Column("level", sa.String(length=16), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.String(length=2000), nullable=False, server_default=""),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
        )
        op.create_index("idx_message_receiver", "sys_message", ["receiver_id", "read_at"])
        op.create_index("idx_message_biz", "sys_message", ["biz_type", "biz_id"])
        op.create_index("idx_message_created", "sys_message", ["created_at"])

    # ---------------------------------------------------------------- 2) 任务运行记录
    if "sys_job_run" not in tables:
        op.create_table(
            "sys_job_run",
            sa.Column("id", pk, primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column(
                "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="RUNNING"),
            sa.Column("message", sa.String(length=1000), nullable=True),
        )
        op.create_index("idx_job_run_job", "sys_job_run", ["job_id", "started_at"])
        op.create_index("idx_job_run_status", "sys_job_run", ["status", "started_at"])

    # ---------------------------------------------------------------- 3) 登录限流落库
    if not _has_column(insp, "sys_user", "failed_login_count"):
        op.add_column(
            "sys_user",
            sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column(insp, "sys_user", "locked_until"):
        op.add_column("sys_user", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))

    # ---------------------------------------------------------------- 4) 报表代填
    if not _has_column(insp, "report", "on_behalf_of"):
        op.add_column("report", sa.Column("on_behalf_of", sa.BigInteger(), nullable=True))

    # ---------------------------------------------------------------- 5) 更正放行
    if not _has_column(insp, "report_grace", "kind"):
        op.add_column(
            "report_grace",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="BACKFILL"),
        )
        op.create_index("ix_report_grace_kind", "report_grace", ["kind", "expires_on"])

    # ---------------------------------------------------------------- 6/7) 大项目
    if not _has_column(insp, "proj_major", "daily_report_enabled"):
        op.add_column("proj_major", sa.Column("daily_report_enabled", sa.Boolean(), nullable=True))
    if not _has_column(insp, "proj_major", "close_objection_at"):
        op.add_column(
            "proj_major", sa.Column("close_objection_at", sa.DateTime(timezone=True), nullable=True)
        )
    if not _has_column(insp, "proj_major", "close_objection_by"):
        op.add_column("proj_major", sa.Column("close_objection_by", sa.BigInteger(), nullable=True))
    if not _has_column(insp, "proj_major", "close_objection_reason"):
        op.add_column(
            "proj_major", sa.Column("close_objection_reason", sa.String(length=500), nullable=True)
        )

    # ---------------------------------------------------------------- 8) 留痕事件类型
    if not _has_column(insp, "prog_record", "event_type"):
        op.add_column(
            "prog_record",
            sa.Column("event_type", sa.String(length=16), nullable=False, server_default="FILL"),
        )

    # ---------------------------------------------------------------- 9) 预警详情
    if not _has_column(insp, "alert", "detail"):
        # PG 用 JSONB，SQLite 用 JSON（与 app.db.types.JSON_TYPE 一致）
        json_type = (
            sa.JSON()
            if bind.dialect.name == "sqlite"
            else sa.dialects.postgresql.JSONB()
        )
        op.add_column("alert", sa.Column("detail", json_type, nullable=True))

    # ---------------------------------------------------------------- 10) 权限点
    conn = op.get_bind()
    from app.core.permissions import ROLE_PERMISSIONS, permission_rows

    existing = {
        row[0] for row in conn.execute(sa.text("SELECT code FROM sys_permission")).fetchall()
    }
    max_sort = conn.execute(
        sa.text("SELECT COALESCE(MAX(sort), 0) FROM sys_permission")
    ).scalar() or 0
    added_perms: dict[str, int] = {}
    for code, name, ptype, sort in permission_rows():
        if code in existing:
            row = conn.execute(
                sa.text("SELECT id FROM sys_permission WHERE code = :c"), {"c": code}
            ).first()
            if row:
                added_perms[code] = row[0]
            continue
        max_sort += 1
        conn.execute(
            sa.text(
                "INSERT INTO sys_permission (code, name, type, sort) "
                "VALUES (:c, :n, :t, :s)"
            ),
            {"c": code, "n": name, "t": ptype, "s": max_sort},
        )
        row = conn.execute(
            sa.text("SELECT id FROM sys_permission WHERE code = :c"), {"c": code}
        ).first()
        if row:
            added_perms[code] = row[0]

    # 按矩阵补授权（只增不减）
    role_rows = conn.execute(sa.text("SELECT id, code FROM sys_role")).fetchall()
    for role_id, role_code in role_rows:
        wanted = ROLE_PERMISSIONS.get(role_code, ())
        if not wanted:
            continue
        have = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT permission_id FROM sys_role_permission WHERE role_id = :r"),
                {"r": role_id},
            ).fetchall()
        }
        for code in wanted:
            pid = added_perms.get(code)
            if pid is not None and pid not in have:
                conn.execute(
                    sa.text(
                        "INSERT INTO sys_role_permission (role_id, permission_id) "
                        "VALUES (:r, :p)"
                    ),
                    {"r": role_id, "p": pid},
                )

    # ---------------------------------------------------------------- 11) 参数补齐
    from app.db.session import _DEFAULT_CONFIGS

    have_keys = {
        row[0] for row in conn.execute(sa.text("SELECT key FROM sys_config")).fetchall()
    }
    for key, value, vtype, remark in _DEFAULT_CONFIGS:
        if key in have_keys:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO sys_config (key, value, value_type, remark) "
                "VALUES (:k, :v, :t, :r)"
            ),
            {"k": key, "v": value, "t": vtype, "r": remark},
        )


def downgrade() -> None:
    # 结构回滚按 SQLite 的能力有限，这里只删除新增表，列保留（与项目历史迁移风格一致）
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for t in ("sys_message", "sys_job_run"):
        if t in tables:
            op.drop_table(t)
