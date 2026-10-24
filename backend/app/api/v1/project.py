"""项目台账 API：学院、大项目、学院分工、子项目、完整性检查"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError, ForbiddenError
from app.core.response import ok, page
from app.core.scope import assert_sub_access, can_write_sub_target
from app.db.session import get_db
from app.schemas.project import (
    CollegeIn,
    MajorCollegeIn,
    MajorIn,
    MajorUpdateIn,
    ObjectionIn,
    SubCloseIn,
    SubIn,
    SubUpdateIn,
)
from app.services.project_service import ProjectService

router = APIRouter(prefix="/api", tags=["项目台账"])


def _major_out(m) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "type": m.type,
        "partner_company": m.partner_company,
        "build_period": m.build_period,
        "owner_id": m.owner_id,
        "owner_name": m.owner.name if m.owner else None,
        "status": m.status,
        "original_start": m.original_start,
        "original_end": m.original_end,
        "budget_total": m.budget_total,
        "budget_used": m.budget_used,
        "acceptance_status": m.acceptance_status,
        "incomplete_reason": m.incomplete_reason,
        # 日报按项目开关（前端据此决定是否显示日报入口）
        "daily_report_enabled": m.daily_report_enabled,
        # 结项异议（前端展示「曾被异议退回」提示）
        "close_objection_at": m.close_objection_at,
        "close_objection_reason": m.close_objection_reason,
        "created_at": m.created_at,
    }


def _sub_out(s) -> dict:
    return {
        "id": s.id,
        "major_id": s.major_id,
        "major_name": s.major.name if s.major else None,
        "college_id": s.college_id,
        "college_name": None,
        "name": s.name,
        "owner_id": s.owner_id,
        "owner_name": s.owner.name if s.owner else None,
        "plan_start": s.plan_start,
        "plan_end": s.plan_end,
        "original_start": s.original_start,
        "original_end": s.original_end,
        "weight": s.weight,
        "status": s.status,
        "current_progress": s.current_progress,
        "last_update_at": s.last_update_at,
        "created_at": s.created_at,
    }


# ================================================================ 学院
@router.get("/colleges", summary="学院列表")
def list_colleges(
    user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    svc = ProjectService(db)
    return ok(
        [
            {"id": c.id, "name": c.name, "code": c.code, "parent_id": c.parent_id, "sort": c.sort}
            for c in svc.list_colleges()
        ]
    )


@router.post(
    "/colleges",
    summary="新增学院",
    dependencies=[Depends(require_perm("system:org"))],
)
def create_college(body: CollegeIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    c = svc.create_college(body)
    db.commit()
    return ok({"id": c.id})


@router.put(
    "/colleges/{college_id}",
    summary="编辑学院",
    dependencies=[Depends(require_perm("system:org"))],
)
def update_college(college_id: int, body: CollegeIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.update_college(college_id, body)
    db.commit()
    return ok()


@router.delete(
    "/colleges/{college_id}",
    summary="删除学院",
    dependencies=[Depends(require_perm("system:org"))],
)
def delete_college(college_id: int, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.delete_college(college_id)
    db.commit()
    return ok()


# ================================================================ 大项目
@router.get("/majors", summary="大项目列表")
def list_majors(
    keyword: str | None = None,
    type_: str | None = Query(default=None, alias="type"),
    status: str | None = None,
    owner_id: int | None = None,
    college_id: int | None = None,
    created_from: date | None = Query(default=None, description="立项日期起（含）"),
    created_to: date | None = Query(default=None, description="立项日期止（含）"),
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    rows, total = svc.list_majors(
        user,
        keyword=keyword,
        type_=type_,
        status=status,
        owner_id=owner_id,
        college_id=college_id,
        created_from=created_from,
        created_to=created_to,
        page=page_no,
        page_size=page_size,
    )
    out = []
    for m in rows:
        d = _major_out(m)
        subs = svc.list_major_colleges(m.id)
        d["college_count"] = len(subs)
        d["sub_count"] = sum(x["sub_count"] for x in subs)
        out.append(d)
    return page(out, total, page_no, page_size)


@router.get("/majors/{major_id}", summary="大项目详情")
def get_major(
    major_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    m = svc.get_major(major_id, user)
    d = _major_out(m)
    d["colleges"] = svc.list_major_colleges(major_id)
    return ok(d)


@router.post("/majors", summary="新建大项目", dependencies=[Depends(require_perm("project:major:edit"))])
def create_major(
    body: MajorIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    m = svc.create_major(body, user)
    db.commit()
    return ok({"id": m.id})


@router.put("/majors/{major_id}", summary="编辑大项目", dependencies=[Depends(require_perm("project:major:edit"))])
def update_major(
    major_id: int,
    body: MajorUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc.update_major(major_id, body, user)
    db.commit()
    return ok()


@router.delete("/majors/{major_id}", summary="删除大项目", dependencies=[Depends(require_perm("project:major:edit"))])
def delete_major(
    major_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc.delete_major(major_id, user)
    db.commit()
    return ok()


@router.post(
    "/majors/{major_id}/objection",
    summary="结项异议（结项后 7 天内退回）",
    dependencies=[Depends(require_perm("project:major:edit"))],
)
def raise_objection(
    major_id: int,
    body: ObjectionIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """需求 6.3 第 27 项：结项退回机制

    大项目负责人对结项结论有异议时，可在结项后 7 天内提出，
    项目状态退回 ACTIVE 继续整改；异议人/时间/原因落 close_objection_* 字段。
    """
    svc = ProjectService(db)
    svc.raise_objection(major_id, body.reason, user)
    db.commit()
    return ok()


@router.get("/majors/{major_id}/changes", summary="大项目变更留痕")
def list_major_changes(
    major_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """字段级变更流水（计划时间/权重/负责人/状态），含改前改后值与原因"""
    svc = ProjectService(db)
    svc.get_major(major_id, user)  # 数据范围校验
    rows = svc.list_changes("MAJOR", major_id, limit)
    return ok([_change_out(db, r) for r in rows])


def _change_out(db: Session, r) -> dict:
    """变更流水输出（补 operator_name，前端直接展示）"""
    from app.models.system import SysUser

    op = db.get(SysUser, r.operator_id) if r.operator_id else None
    return {
        "id": r.id,
        "field": r.field,
        "old_value": r.old_value,
        "new_value": r.new_value,
        "reason": r.reason,
        "operator_id": r.operator_id,
        "operator_name": op.name if op else None,
        "created_at": r.created_at,
    }


# ================================================================ 学院分工
@router.get("/majors/{major_id}/colleges", summary="参与学院列表")
def list_major_colleges(
    major_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc.get_major(major_id, user)
    return ok(svc.list_major_colleges(major_id))


@router.post(
    "/majors/{major_id}/colleges",
    summary="添加参与学院 / 指定学院负责人",
    dependencies=[Depends(require_perm("project:college:edit"))],
)
def add_major_college(
    major_id: int,
    body: MajorCollegeIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc.add_major_college(major_id, body, user)
    db.commit()
    return ok()


@router.delete(
    "/majors/{major_id}/colleges/{college_id}",
    summary="移除参与学院",
    dependencies=[Depends(require_perm("project:college:edit"))],
)
def remove_major_college(
    major_id: int,
    college_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc.remove_major_college(major_id, college_id, user)
    db.commit()
    return ok()


# ================================================================ 子项目
@router.get("/subs", summary="子项目列表")
def list_subs(
    major_id: int | None = None,
    college_id: int | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    keyword: str | None = None,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    rows, total = svc.list_subs(
        user,
        major_id=major_id,
        college_id=college_id,
        status=status,
        owner_id=owner_id,
        keyword=keyword,
        page=page_no,
        page_size=page_size,
    )
    # 补学院名
    colleges = {c.id: c.name for c in svc.list_colleges()}
    out = []
    for s in rows:
        d = _sub_out(s)
        d["college_name"] = colleges.get(s.college_id)
        out.append(d)
    return page(out, total, page_no, page_size)


@router.get("/subs/{sub_id}", summary="子项目详情")
def get_sub(
    sub_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    s = svc.get_sub(sub_id)
    assert_sub_access(user, s)
    colleges = {c.id: c.name for c in svc.list_colleges()}
    d = _sub_out(s)
    d["college_name"] = colleges.get(s.college_id)
    return ok(d)


@router.post("/subs", summary="新建子项目", dependencies=[Depends(require_perm("project:sub:edit"))])
def create_sub(
    body: SubIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 目标范围校验：非全校授权时，只能在自己管辖的大项目/学院下建子项目
    if not can_write_sub_target(user, body.major_id, body.college_id):
        raise ForbiddenError("无权在该大项目/学院下新建子项目")
    svc = ProjectService(db)
    s = svc.create_sub(body)
    db.commit()
    return ok({"id": s.id})


@router.put("/subs/{sub_id}", summary="编辑子项目", dependencies=[Depends(require_perm("project:sub:edit"))])
def update_sub(
    sub_id: int,
    body: SubUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    assert_sub_access(user, svc.get_sub(sub_id))
    # operator 必须传：结项时要做「学院负责人确认」校验（P0-4），并写变更留痕
    svc.update_sub(sub_id, body, user)
    db.commit()
    return ok()


@router.post(
    "/subs/{sub_id}/close",
    summary="结项确认",
    dependencies=[Depends(require_perm("project:sub:close"))],
)
def close_sub(
    sub_id: int,
    body: SubCloseIn | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """独立结项入口（P0-4）

    与 PUT /subs/{id} 传 status=CLOSED 走同一套校验：
    进度 100% + 至少 1 份月报 + 无未关闭预警 + 学院负责人确认。
    """
    svc = ProjectService(db)
    assert_sub_access(user, svc.get_sub(sub_id))
    s = svc.close_sub(sub_id, user, remark=(body.remark if body else None))
    db.commit()
    return ok({"id": s.id, "status": s.status})


@router.get("/subs/{sub_id}/changes", summary="子项目变更留痕")
def list_sub_changes(
    sub_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    assert_sub_access(user, svc.get_sub(sub_id))
    rows = svc.list_changes("SUB", sub_id, limit)
    return ok([_change_out(db, r) for r in rows])


@router.delete("/subs/{sub_id}", summary="删除子项目", dependencies=[Depends(require_perm("project:sub:edit"))])
def delete_sub(
    sub_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    assert_sub_access(user, svc.get_sub(sub_id))
    svc.delete_sub(sub_id)
    db.commit()
    return ok()


# ================================================================ 完整性检查
@router.get("/majors/{major_id}/completeness", summary="责任与计划完整性检查")
def check_completeness(
    major_id: int,
    persist: bool = Query(default=True),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc.get_major(major_id, user)
    result = svc.check_completeness(major_id, persist=persist)
    db.commit()
    return ok(result.model_dump())
