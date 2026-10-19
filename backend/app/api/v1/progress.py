"""进度 API：填报、留痕、进度总表、汇总、快照"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError
from app.core.response import ok, page
from app.core.scope import assert_sub_access
from app.db.session import get_db
from app.services.progress_service import ProgressService
from app.services.project_service import ProjectService

router = APIRouter(prefix="/api", tags=["进度管控"])


class ProgressIn(BaseModel):
    progress: int = Field(ge=0, le=100, description="进度百分比")
    note: str | None = Field(default=None, max_length=500, description="一句说明")
    no_change: bool = Field(default=False, description="本周期无变化")
    rollback_reason: str | None = Field(default=None, max_length=500, description="回调原因")

    @model_validator(mode="after")
    def check(self):
        if self.no_change and self.progress:
            # 允许传当前值，但不强制为 0
            pass
        return self


class SnapshotIn(BaseModel):
    day: date | None = None


# ================================================================ 填报
@router.post(
    "/subs/{sub_id}/progress",
    summary="更新进度（自动留痕）",
    dependencies=[Depends(require_perm("progress:update"))],
)
def update_progress(
    sub_id: int,
    body: ProgressIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProgressService(db)
    try:
        result = svc.update_progress(
            sub_id,
            progress=body.progress,
            note=body.note,
            no_change=body.no_change,
            rollback_reason=body.rollback_reason,
            operator=user,
        )
    except BusinessError:
        raise
    db.commit()
    return ok(result)


@router.get("/subs/{sub_id}/records", summary="进度变更留痕")
def list_records(
    sub_id: int,
    limit: int = Query(default=100, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProgressService(db)
    assert_sub_access(user, ProjectService(db).get_sub(sub_id))
    rows = svc.list_records(sub_id, limit)
    return ok(
        [
            {
                "id": r.id,
                "progress": r.progress,
                "note": r.note,
                "is_no_change": r.is_no_change,
                "is_rollback": r.is_rollback,
                "rollback_reason": r.rollback_reason,
                "operator_id": r.operator_id,
                "on_behalf_of": r.on_behalf_of,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


@router.get("/subs/{sub_id}/view", summary="子项目进度视图（含时间进度与偏差）")
def sub_view(
    sub_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    proj = ProjectService(db)
    s = proj.get_sub(sub_id)
    assert_sub_access(user, s)
    svc = ProgressService(db)
    return ok(svc.sub_view(s))


# ================================================================ 进度总表
@router.get("/progress/table", summary="进度总表（默认按偏差排序）")
def progress_table(
    major_id: int | None = None,
    college_id: int | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    keyword: str | None = None,
    only_abnormal: bool = Query(default=False, description="只看异常"),
    sort: str = Query(default="deviation", pattern="^(deviation|update|progress)$"),
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProgressService(db)
    rows, total = svc.table(
        user,
        major_id=major_id,
        college_id=college_id,
        status=status,
        owner_id=owner_id,
        keyword=keyword,
        only_abnormal=only_abnormal,
        page=page_no,
        page_size=page_size,
        sort=sort,
    )
    return page(rows, total, page_no, page_size)


# ================================================================ 汇总
@router.get("/progress/major/{major_id}", summary="大项目进度汇总（含学院分解）")
def major_summary(
    major_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProgressService(db)
    return ok(svc.major_summary(major_id))


@router.get("/progress/school", summary="全校进度汇总")
def school_summary(db: Session = Depends(get_db)):
    svc = ProgressService(db)
    return ok(svc.school_summary())


# ================================================================ 趋势与对比
@router.get("/progress/sub/{sub_id}/trend", summary="子项目进度趋势曲线")
def sub_trend(
    sub_id: int,
    days: int = Query(default=90, ge=7, le=365, description="统计天数"),
    db: Session = Depends(get_db),
):
    """返回两条线：实际进度 vs 按时间应有进度，用于看「从哪天开始慢了」"""
    svc = ProgressService(db)
    return ok(svc.sub_trend(sub_id, days))


@router.get("/progress/college-compare", summary="学院横向对比")
def college_compare(
    major_id: int | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """各学院平均偏差、滞后数、更新及时率，用于校级横向比较"""
    svc = ProgressService(db)
    return ok(svc.college_compare(user, major_id))


# ================================================================ 快照
@router.post(
    "/progress/snapshot",
    summary="生成每日快照（手动触发，定时任务也会跑）",
    dependencies=[Depends(require_perm("system:config"))],
)
def generate_snapshot(body: SnapshotIn, db: Session = Depends(get_db)):
    svc = ProgressService(db)
    result = svc.generate_snapshots(body.day)
    db.commit()
    return ok(result)
