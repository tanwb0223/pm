"""风险登记 API"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError, ForbiddenError, NotFoundError
from app.core.response import ok, page
from app.core.scope import assert_risk_access
from app.db.session import get_db
from app.models.alert import Risk
from app.models.project import ProjSub
from app.models.system import SysUser

router = APIRouter(prefix="/api", tags=["风险登记"])

LEVEL_LABEL = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}


def _assert_risk_write_scope(
    user: CurrentUser, major_id: int, sub_id: int | None, db: Session
) -> None:
    """登记风险时的目标范围校验（编辑/关闭/删除用 assert_risk_access）"""
    if user.has_all_scope():
        return
    if major_id in user.major_ids():
        return
    if sub_id is not None:
        sub = db.get(ProjSub, sub_id)
        if sub is not None:
            if sub.college_id in user.college_ids():
                return
            if sub.owner_id == user.id:
                return
    raise ForbiddenError("无权在该项目范围内登记风险")


def _load_risk_or_404(db: Session, risk_id: int) -> Risk:
    r = db.get(Risk, risk_id)
    if r is None:
        raise NotFoundError("风险不存在")
    return r


class RiskIn(BaseModel):
    major_id: int
    sub_id: int | None = None
    level: str = Field(default="MEDIUM", pattern="^(HIGH|MEDIUM|LOW)$")
    description: str = Field(min_length=1, max_length=2000)
    owner_id: int | None = None
    # 不填则按参数 alert.AL-08.days（风险闭环默认天数）自动算
    plan_close_date: date | None = None


class RiskUpdateIn(BaseModel):
    level: str | None = Field(default=None, pattern="^(HIGH|MEDIUM|LOW)$")
    description: str | None = Field(default=None, max_length=2000)
    owner_id: int | None = None
    plan_close_date: date | None = None
    status: str | None = Field(default=None, pattern="^(OPEN|CLOSED)$")


def _out(r: Risk, users: dict[int, str] | None = None) -> dict:
    users = users or {}
    return {
        "id": r.id,
        "major_id": r.major_id,
        "sub_id": r.sub_id,
        "level": r.level,
        "level_label": LEVEL_LABEL.get(r.level, r.level),
        "description": r.description,
        "owner_id": r.owner_id,
        "owner_name": users.get(r.owner_id) if r.owner_id else None,
        "plan_close_date": r.plan_close_date,
        "actual_close_date": r.actual_close_date,
        "status": r.status,
        "overdue_days": (
            (date.today() - r.plan_close_date).days
            if r.status == "OPEN" and r.plan_close_date < date.today()
            else 0
        ),
        "created_at": r.created_at,
    }


@router.get("/risks", summary="风险列表")
def list_risks(
    major_id: int | None = None,
    sub_id: int | None = None,
    level: str | None = None,
    status: str | None = None,
    only_overdue: bool = False,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Risk)
    # 数据范围：风险挂在 major_id / sub_id 上，按大项目与学院范围收敛
    if not user.has_all_scope():
        conds = []
        major_ids = user.major_ids()
        if major_ids:
            conds.append(Risk.major_id.in_(major_ids))
        college_ids = user.college_ids()
        if college_ids:
            conds.append(
                Risk.sub_id.in_(
                    select(ProjSub.id).where(ProjSub.college_id.in_(college_ids))
                )
            )
        if any(s.scope_type in ("SELF", "CUSTOM") for s in user.scopes):
            conds.append(Risk.owner_id == user.id)
        if conds:
            stmt = stmt.where(or_(*conds))
        else:
            # 没有任何可匹配范围 → 空集（安全默认）
            stmt = stmt.where(Risk.id == -1)

    if major_id:
        stmt = stmt.where(Risk.major_id == major_id)
    if sub_id:
        stmt = stmt.where(Risk.sub_id == sub_id)
    if level:
        stmt = stmt.where(Risk.level == level)
    if status:
        stmt = stmt.where(Risk.status == status)
    if only_overdue:
        stmt = stmt.where(Risk.status == "OPEN", Risk.plan_close_date < date.today())

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Risk.status, Risk.plan_close_date)
            .offset((page_no - 1) * page_size)
            .limit(page_size)
        )
    )
    users = {u.id: u.name for u in db.scalars(select(SysUser))}
    return page([_out(r, users) for r in rows], total, page_no, page_size)


@router.post("/risks", summary="登记风险", dependencies=[Depends(require_perm("risk:edit"))])
def create_risk(
    body: RiskIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.sub_id and db.get(ProjSub, body.sub_id) is None:
        raise BusinessError("子项目不存在")
    # 数据范围：只能在自己管辖范围内登记风险
    _assert_risk_write_scope(user, body.major_id, body.sub_id, db)
    data = body.model_dump()
    if data.get("plan_close_date") is None:
        # 参数 alert.AL-08.days 决定默认闭环天数（此前该参数改了没有任何效果）
        from app.services.config_service import ConfigService

        days = max(1, ConfigService(db).get_int("alert.AL-08.days", 7))
        data["plan_close_date"] = date.today() + timedelta(days=days)
    r = Risk(**data)
    db.add(r)
    db.commit()
    return ok({"id": r.id})


@router.put("/risks/{risk_id}", summary="更新风险", dependencies=[Depends(require_perm("risk:edit"))])
def update_risk(
    risk_id: int,
    body: RiskUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _load_risk_or_404(db, risk_id)
    assert_risk_access(user, r, db.get(ProjSub, r.sub_id) if r.sub_id else None)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    if data.get("status") == "CLOSED" and not r.actual_close_date:
        r.actual_close_date = date.today()
    db.commit()
    return ok()


@router.post("/risks/{risk_id}/close", summary="闭环风险", dependencies=[Depends(require_perm("risk:edit"))])
def close_risk(
    risk_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _load_risk_or_404(db, risk_id)
    assert_risk_access(user, r, db.get(ProjSub, r.sub_id) if r.sub_id else None)
    if r.status == "CLOSED":
        raise BusinessError("风险已闭环")
    r.status = "CLOSED"
    r.actual_close_date = date.today()
    db.commit()
    return ok()


@router.delete("/risks/{risk_id}", summary="删除风险", dependencies=[Depends(require_perm("risk:edit"))])
def delete_risk(
    risk_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _load_risk_or_404(db, risk_id)
    assert_risk_access(user, r, db.get(ProjSub, r.sub_id) if r.sub_id else None)
    db.delete(r)
    db.commit()
    return ok()
