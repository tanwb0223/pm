"""附件服务：文件存储与元数据管理

安全要点：
1. 路径穿越防护（拒绝 ../ 等）
2. 文件类型白名单
3. 大小限制
4. 文件名净化
5. 存储路径用 UUID，原始名只存数据库
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessError, NotFoundError
from app.core.scope import apply_scope
from app.models.attachment import Attachment, new_stored_path

logger = logging.getLogger(__name__)

# 允许的文件类型（按扩展名）
ALLOWED_EXTS = {
    # 文档
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv",
    # 图片
    "jpg", "jpeg", "png", "gif", "bmp", "webp",
    # 压缩包
    "zip", "rar", "7z",
    # 其他
    "ofd",
}

# 禁止的扩展名（可执行文件，防病毒）
BLOCKED_EXTS = {
    "exe", "bat", "cmd", "com", "scr", "msi", "vbs", "js", "jar",
    "sh", "ps1", "py", "php", "asp", "jsp", "dll", "so",
}

BIZ_TYPES = {"SUB", "MAJOR", "REPORT", "RISK", "OTHER"}


class AttachmentService:
    def __init__(self, db: Session):
        self.db = db
        self.root = Path(settings.upload_dir)

    # ============================================================ 上传
    def save(
        self,
        *,
        file_obj: BinaryIO,
        file_name: str,
        biz_type: str,
        biz_id: int,
        uploader_id: int,
        content_type: str | None = None,
    ) -> Attachment:
        """保存附件

        Args:
            file_obj: 文件对象
            file_name: 原始文件名
            biz_type: 业务类型（SUB/MAJOR/REPORT/RISK/OTHER）
            biz_id: 业务 ID
            uploader_id: 上传人
            content_type: MIME 类型
        """
        if biz_type not in BIZ_TYPES:
            raise BusinessError(f"无效的业务类型：{biz_type}")
        if biz_id <= 0:
            raise BusinessError("业务 ID 无效")

        safe_name = self._sanitize_name(file_name)
        ext = self._ext_of(safe_name)
        self._check_ext(ext)

        # 写入文件系统
        rel_path = new_stored_path(safe_name)
        abs_path = self.root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        max_bytes = settings.upload_max_size_mb * 1024 * 1024
        size = 0
        hasher = hashlib.sha256()

        try:
            with abs_path.open("wb") as out:
                while True:
                    chunk = file_obj.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise BusinessError(
                            f"文件超过 {settings.upload_max_size_mb} MB 限制"
                        )
                    hasher.update(chunk)
                    out.write(chunk)
        except BusinessError:
            abs_path.unlink(missing_ok=True)
            raise
        except OSError as e:
            abs_path.unlink(missing_ok=True)
            logger.exception("写入附件失败")
            raise BusinessError(f"文件保存失败：{e}") from e

        if size == 0:
            abs_path.unlink(missing_ok=True)
            raise BusinessError("文件内容为空")

        # 写元数据
        mime = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        att = Attachment(
            biz_type=biz_type,
            biz_id=biz_id,
            file_name=safe_name,
            stored_path=rel_path,
            file_size=size,
            mime_type=mime[:128],
            file_ext=ext,
            sha256=hasher.hexdigest(),
            uploader_id=uploader_id,
        )
        self.db.add(att)
        self.db.flush()

        logger.info(
            "附件已保存：id=%s name=%s size=%s biz=%s:%s",
            att.id, safe_name, size, biz_type, biz_id,
        )
        return att

    # ============================================================ 查询
    def list_by_biz(self, biz_type: str, biz_id: int) -> list[Attachment]:
        return list(
            self.db.scalars(
                select(Attachment)
                .where(
                    Attachment.biz_type == biz_type,
                    Attachment.biz_id == biz_id,
                    Attachment.deleted_at.is_(None),
                )
                .order_by(Attachment.uploaded_at.desc())
            )
        )

    def get(self, attachment_id: int) -> Attachment:
        att = self.db.get(Attachment, attachment_id)
        if att is None or att.deleted_at is not None:
            raise NotFoundError("附件不存在")
        return att

    def abs_path(self, att: Attachment) -> Path:
        """取文件绝对路径，并做路径穿越校验"""
        p = (self.root / att.stored_path).resolve()
        root = self.root.resolve()
        try:
            p.relative_to(root)
        except ValueError:
            logger.error("检测到路径穿越尝试：%s", att.stored_path)
            raise BusinessError("非法的文件路径") from None
        return p

    # ============================================================ 下载
    def mark_download(self, att: Attachment) -> None:
        att.download_count += 1
        self.db.flush()

    # ============================================================ 删除
    def delete(self, attachment_id: int, operator_id: int) -> None:
        """软删除：只标记，不删文件（防误删，可恢复）"""
        att = self.get(attachment_id)
        att.deleted_at = datetime.now(UTC)
        att.deleted_by = operator_id
        self.db.flush()
        logger.info("附件已软删除：id=%s by=%s", attachment_id, operator_id)

    def purge(self, attachment_id: int) -> None:
        """物理删除（仅管理员，用于清理）"""
        att = self.db.get(Attachment, attachment_id)
        if att is None:
            raise NotFoundError("附件不存在")
        try:
            self.abs_path(att).unlink(missing_ok=True)
        except (OSError, BusinessError):
            logger.warning("删除物理文件失败：%s", att.stored_path, exc_info=True)
        self.db.delete(att)
        self.db.flush()

    # ============================================================ 统计
    def stats(self) -> dict[str, Any]:
        """附件使用统计"""
        total_count = self.db.scalar(
            select(func.count()).select_from(Attachment).where(
                Attachment.deleted_at.is_(None)
            )
        ) or 0
        total_size = self.db.scalar(
            select(func.coalesce(func.sum(Attachment.file_size), 0)).where(
                Attachment.deleted_at.is_(None)
            )
        ) or 0

        # 磁盘剩余空间
        try:
            usage = shutil.disk_usage(self.root)
            free_mb = usage.free / 1024 / 1024
            total_mb = usage.total / 1024 / 1024
        except OSError:
            free_mb = total_mb = 0

        return {
            "count": total_count,
            "size_mb": round(total_size / 1024 / 1024, 1),
            "storage_dir": str(self.root),
            "disk_free_mb": round(free_mb, 0),
            "disk_total_mb": round(total_mb, 0),
        }

    # ============================================================ 内部
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """净化文件名

        - 去掉路径部分（防路径穿越）
        - 去掉控制字符
        - 限制长度
        """
        if not name:
            raise BusinessError("文件名不能为空")

        # 只取最后一段（去掉 ../../ 之类）
        name = name.replace("\\", "/").split("/")[-1].strip()

        # 去掉危险字符
        bad = '<>:"|?*\x00'
        for ch in bad:
            name = name.replace(ch, "_")

        name = name.strip(". ")

        if not name:
            raise BusinessError("文件名无效")
        if len(name) > 200:
            # 保留扩展名
            ext = name.rsplit(".", 1)[-1] if "." in name else ""
            base = name[: 190 - len(ext)]
            name = f"{base}.{ext}" if ext else base
        return name

    @staticmethod
    def _ext_of(name: str) -> str:
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    @staticmethod
    def _check_ext(ext: str) -> None:
        if not ext:
            raise BusinessError("文件必须有扩展名")
        if ext in BLOCKED_EXTS:
            raise BusinessError(f"不允许上传 {ext} 类型的文件")
        if ext not in ALLOWED_EXTS:
            allowed = "、".join(sorted(ALLOWED_EXTS))
            raise BusinessError(f"不支持的文件类型 .{ext}，允许：{allowed}")
