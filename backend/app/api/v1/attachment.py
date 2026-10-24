"""附件 API：上传、下载、列表、删除

文件存文件系统，数据库只存元数据。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError, ForbiddenError
from app.core.response import ok
from app.core.scope import can_access_risk, can_access_sub
from app.db.session import get_db
from app.models.alert import Risk
from app.models.project import ProjSub
from app.models.report import Report
from app.services.attachment_service import AttachmentService

router = APIRouter(prefix="/api/attachments", tags=["附件管理"])


def _assert_biz_access(
    user: CurrentUser, db: Session, biz_type: str, biz_id: int, uploader_id: int | None = None
) -> None:
    """附件所属业务对象的数据范围校验（防水平越权下载/上传）"""
    if user.has_all_scope():
        return
    if biz_type == "OTHER":
        # 无业务对象：仅上传人本人
        if uploader_id is not None and uploader_id == user.id:
            return
    elif biz_type == "SUB":
        sub = db.get(ProjSub, biz_id)
        if sub is not None and can_access_sub(user, sub):
            return
    elif biz_type == "MAJOR":
        if biz_id in user.major_ids():
            return
    elif biz_type == "REPORT":
        rep = db.get(Report, biz_id)
        sub = db.get(ProjSub, rep.sub_id) if rep is not None else None
        if sub is not None and can_access_sub(user, sub):
            return
    elif biz_type == "RISK":
        risk = db.get(Risk, biz_id)
        if risk is not None:
            sub = db.get(ProjSub, risk.sub_id) if risk.sub_id else None
            if can_access_risk(user, risk, sub):
                return
    raise ForbiddenError("无权访问该业务的附件")


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
    # 数据范围：只能往自己有权访问的业务对象上传附件
    _assert_biz_access(user, db, biz_type, biz_id)
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
    _assert_biz_access(user, db, biz_type, biz_id)
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
    _assert_biz_access(user, db, att.biz_type, att.biz_id, uploader_id=att.uploader_id)
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
    _assert_biz_access(user, db, att.biz_type, att.biz_id, uploader_id=att.uploader_id)
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
@router.get(
    "/stats",
    summary="附件存储统计（含孤儿文件）",
    dependencies=[Depends(require_perm("system:config"))],
)
def storage_stats(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """磁盘占用、剩余空间与孤儿文件：属运维信息，限制为管理员（system:config）"""
    svc = AttachmentService(db)
    return ok(svc.stats())


# ================================================================ 孤儿文件清理
@router.post(
    "/orphans/purge",
    summary="清理孤儿文件（数据库无记录的物理文件）",
    dependencies=[Depends(require_perm("system:config"))],
)
def purge_orphans(db: Session = Depends(get_db)):
    """清理孤儿文件

    背景：上传目录里存在但 attachment 表无记录的文件（事务回滚、
    库还原到旧备份、手工拷贝等都会产生），长期白占磁盘。
    先 GET /api/attachments/stats 看 orphan_count 再决定是否清理。
    """
    svc = AttachmentService(db)
    return ok(svc.purge_orphans())

