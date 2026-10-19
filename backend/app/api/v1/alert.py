"""预警 API：扫描、列表、处置、升级、统计、我的督办"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.response import ok, page
from app.db.session import get_db
from app.services.alert_service import AlertService

router = APIRouter(prefix="/api", tags=["预警管理"])


class ScanIn(BaseModel):
    day: date | None = None


class CloseIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class IgnoreIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class TransferIn(BaseModel):
    to_user_id: int


def _out(a) -> dict:
    return {
        "id": a.id,
        "alert_type": a.alert_type,
        "level": a.level,
        "status": a.status,
        "sub_id": a.sub_id,
        "sub_name": a.sub.name if a.sub else None,
        "major_id": a.major_id,
        "college_id": a.college_id,
        "trigger_at": a.trigger_at,
        "deadline_at": a.deadline_at,
        "claimed_by": a.claimed_by,
        "claimed_at": a.claimed_at,
        "closed_at": a.closed_at,
        "close_reason": a.close_reason,
        "escalated_at": a.escalated_at,
        "merged_count": a.merged_count,
        "dedup_key": a.dedup_key,
    }


# ================================================================ 扫描
@router.post(
    "/alerts/scan",
    summary="执行预警扫描（定时任务也会跑）",
    dependencies=[Depends(require_perm("system:config"))],
)
def scan(body: ScanIn, db: Session = Depends(get_db)):
    svc = AlertService(db)
    result = svc.scan(body.day)
    db.commit()
    return ok(result)


@router.post(
    "/alerts/escalate",
    summary="执行超时升级",
    dependencies=[Depends(require_perm("system:config"))],
)
def escalate(db: Session = Depends(get_db)):
    svc = AlertService(db)
    result = svc.escalate()
    db.commit()
    return ok(result)


# ================================================================ 查询
@router.get("/alerts", summary="预警列表")
def list_alerts(
    status: str | None = None,
    level: str | None = None,
    alert_type: str | None = None,
    major_id: int | None = None,
    college_id: int | None = None,
    only_mine: bool = False,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AlertService(db)
    rows, total = svc.list_alerts(
        user,
        status=status,
        level=level,
        alert_type=alert_type,
        major_id=major_id,
        college_id=college_id,
        only_mine=only_mine,
        page=page_no,
        page_size=page_size,
    )
    return page([_out(a) for a in rows], total, page_no, page_size)


@router.get("/alerts/grouped", summary="预警折叠聚合（按大项目→学院）")
def grouped(
    user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    svc = AlertService(db)
    return ok(svc.grouped(user))


@router.get("/alerts/stats", summary="预警统计与按时处置率")
def stats(
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AlertService(db)
    return ok(svc.stats(user, days))


@router.get("/alerts/{alert_id}", summary="预警详情")
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    svc = AlertService(db)
    a = svc.get(alert_id)
    d = _out(a)
    d["actions"] = [
        {
            "action": x.action,
            "operator_id": x.operator_id,
            "reason": x.reason,
            "created_at": x.created_at,
        }
        for x in svc.actions(a.id)
    ]
    return ok(d)


# ================================================================ 处置
@router.post("/alerts/{alert_id}/claim", summary="认领预警")
def claim(
    alert_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AlertService(db)
    svc.claim(alert_id, user)
    db.commit()
    return ok()


@router.post("/alerts/{alert_id}/close", summary="关闭预警")
def close(
    alert_id: int,
    body: CloseIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AlertService(db)
    svc.close(alert_id, user, body.reason)
    db.commit()
    return ok()


@router.post("/alerts/{alert_id}/ignore", summary="忽略预警（必须填理由）")
def ignore(
    alert_id: int,
    body: IgnoreIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AlertService(db)
    svc.ignore(alert_id, user, body.reason)
    db.commit()
    return ok()


@router.post("/alerts/{alert_id}/transfer", summary="转交预警")
def transfer(
    alert_id: int,
    body: TransferIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AlertService(db)
    svc.transfer(alert_id, user, body.to_user_id)
    db.commit()
    return ok()


# ================================================================ 我的督办
@router.get("/dashboard/todo", summary="我的督办（三个数字 + 待办）")
def my_todo(
    user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    svc = AlertService(db)
    return ok(svc.my_todo(user))
