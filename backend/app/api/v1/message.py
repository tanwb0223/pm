"""站内消息 API + 定时任务运行记录 API

## 为什么新增（对应 .workbuddy/audit/backend-system.md P0-2 / P0-5）

1. **站内消息没有用户入口**：INNER 通道只写 notify_log 就记 SUCCESS，
   被通知人（子项目负责人等普通角色）没有 system:audit 权限，
   永远看不到预警/催办/摘要。这里提供「我的消息」四个接口。
2. **定时任务不可观测**：6 个 job 失败只有 logger.exception，
   管理员在系统里看不到昨晚快照/备份/扫描有没有成功。
   `GET /api/system/jobs` 暴露 `sys_job_run` 最近运行状态。

两个路由合并放在本文件、共用同一个 router 对象（前缀 /api），
好处是 main.py 只需 include 一次；`/system/jobs` 是系统运维视角，
但为避免动别人负责的 system.py，这里单独挂在同一 router 下。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.response import ok, page
from app.db.session import get_db
from app.models.system import SysJobRun
from app.services.message_service import MessageService

router = APIRouter(prefix="/api", tags=["站内消息"])


def _out(m) -> dict:
    """消息出参：字段与前端约定一致（E 组已按此对接）"""
    return {
        "id": m.id,
        "biz_type": m.biz_type,
        "biz_id": m.biz_id,
        "level": m.level,
        "title": m.title,
        "body": m.body,
        "read_at": m.read_at,
        "created_at": m.created_at,
    }


# ================================================================ 我的消息
@router.get("/messages", summary="我的消息（收件箱，分页）")
def list_messages(
    unread_only: bool = False,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = MessageService(db).list_mine(
        user, unread_only=unread_only, page=page_no, page_size=page_size
    )
    return page([_out(m) for m in rows], total, page_no, page_size)


@router.get("/messages/unread-count", summary="我的未读消息数")
def unread_count(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ok({"count": MessageService(db).unread_count(user)})


@router.post("/messages/{message_id}/read", summary="标记单条消息已读")
def mark_read(
    message_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    MessageService(db).mark_read(user, message_id)
    db.commit()
    return ok()


@router.post("/messages/read-all", summary="全部标记已读")
def read_all(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updated = MessageService(db).read_all(user)
    db.commit()
    return ok({"updated": updated})


# ================================================================ 定时任务
@router.get(
    "/system/jobs",
    summary="定时任务运行记录（最近 N 条）",
    dependencies=[Depends(require_perm("system:config"))],
)
def list_job_runs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """返回 sys_job_run 最近运行记录

    status 取值：RUNNING / SUCCESS / FAILED / SKIPPED
    （SKIPPED 表示该次因其他实例持锁而跳过，不算失败）
    """
    rows = list(
        db.scalars(
            select(SysJobRun).order_by(SysJobRun.id.desc()).limit(limit)
        )
    )
    return ok(
        [
            {
                "id": r.id,
                "job_id": r.job_id,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "status": r.status,
                "message": r.message,
            }
            for r in rows
        ]
    )
