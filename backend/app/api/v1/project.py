"""项目台账 API：学院、大项目、学院分工、子项目、完整性检查"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError
from app.core.response import ok, page
from app.db.session import get_db
from app.schemas.project import (
    CollegeIn,
    MajorCollegeIn,
    MajorIn,
    MajorUpdateIn,
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
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=200),
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
def create_major(body: MajorIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    m = svc.create_major(body)
    db.commit()
    return ok({"id": m.id})


@router.put("/majors/{major_id}", summary="编辑大项目", dependencies=[Depends(require_perm("project:major:edit"))])
def update_major(major_id: int, body: MajorUpdateIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.update_major(major_id, body)
    db.commit()
    return ok()


@router.delete("/majors/{major_id}", summary="删除大项目", dependencies=[Depends(require_perm("project:major:edit"))])
def delete_major(major_id: int, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.delete_major(major_id)
    db.commit()
    return ok()


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
def add_major_college(major_id: int, body: MajorCollegeIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.add_major_college(major_id, body)
    db.commit()
    return ok()


@router.delete(
    "/majors/{major_id}/colleges/{college_id}",
    summary="移除参与学院",
    dependencies=[Depends(require_perm("project:college:edit"))],
)
def remove_major_college(major_id: int, college_id: int, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.remove_major_college(major_id, college_id)
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
    page_size: int = Query(default=20, ge=1, le=200),
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
def get_sub(sub_id: int, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    s = svc.get_sub(sub_id)
    colleges = {c.id: c.name for c in svc.list_colleges()}
    d = _sub_out(s)
    d["college_name"] = colleges.get(s.college_id)
    return ok(d)


@router.post("/subs", summary="新建子项目", dependencies=[Depends(require_perm("project:sub:edit"))])
def create_sub(body: SubIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    s = svc.create_sub(body)
    db.commit()
    return ok({"id": s.id})


@router.put("/subs/{sub_id}", summary="编辑子项目", dependencies=[Depends(require_perm("project:sub:edit"))])
def update_sub(sub_id: int, body: SubUpdateIn, db: Session = Depends(get_db)):
    svc = ProjectService(db)
    svc.update_sub(sub_id, body)
    db.commit()
    return ok()


@router.delete("/subs/{sub_id}", summary="删除子项目", dependencies=[Depends(require_perm("project:sub:edit"))])
def delete_sub(sub_id: int, db: Session = Depends(get_db)):
    svc = ProjectService(db)
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
