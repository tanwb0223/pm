"""init schema

Revision ID: 0001_init
Revises:
Create Date: 2026-10-19

产教融合项目管理系统 —— 初始建表
共 20 张业务表 + 3 张预留表（验收/整改/成效），含索引、注释、初始数据。

注意：
- 枚举一律用 varchar + CHECK，不用 PG 原生 enum（改值成本高）
- 时间用 timestamptz（UTC），日期用 date
- 一期"只建不用"的预留字段已包含，默认值 NOT_REQUIRED / NULL
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic_helpers import (
    cmt as _h_cmt,
    col_cmt as _h_col_cmt,
    gin_index,
    is_postgres,
    json_column,
    pk_column,
    create_extensions,
    now_default,
    upsert_text,
)

# revision identifiers
revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------- 工具函数
def _timestamps():
    """通用审计时间字段"""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    ]


def cmt(table: str, text: str):
    """表注释（SQLite 上自动跳过）"""
    _h_cmt(table, text)


def col_cmt(table: str, column: str, text: str):
    """列注释（SQLite 上自动跳过）"""
    _h_col_cmt(table, column, text)


# ---------------------------------------------------------------- upgrade
def upgrade() -> None:
    # ============================================================ 组织与权限
    op.create_table(
        "sys_college",
        pk_column(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("code", sa.String(32), nullable=True, unique=True),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("sys_college.id"), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )
    cmt("sys_college", "二级学院（组织）")
    col_cmt("sys_college", "parent_id", "上级组织，NULL 表示学校本级")

    op.create_table(
        "sys_user",
        pk_column(),
        sa.Column("staff_no", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("college_id", sa.BigInteger(), sa.ForeignKey("sys_college.id"), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(128), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('ACTIVE','DISABLED')", name="ck_user_status"),
    )
    cmt("sys_user", "教职工账号")
    col_cmt("sys_user", "staff_no", "工号，登录名")
    col_cmt("sys_user", "password_hash", "Argon2id 哈希；统一身份认证接入后可为空")

    op.create_table(
        "sys_role",
        pk_column(),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remark", sa.String(255), nullable=True),
        *_timestamps(),
    )
    cmt("sys_role", "角色（7 个预置 + 最多 3 个自定义）")

    op.create_table(
        "sys_user_role",
        pk_column(),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("sys_role.id"), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.BigInteger(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('ALL','MAJOR','COLLEGE','SELF','CUSTOM')",
            name="ck_user_role_scope",
        ),
        sa.UniqueConstraint("user_id", "role_id", "scope_type", "scope_id", name="uq_user_role_scope"),
    )
    op.create_index("idx_user_role_user", "sys_user_role", ["user_id"])
    op.create_index("idx_user_role_scope", "sys_user_role", ["scope_type", "scope_id"])
    cmt("sys_user_role", "★ 授权：一人多角色 + 不同项目不同身份（scope 模型）")
    col_cmt("sys_user_role", "scope_type", "ALL 全校 / MAJOR 所辖大项目 / COLLEGE 本学院 / SELF 本人 / CUSTOM 自定义")
    col_cmt("sys_user_role", "scope_id", "MAJOR→大项目id；COLLEGE→学院id；其余为 NULL")

    op.create_table(
        "sys_permission",
        pk_column(),
        sa.Column("code", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("sys_permission.id"), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("type IN ('MODULE','PAGE','ACTION')", name="ck_permission_type"),
    )
    cmt("sys_permission", "权限点（模块—页面—操作 三级）")

    op.create_table(
        "sys_role_permission",
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("sys_role.id"), primary_key=True),
        sa.Column("permission_id", sa.BigInteger(), sa.ForeignKey("sys_permission.id"), primary_key=True),
    )
    cmt("sys_role_permission", "角色—权限关联")

    # ============================================================ 项目与进度
    op.create_table(
        "proj_major",
        pk_column(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("partner_company", sa.String(255), nullable=True),
        sa.Column("build_period", sa.String(64), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        # 第 38 项：原始计划（立项时锁定）
        sa.Column("original_start", sa.Date(), nullable=True),
        sa.Column("original_end", sa.Date(), nullable=True),
        # 第 32/33 项预留：验收
        sa.Column("acceptance_status", sa.String(16), nullable=False, server_default="NOT_REQUIRED"),
        sa.Column("acceptance_date", sa.Date(), nullable=True),
        sa.Column("acceptance_org", sa.String(128), nullable=True),
        # 第 34 项预留：经费
        sa.Column("budget_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("budget_used", sa.Numeric(14, 2), nullable=True),
        # 第 42 项预留：后评价
        sa.Column("evaluation_score", sa.Numeric(5, 2), nullable=True),
        json_column("evaluation_note", nullable=True),
        # 完整性检查结果缓存
        json_column("incomplete_reason", nullable=True),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('ACTIVE','CLOSED','ABORTED')", name="ck_major_status"),
        sa.CheckConstraint(
            "acceptance_status IN ('NOT_REQUIRED','PENDING','PASSED','RECTIFYING','REJECTED')",
            name="ck_major_acceptance",
        ),
    )
    op.create_index("idx_major_owner", "proj_major", ["owner_id"])
    op.create_index("idx_major_status", "proj_major", ["status"])
    op.create_index("idx_major_deleted", "proj_major", ["deleted_at"])
    cmt("proj_major", "大项目（学校立项的产教融合项目）")
    col_cmt("proj_major", "type", "产业学院/实训基地/订单班/课程教材/双师基地")
    col_cmt("proj_major", "original_start", "第38项：原始计划开始日，立项时锁定，不随变更改动")
    col_cmt("proj_major", "acceptance_status", "第32/33项预留：一期恒为 NOT_REQUIRED，二期启用")
    col_cmt("proj_major", "budget_total", "第34项预留：预算总额")
    col_cmt("proj_major", "evaluation_score", "第42项预留：后评价得分")
    col_cmt("proj_major", "incomplete_reason", "责任与计划完整性检查结果缓存")

    op.create_table(
        "proj_major_college",
        sa.Column("major_id", sa.BigInteger(), sa.ForeignKey("proj_major.id"), primary_key=True),
        sa.Column("college_id", sa.BigInteger(), sa.ForeignKey("sys_college.id"), primary_key=True),
        sa.Column("college_owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    )
    op.create_index("idx_major_college_college", "proj_major_college", ["college_id"])
    cmt("proj_major_college", "大项目 ↔ 参与学院（多对多）")

    op.create_table(
        "proj_sub",
        pk_column(),
        sa.Column("major_id", sa.BigInteger(), sa.ForeignKey("proj_major.id"), nullable=False),
        sa.Column("college_id", sa.BigInteger(), sa.ForeignKey("sys_college.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("plan_start", sa.Date(), nullable=True),
        sa.Column("plan_end", sa.Date(), nullable=True),
        # 第 38 项：子项目级原始计划
        sa.Column("original_start", sa.Date(), nullable=True),
        sa.Column("original_end", sa.Date(), nullable=True),
        sa.Column("weight", sa.Numeric(8, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="NOT_STARTED"),
        sa.Column("current_progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_update_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        # 第 32/33 项预留
        sa.Column("acceptance_status", sa.String(16), nullable=False, server_default="NOT_REQUIRED"),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED','IN_PROGRESS','ABORTED','CLOSED')", name="ck_sub_status"
        ),
        sa.CheckConstraint("current_progress BETWEEN 0 AND 100", name="ck_sub_progress"),
        sa.CheckConstraint(
            "acceptance_status IN ('NOT_REQUIRED','PENDING','PASSED','RECTIFYING','REJECTED')",
            name="ck_sub_acceptance",
        ),
    )
    op.create_index("idx_sub_major", "proj_sub", ["major_id"])
    op.create_index("idx_sub_college", "proj_sub", ["college_id"])
    op.create_index("idx_sub_owner", "proj_sub", ["owner_id"])
    op.create_index("idx_sub_status", "proj_sub", ["status"])
    op.create_index("idx_sub_plan", "proj_sub", ["plan_start", "plan_end"])
    op.create_index("idx_sub_last_update", "proj_sub", ["last_update_at"])
    cmt("proj_sub", "★ 子项目：进度填报与预警的最小单元；major_id 与 college_id 均非空")
    col_cmt("proj_sub", "weight", "权重，可空；空则汇总时按等权")
    col_cmt("proj_sub", "last_update_at", "最后一次进度更新时间（含'确认无变化'）")

    op.create_table(
        "prog_record",
        pk_column(),
        sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("is_no_change", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_rollback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rollback_reason", sa.String(500), nullable=True),
        sa.Column("operator_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("on_behalf_of", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_record_progress"),
    )
    op.create_index("idx_record_sub_time", "prog_record", ["sub_id", "created_at"])
    op.create_index("idx_record_time", "prog_record", ["created_at"])
    cmt("prog_record", "★ 进度留痕（只增不改）")
    col_cmt("prog_record", "is_no_change", "'本周期无变化'，与'没更新'区分记录")
    col_cmt("prog_record", "on_behalf_of", "代填：被代填人 id；为空表示本人填报")

    op.create_table(
        "prog_snapshot",
        sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), primary_key=True),
        sa.Column("snap_date", sa.Date(), primary_key=True),
        sa.Column("time_progress", sa.Integer(), nullable=False),
        sa.Column("actual_progress", sa.Integer(), nullable=False),
        sa.Column("deviation", sa.Integer(), nullable=False),
        sa.Column("days_since_update", sa.Integer(), nullable=False),
        sa.Column("is_free_day", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("idx_snap_date", "prog_snapshot", ["snap_date"])
    op.create_index("idx_snap_dev", "prog_snapshot", ["snap_date", "deviation"])
    op.create_index("idx_snap_stagnant", "prog_snapshot", ["snap_date", "days_since_update"])
    cmt("prog_snapshot", "★ 每日快照：让'连续N天'判定变成纯 SQL")

    # ============================================================ 报表与材料
    op.create_table(
        "report",
        pk_column(),
        sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=True),
        json_column("content", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("void_reason", sa.String(500), nullable=True),
        sa.Column("voided_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint("type IN ('DAILY','WEEKLY','MONTHLY')", name="ck_report_type"),
        sa.CheckConstraint("status IN ('DRAFT','SUBMITTED','VOIDED')", name="ck_report_status"),
        sa.UniqueConstraint("sub_id", "type", "period_start", name="uq_report_period"),
    )
    op.create_index("idx_report_period", "report", ["type", "period_start"])
    op.create_index("idx_report_sub", "report", ["sub_id", "type", "period_start"])
    op.create_index("idx_report_status", "report", ["status"])
    gin_index("report", "content", "idx_report_content")
    cmt("report", "日报/周报/月报（一张表，content 存各自特有字段）")

    op.create_table(
        "attachment",
        pk_column(),
        sa.Column("biz_type", sa.String(32), nullable=False),
        sa.Column("biz_id", sa.BigInteger(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("uploader_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    )
    op.create_index("idx_att_biz", "attachment", ["biz_type", "biz_id"])
    cmt("attachment", "附件统一表（报表/风险/项目）")

    op.create_table(
        "sys_holiday",
        pk_column(),
        sa.Column("date", sa.Date(), nullable=False, unique=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.CheckConstraint("type IN ('HOLIDAY','VACATION')", name="ck_holiday_type"),
    )
    op.create_index("idx_holiday_date", "sys_holiday", ["date"])
    cmt("sys_holiday", "免填日历（法定节假日、寒暑假）")

    # ============================================================ 预警与督办
    op.create_table(
        "alert",
        pk_column(),
        sa.Column("alert_type", sa.String(8), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), nullable=True),
        sa.Column("major_id", sa.BigInteger(), sa.ForeignKey("proj_major.id"), nullable=True),
        sa.Column("college_id", sa.BigInteger(), sa.ForeignKey("sys_college.id"), nullable=True),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(500), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedup_key", sa.String(128), nullable=False),
        sa.Column("merged_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint("level IN ('RED','ORANGE','YELLOW')", name="ck_alert_level"),
        sa.CheckConstraint("status IN ('PENDING','CLAIMED','CLOSED','IGNORED')", name="ck_alert_status"),
        sa.CheckConstraint("alert_type IN ('AL-01','AL-02','AL-03','AL-04','AL-05','AL-06',"
                           "'AL-07','AL-08','AL-09','AL-10','AL-11')", name="ck_alert_type"),
    )
    op.create_index("idx_alert_dedup", "alert", ["dedup_key", "created_at"])
    op.create_index("idx_alert_status", "alert", ["status", "level"])
    op.create_index("idx_alert_scope", "alert", ["major_id", "college_id"])
    op.create_index("idx_alert_deadline", "alert", ["status", "deadline_at"])
    op.create_index("idx_alert_sub", "alert", ["sub_id"])
    cmt("alert", "预警主表（11 类规则）")
    col_cmt("alert", "dedup_key", "sub_id:alert_type，7 天同源合并依据")
    col_cmt("alert", "merged_count", "合并计数")
    col_cmt("alert", "deadline_at", "处置截止：红 3 日 / 橙 7 日")

    op.create_table(
        "alert_action",
        pk_column(),
        sa.Column("alert_id", sa.BigInteger(), sa.ForeignKey("alert.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("operator_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint(
            "action IN ('CLAIM','TRANSFER','CLOSE','IGNORE','ESCALATE')", name="ck_action_type"
        ),
    )
    op.create_index("idx_action_alert", "alert_action", ["alert_id"])
    cmt("alert_action", "预警处置流水")

    op.create_table(
        "risk",
        pk_column(),
        sa.Column("major_id", sa.BigInteger(), sa.ForeignKey("proj_major.id"), nullable=False),
        sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), nullable=True),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("plan_close_date", sa.Date(), nullable=False),
        sa.Column("actual_close_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        *_timestamps(),
        sa.CheckConstraint("level IN ('HIGH','MEDIUM','LOW')", name="ck_risk_level"),
        sa.CheckConstraint("status IN ('OPEN','CLOSED')", name="ck_risk_status"),
    )
    op.create_index("idx_risk_major", "risk", ["major_id"])
    op.create_index("idx_risk_open", "risk", ["status", "plan_close_date"])
    cmt("risk", "风险登记")

    # ============================================================ 系统与审计
    op.create_table(
        "sys_config",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False, server_default="STRING"),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    )
    cmt("sys_config", "系统参数（阈值、时限、开关、通知渠道）")

    op.create_table(
        "audit_log",
        pk_column(),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        json_column("detail", nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
    )
    op.create_index("idx_audit_time", "audit_log", ["created_at"])
    op.create_index("idx_audit_op", "audit_log", ["operator_id", "created_at"])
    op.create_index("idx_audit_target", "audit_log", ["target_type", "target_id"])
    cmt("audit_log", "审计日志（只插不改，保留 2 年）")

    op.create_table(
        "notify_log",
        pk_column(),
        sa.Column("biz_type", sa.String(32), nullable=False),
        sa.Column("biz_id", sa.BigInteger(), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("receiver_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint("channel IN ('INNER','EMAIL','SMS')", name="ck_notify_channel"),
        sa.CheckConstraint("status IN ('SUCCESS','FAILED')", name="ck_notify_status"),
    )
    op.create_index("idx_notify_receiver", "notify_log", ["receiver_id", "sent_at"])
    op.create_index("idx_notify_biz", "notify_log", ["biz_type", "biz_id"])
    cmt("notify_log", "通知送达记录（以邮件发出成功作为送达认定）")

    # ============================================================ 二期预留表
    # 一期不建也不影响；如确定要做二期验收，取消下面注释即可
    op.create_table(
        "proj_acceptance",
        pk_column(),
        sa.Column("major_id", sa.BigInteger(), sa.ForeignKey("proj_major.id"), nullable=False),
        sa.Column("sub_id", sa.BigInteger(), sa.ForeignKey("proj_sub.id"), nullable=True),
        sa.Column("org", sa.String(128), nullable=True),
        sa.Column("accept_date", sa.Date(), nullable=True),
        sa.Column("result", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(), nullable=False),
        sa.CheckConstraint(
            "result IN ('PENDING','PASSED','RECTIFYING','REJECTED')", name="ck_acceptance_result"
        ),
    )
    op.create_index("idx_acceptance_major", "proj_acceptance", ["major_id"])
    cmt("proj_acceptance", "【二期预留】验收记录")

    op.create_table(
        "proj_rectify",
        pk_column(),
        sa.Column("acceptance_id", sa.BigInteger(), sa.ForeignKey("proj_acceptance.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=True),
        sa.Column("plan_finish", sa.Date(), nullable=True),
        sa.Column("actual_finish", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        *_timestamps(),
        sa.CheckConstraint("status IN ('OPEN','DONE')", name="ck_rectify_status"),
    )
    op.create_index("idx_rectify_acc", "proj_rectify", ["acceptance_id"])
    cmt("proj_rectify", "【二期预留】整改事项")

    op.create_table(
        "proj_evaluation",
        pk_column(),
        sa.Column("major_id", sa.BigInteger(), sa.ForeignKey("proj_major.id"), nullable=False),
        sa.Column("indicator_code", sa.String(64), nullable=False),
        sa.Column("indicator_name", sa.String(128), nullable=False),
        sa.Column("value", sa.String(128), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        *_timestamps(),
    )
    op.create_index("idx_eval_major", "proj_evaluation", ["major_id"])
    cmt("proj_evaluation", "【二期预留】成效指标登记")

    # ============================================================ 初始数据
    _seed()


def _seed() -> None:
    """初始数据：7 个预置角色、11 条预警配置、常用参数"""

    roles = [
        ("SYS_ADMIN", "系统管理员", True),
        ("PROJECT_OWNER", "项目总负责人", True),
        ("MAJOR_OWNER", "大项目负责人", True),
        ("COLLEGE_OWNER", "学院负责人", True),
        ("SUB_OWNER", "子项目负责人", True),
        ("REPORTER", "填报人", True),
        ("VIEWER", "查看者", True),
    ]
    for code, name, is_sys in roles:
        op.execute(
            upsert_text("INSERT INTO sys_role (code, name, is_system) VALUES (:c, :n, :s) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(c=code, n=name, s=is_sys)
        )

    configs = [
        # 预警阈值
        ("alert.AL-01.enabled", "true", "BOOLEAN", "进度滞后预警开关"),
        ("alert.AL-01.deviation", "-10", "INT", "滞后阈值（百分点）"),
        ("alert.AL-01.days", "3", "INT", "滞后持续天数"),
        ("alert.AL-02.enabled", "true", "BOOLEAN", "严重滞后预警开关"),
        ("alert.AL-02.deviation", "-25", "INT", "严重滞后阈值"),
        ("alert.AL-03.enabled", "true", "BOOLEAN", "进度停滞预警开关"),
        ("alert.AL-03.days", "21", "INT", "停滞判定天数"),
        ("alert.AL-05.enabled", "true", "BOOLEAN", "项目临期预警开关"),
        ("alert.AL-05.days", "30", "INT", "临期判定天数"),
        ("alert.AL-05.progress", "80", "INT", "临期进度阈值"),
        ("alert.AL-07.enabled", "true", "BOOLEAN", "连续未填预警开关"),
        ("alert.AL-07.periods", "3", "INT", "连续未填周期数"),
        ("alert.AL-08.enabled", "true", "BOOLEAN", "风险超期预警开关"),
        ("alert.AL-08.days", "7", "INT", "风险闭环默认天数"),
        ("alert.AL-11.enabled", "true", "BOOLEAN", "长期零进度预警开关"),
        ("alert.AL-11.days", "60", "INT", "零进度判定天数"),
        # 处置时限
        ("alert.red.deadline_days", "3", "INT", "红色预警处置天数"),
        ("alert.orange.deadline_days", "7", "INT", "橙色预警处置天数"),
        # 报表时限
        ("report.daily.enabled", "false", "BOOLEAN", "日报是否启用（按项目类型可覆盖）"),
        ("report.weekly.deadline_weekday", "1", "INT", "周报截止星期（1=周一）"),
        ("report.weekly.deadline_hour", "12", "INT", "周报截止小时"),
        ("report.monthly.deadline_workday", "3", "INT", "月报截止第几个工作日"),
        ("report.monthly.deadline_hour", "18", "INT", "月报截止小时"),
        ("report.correct_days", "3", "INT", "填报人自行更正天数"),
        # 汇总
        ("aggregate.school.mode", "EQUAL", "STRING", "全校进度汇总方式：EQUAL 等权"),
        ("aggregate.include_closed", "true", "BOOLEAN", "已结项子项目是否按100%计入"),
        # 通知
        ("notify.digest.hour", "8", "INT", "每日摘要小时"),
        ("notify.digest.minute", "30", "INT", "每日摘要分钟"),
        ("notify.sms.enabled", "false", "BOOLEAN", "短信开关（仅红色预警）"),
        ("notify.email.enabled", "true", "BOOLEAN", "邮件开关"),
        # 系统
        ("system.audit_retention_years", "2", "INT", "审计日志保留年数"),
        ("system.session_idle_minutes", "30", "INT", "会话空闲超时分钟"),
        ("system.snapshot_retention_years", "3", "INT", "快照保留年数"),
    ]
    for k, v, t, remark in configs:
        op.execute(
            upsert_text("INSERT INTO sys_config (key, value, value_type, remark) VALUES (:k, :v, :t, :r) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=k, v=v, t=t, r=remark)
        )

    # 权限点：模块 → 页面 → 操作
    perms = [
        # (code, name, type, parent_code)
        ("dashboard", "我的督办", "MODULE", None),
        ("dashboard:view", "查看我的督办", "PAGE", "dashboard"),
        ("project", "项目管理", "MODULE", None),
        ("project:major", "大项目", "PAGE", "project"),
        ("project:major:view", "查看大项目", "ACTION", "project:major"),
        ("project:major:edit", "新建/编辑大项目", "ACTION", "project:major"),
        ("project:college", "学院分工", "PAGE", "project"),
        ("project:college:edit", "配置学院与负责人", "ACTION", "project:college"),
        ("project:sub", "子项目", "PAGE", "project"),
        ("project:sub:view", "查看子项目", "ACTION", "project:sub"),
        ("project:sub:edit", "新建/编辑子项目", "ACTION", "project:sub"),
        ("project:check", "完整性检查", "PAGE", "project"),
        ("progress", "进度管控", "MODULE", None),
        ("progress:table", "进度总表", "PAGE", "progress"),
        ("progress:update", "更新进度", "ACTION", "progress:table"),
        ("progress:onbehalf", "代填进度", "ACTION", "progress:table"),
        ("progress:urge", "催办", "ACTION", "progress:table"),
        ("alert", "预警管理", "MODULE", None),
        ("alert:list", "预警中心", "PAGE", "alert"),
        ("alert:claim", "认领预警", "ACTION", "alert:list"),
        ("alert:close", "关闭预警", "ACTION", "alert:list"),
        ("alert:transfer", "转交预警", "ACTION", "alert:list"),
        ("report", "报表管理", "MODULE", None),
        ("report:fill", "填报报表", "PAGE", "report"),
        ("report:void", "作废报表", "ACTION", "report:fill"),
        ("risk", "风险登记", "MODULE", None),
        ("risk:list", "风险列表", "PAGE", "risk"),
        ("risk:edit", "登记/编辑风险", "ACTION", "risk:list"),
        ("export", "数据导出", "MODULE", None),
        ("export:ledger", "导出台账", "ACTION", "export"),
        ("export:progress", "导出进度报表", "ACTION", "export"),
        ("system", "系统管理", "MODULE", None),
        ("system:user", "用户管理", "PAGE", "system"),
        ("system:role", "角色权限", "PAGE", "system"),
        ("system:org", "组织管理", "PAGE", "system"),
        ("system:config", "参数配置", "PAGE", "system"),
        ("system:holiday", "免填日历", "PAGE", "system"),
        ("system:audit", "操作日志", "PAGE", "system"),
    ]
    for code, name, typ, parent in perms:
        op.execute(
            upsert_text("INSERT INTO sys_permission (code, name, type, parent_id) "
                "VALUES (:c, :n, :t, (SELECT id FROM sys_permission WHERE code = :p)) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(c=code, n=name, t=typ, p=parent)
        )

    # 系统管理员拥有全部权限
    op.execute(
        """
        INSERT INTO sys_role_permission (role_id, permission_id)
        SELECT r.id, p.id FROM sys_role r CROSS JOIN sys_permission p
        WHERE r.code = 'SYS_ADMIN'
        ON CONFLICT DO NOTHING
        """
    )

    # 其余角色的基础权限（查看类）
    viewer_perms = [
        "dashboard", "dashboard:view",
        "project", "project:major", "project:major:view",
        "project:sub", "project:sub:view",
        "progress", "progress:table",
        "alert", "alert:list",
        "export", "export:ledger", "export:progress",
    ]
    for role_code in ("PROJECT_OWNER", "MAJOR_OWNER", "COLLEGE_OWNER", "SUB_OWNER", "REPORTER", "VIEWER"):
        for pc in viewer_perms:
            op.execute(
                upsert_text("INSERT INTO sys_role_permission (role_id, permission_id) "
                    "SELECT r.id, p.id FROM sys_role r, sys_permission p "
                    "WHERE r.code = :rc AND p.code = :pc ON CONFLICT DO NOTHING"
                ).bindparams(rc=role_code, pc=pc)
            )


# ---------------------------------------------------------------- downgrade
def downgrade() -> None:
    tables = [
        "proj_evaluation",
        "proj_rectify",
        "proj_acceptance",
        "notify_log",
        "audit_log",
        "sys_config",
        "risk",
        "alert_action",
        "alert",
        "sys_holiday",
        "attachment",
        "report",
        "prog_snapshot",
        "prog_record",
        "proj_sub",
        "proj_major_college",
        "proj_major",
        "sys_role_permission",
        "sys_permission",
        "sys_user_role",
        "sys_role",
        "sys_user",
        "sys_college",
    ]
    for t in tables:
        op.drop_table(t)
