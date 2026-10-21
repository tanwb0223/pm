"""报表 API：填报、更正、作废、周报初稿、待填清单、及时率"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.response import ok, page
from app.core.scope import assert_report_access
from app.db.session import get_db
from app.services.report_service import ReportService

router = APIRouter(prefix="/api", tags=["报表管理"])


# ---------------------------------------------------------------- 请求体
class DailyContent(BaseModel):
    today: str = Field(description="今天做了什么")
    tomorrow: str | None = Field(default=None, description="明天做什么")
    issues: str | None = Field(default=None, description="有什么问题")


class WeeklyContent(BaseModel):
    summary: str = Field(description="本周进展")
    next_week: str | None = Field(default=None, description="下周计划")
    issues: str | None = Field(default=None, description="问题与风险")


class MonthlyContent(BaseModel):
    completed: str = Field(description="本月完成")
    next_month: str | None = Field(default=None, description="下月计划")
    issues: str | None = Field(default=None, description="问题与风险")
    suggestions: str | None = Field(default=None, description="意见建议")


class ReportIn(BaseModel):
    sub_id: int
    type: str = Field(pattern="^(DAILY|WEEKLY|MONTHLY)$")
    period_start: date | None = Field(default=None, description="所属周期（不填用今天）")
    progress: int | None = Field(default=None, ge=0, le=100, description="顺带更新进度")
    content: dict[str, Any] = Field(description="按报表类型填不同字段")


class VoidIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class WeeklyDraftIn(BaseModel):
    sub_id: int
    week_start: date | None = None


def _out(r) -> dict:
    return {
        "id": r.id,
        "sub_id": r.sub_id,
        "sub_name": r.sub.name if r.sub else None,
        "type": r.type,
        "period_start": r.period_start,
        "period_end": r.period_end,
        "progress": r.progress,
        "content": r.content,
        "status": r.status,
        "submitted_at": r.submitted_at,
        "submitted_by": r.submitted_by,
        "correct_count": r.correct_count,
        "void_reason": r.void_reason,
        "voided_at": r.voided_at,
    }


# ---------------------------------------------------------------- 填报
@router.post(
    "/reports",
    summary="提交报表（日报/周报/月报）",
    dependencies=[Depends(require_perm("report:fill"))],
)
def submit_report(
    body: ReportIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    rep = svc.submit(
        body.sub_id, body.type, body.content, body.progress, user, body.period_start
    )
    db.commit()
    return ok({"id": rep.id, "status": rep.status})


@router.get("/reports", summary="报表列表")
def list_reports(
    type: str | None = Query(default=None, pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    sub_id: int | None = None,
    major_id: int | None = None,
    status: str | None = None,
    submitted_from: date | None = None,
    submitted_to: date | None = None,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    rows, total = svc.list_reports(
        user,
        rtype=type,
        sub_id=sub_id,
        major_id=major_id,
        status=status,
        submitted_from=submitted_from,
        submitted_to=submitted_to,
        page=page_no,
        page_size=page_size,
    )
    return page([_out(r) for r in rows], total, page_no, page_size)


# ---------------------------------------------------------------- 周报初稿
@router.post("/reports/weekly-draft", summary="生成周报初稿")
def weekly_draft(
    body: WeeklyDraftIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    return ok(svc.generate_weekly_draft(body.sub_id, body.week_start))


# ---------------------------------------------------------------- 待填
@router.get("/reports/pending", summary="待填报表清单")
def pending_reports(
    day: date | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    return ok(svc.pending_list(user, day))


# ---------------------------------------------------------------- 及时率
@router.get("/reports/timeliness", summary="报表及时率统计")
def timeliness(
    start: date,
    end: date,
    type: str | None = Query(default=None, pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    major_id: int | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    data = svc.timeliness(user, start=start, end=end, rtype=type, major_id=major_id)
    # detail_all 仅内部使用，不返回给前端
    data.pop("detail_all", None)
    return ok(data)


@router.get("/reports/timeliness/detail", summary="报表及时率明细（分页）")
def timeliness_detail(
    start: date,
    end: date,
    type: str | None = Query(default=None, pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    major_id: int | None = None,
    state: str | None = Query(default=None, pattern="^(MISSING|LATE|ON_TIME)$"),
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    rows, total = svc.timeliness_detail(
        user,
        start=start,
        end=end,
        rtype=type,
        major_id=major_id,
        state=state,
        page=page_no,
        page_size=page_size,
    )
    return page(rows, total, page_no, page_size)


# ---------------------------------------------------------------- 周期辅助
@router.get("/reports/period", summary="查询某类型某日所属周期与截止时间")
def period_info(
    type: str = Query(pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    day: date | None = None,
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    d = day or date.today()
    p_start, p_end = svc.period_of(type, d)
    deadline = svc.deadline_of(type, p_start)
    return ok(
        {
            "type": type,
            "period_start": p_start,
            "period_end": p_end,
            "deadline": deadline,
            "can_submit": deadline is not None,
        }
    )


@router.get("/reports/{report_id}", summary="报表详情")
def get_report(
    report_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    rep = svc.get(report_id)
    assert_report_access(user, rep)
    return ok(_out(rep))


# ---------------------------------------------------------------- 作废
@router.post(
    "/reports/{report_id}/void",
    summary="作废报表（仅管理员，须填理由）",
    dependencies=[Depends(require_perm("report:void"))],
)
def void_report(
    report_id: int,
    body: VoidIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    rep = svc.void(report_id, user, body.reason)
    db.commit()
    return ok({"id": rep.id, "status": rep.status})
