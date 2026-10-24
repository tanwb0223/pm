"""附件模型：文件存文件系统，数据库只存元数据

对应表结构（0001 建表 + 0004 增强）：
    id, biz_type, biz_id, file_name, stored_path, file_size,
    mime_type, file_ext, sha256, uploader_id, download_count,
    deleted_at, deleted_by, uploaded_at

设计要点（文档 3.4 / 6.3 第 33 项）：
1. 文件实体存 {PM_UPLOAD_DIR}/YYYY/MM/，数据库只存相对路径
2. 用 UUID 作存储文件名，避免中文/重名/路径注入
3. 保留原始文件名供下载时显示
4. 软删除：只标记 deleted_at，不物理删文件（防误删可恢复）
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import BIGINT_PK, JSON_TYPE, aware_timestamp


class Attachment(Base):
    """附件元数据

    文件实体路径 = {PM_UPLOAD_DIR}/{stored_path}
    """

    __tablename__ = "attachment"
    __table_args__ = (
        Index("idx_att_biz", "biz_type", "biz_id"),
        Index("idx_att_uploader", "uploader_id"),
        Index("idx_att_created", "uploaded_at"),
        Index("idx_att_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)

    # 业务关联
    biz_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="业务类型：SUB/MAJOR/REPORT/RISK/OTHER"
    )
    biz_id: Mapped[int] = mapped_column(BIGINT_PK, nullable=False, comment="业务对象 ID")

    # 文件信息
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    stored_path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="相对路径 YYYY/MM/uuid.ext"
    )
    file_size: Mapped[int] = mapped_column(BIGINT_PK, nullable=False, comment="字节数")
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_ext: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", server_default="", comment="扩展名"
    )
    sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="内容哈希，用于去重"
    )

    # 上传人
    uploader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_user.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = aware_timestamp(server_default=func.now(), nullable=False
    )

    # 下载计数
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # 软删除
    deleted_at: Mapped[datetime | None] = aware_timestamp(nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(BIGINT_PK, nullable=True)

    uploader: Mapped["SysUser"] = relationship(lazy="joined")


def new_stored_path(original_name: str) -> str:
    """生成存储路径：YYYY/MM/{uuid}.{ext}

    用 UUID 而非原文件名，原因：
    - 避免中文/空格/特殊字符在不同文件系统上的兼容问题
    - 避免同名覆盖
    - 避免路径穿越（如 ../../etc/passwd）
    """
    ext = ""
    if "." in original_name:
        ext = original_name.rsplit(".", 1)[-1].lower()[:16]
        ext = "".join(c for c in ext if c.isalnum())

    now = datetime.now()
    folder = f"{now.year}/{now.month:02d}"
    name = uuid.uuid4().hex
    return f"{folder}/{name}.{ext}" if ext else f"{folder}/{name}"


from app.models.system import SysUser  # noqa: E402
