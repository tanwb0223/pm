"""增强 attachment 表：补充软删除、哈希、下载计数等字段

0001_init 已建 attachment 表（基础字段）：
    id, biz_type, biz_id, file_name, file_path, file_size,
    mime_type, uploader_id, uploaded_at

本迁移补充：
    file_ext        扩展名（便于筛选与图标显示）
    sha256          内容哈希（去重/秒传/完整性校验）
    download_count  下载次数
    deleted_at      软删除时间
    deleted_by      删除人
    stored_path     重命名 file_path → stored_path（语义更清晰）

Revision ID: 0004_attachment
Revises: 0003_alert_nullable
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from alembic_helpers import cmt, col_cmt, json_column, pk_column

revision = "0004_attachment"
down_revision = "0003_alert_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------- 字段重命名
    # file_path → stored_path（语义更准确：存的是相对路径）
    op.alter_column("attachment", "file_path", new_column_name="stored_path")

    # ---------------------------------------------------------- 新增字段
    op.add_column(
        "attachment",
        sa.Column("file_ext", sa.String(16), nullable=False, server_default=""),
    )
    op.add_column(
        "attachment",
        sa.Column("sha256", sa.String(64), nullable=True, comment="内容哈希，用于去重"),
    )
    op.add_column(
        "attachment",
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("attachment", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("attachment", sa.Column("deleted_by", sa.BigInteger(), nullable=True))

    # ---------------------------------------------------------- 补充索引
    op.create_index("idx_att_uploader", "attachment", ["uploader_id"])
    op.create_index("idx_att_created", "attachment", ["uploaded_at"])
    op.create_index("idx_att_sha256", "attachment", ["sha256"])

    # ---------------------------------------------------------- 注释
    col_cmt("attachment", "stored_path", "相对路径 YYYY/MM/uuid.ext，实际文件在 PM_UPLOAD_DIR 下")
    col_cmt("attachment", "file_ext", "扩展名，如 pdf/docx")
    col_cmt("attachment", "sha256", "文件内容 SHA256，用于去重和完整性校验")
    col_cmt("attachment", "deleted_at", "软删除时间，非空表示已删除（文件保留可恢复）")
    cmt("attachment", "附件元数据：文件实体存文件系统，数据库只存元数据")


def downgrade() -> None:
    op.drop_index("idx_att_sha256", table_name="attachment")
    op.drop_index("idx_att_created", table_name="attachment")
    op.drop_index("idx_att_uploader", table_name="attachment")
    op.drop_column("attachment", "deleted_by")
    op.drop_column("attachment", "deleted_at")
    op.drop_column("attachment", "download_count")
    op.drop_column("attachment", "sha256")
    op.drop_column("attachment", "file_ext")
    op.alter_column("attachment", "stored_path", new_column_name="file_path")
