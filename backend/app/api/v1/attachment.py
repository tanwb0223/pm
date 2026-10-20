"""附件 API：上传、下载、列表、删除

文件存文件系统，数据库只存元数据。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError
from app.core.response import ok
from app.db.session import get_db
from app.services.attachment_service import AttachmentService

router = APIRouter(prefix="/api/attachments", tags=["附件管理"])


def _out(att, service: AttachmentService) -> dict:
    return {
        "id": att.id,
        "biz_type": att.biz_type,
        "biz_id": att.biz_id,
        "file_name": att.file_name,
        "file_size": att.file_size,
        "size_text": _human_size(att.file_size),
        "file_ext": att.file_ext,
        "mime_type": att.mime_type,
        "download_count": att.download_count,
        "uploader_id": att.uploader_id,
        "uploader_name": att.uploader.name if att.uploader else None,
        "uploaded_at": att.uploaded_at,
    }


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


# ================================================================ 上传
@router.post("/upload", summary="上传附件")
async def upload(
    file: UploadFile = File(..., description="文件"),
    biz_type: str = Form(..., description="业务类型：SUB/MAJOR/REPORT/RISK/OTHER"),
    biz_id: int = Form(..., description="业务对象 ID"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AttachmentService(db)
    att = svc.save(
        file_obj=file.file,
        file_name=file.filename or "unnamed",
        biz_type=biz_type,
        biz_id=biz_id,
        uploader_id=user.id,
        content_type=file.content_type,
    )
    db.commit()
    return ok(_out(att, svc))


# ================================================================ 列表
@router.get("", summary="附件列表")
def list_attachments(
    biz_type: str = Query(..., description="业务类型"),
    biz_id: int = Query(..., description="业务对象 ID"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AttachmentService(db)
    items = svc.list_by_biz(biz_type, biz_id)
    return ok([_out(a, svc) for a in items])


# ================================================================ 下载
@router.get("/{attachment_id}/download", summary="下载附件")
def download(
    attachment_id: int,
    inline: bool = Query(default=False, description="是否在浏览器内预览"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AttachmentService(db)
    att = svc.get(attachment_id)
    path = svc.abs_path(att)

    if not path.is_file():
        raise BusinessError("文件已丢失，请联系管理员")

    svc.mark_download(att)
    db.commit()

    # 中文文件名用 RFC 5987 编码
    disposition = "inline" if inline else "attachment"
    encoded = quote(att.file_name)

    return FileResponse(
        path,
        media_type=att.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


# ================================================================ 预览
@router.get("/{attachment_id}/preview", summary="预览附件（图片/PDF 内联）")
def preview(
    attachment_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AttachmentService(db)
    att = svc.get(attachment_id)
    path = svc.abs_path(att)

    if not path.is_file():
        raise BusinessError("文件已丢失")

    # 只有可预览类型才内联，其他强制下载
    previewable = att.mime_type.startswith(("image/", "text/")) or att.mime_type == "application/pdf"
    disposition = "inline" if previewable else "attachment"
    encoded = quote(att.file_name)

    return FileResponse(
        path,
        media_type=att.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}",
        },
    )


# ================================================================ 删除
@router.delete("/{attachment_id}", summary="删除附件（软删除）")
def delete_attachment(
    attachment_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AttachmentService(db)
    att = svc.get(attachment_id)

    # 只有上传人本人或管理员能删
    if att.uploader_id != user.id and not user.is_admin:
        raise BusinessError("只能删除自己上传的附件")

    svc.delete(attachment_id, user.id)
    db.commit()
    return ok()


@router.delete(
    "/{attachment_id}/purge",
    summary="彻底删除（仅管理员，删物理文件）",
    dependencies=[Depends(require_perm("system:config"))],
)
def purge_attachment(attachment_id: int, db: Session = Depends(get_db)):
    svc = AttachmentService(db)
    svc.purge(attachment_id)
    db.commit()
    return ok()


# ================================================================ 统计
@router.get("/stats", summary="附件存储统计")
def storage_stats(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AttachmentService(db)
    return ok(svc.stats())
