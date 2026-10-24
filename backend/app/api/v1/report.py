"""报表 API：填报、更正、作废、周报初稿、待填清单、及时率"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.response import ok, page
from app.core.scope import apply_scope, assert_report_access, assert_sub_access
from app.db.session import get_db
from app.models.project import ProjMajor, ProjSub
from app.services.project_service import ProjectService
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
    # 代填：操作人不是子项目负责人时，显式指定「替谁交」（必须等于子项目负责人）
    on_behalf_of: int | None = Field(
        default=None, description="代填对象（子项目负责人 id）；本人填报时留空"
    )


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
        # 代填对象：前端据此显示「由 XX 代填」，及时率也按此归属
        "on_behalf_of": r.on_behalf_of,
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
        body.sub_id,
        body.type,
        body.content,
        body.progress,
        user,
        body.period_start,
        on_behalf_of=body.on_behalf_of,
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
    # 数据范围：只能给自己有权访问的子项目生成初稿
    assert_sub_access(user, ProjectService(db).get_sub(body.sub_id))
    return ok(svc.generate_weekly_draft(body.sub_id, body.week_start))


# ---------------------------------------------------------------- 待填
@router.get("/reports/pending", summary="待填报表清单")
def pending_reports(
    day: date | None = None,
    include_upcoming: bool = Query(
        default=True, description="是否包含尚未到截止、但已进入填报窗口的报表"
    ),
    upcoming_days: int = Query(default=3, ge=0, le=30, description="提前提醒天数"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回两类：OVERDUE 已逾期、UPCOMING 临近截止

    早期只返回已逾期的，教师在截止前看不到提醒，容易拖到逾期。
    """
    svc = ReportService(db)
    return ok(svc.pending_list(user, day, include_upcoming, upcoming_days))


# ---------------------------------------------------------------- 补填 / 更正放行
class GraceIn(BaseModel):
    sub_id: int
    type: str = Field(pattern="^(DAILY|WEEKLY|MONTHLY)$")
    period_start: date | None = Field(
        default=None, description="为空表示放行该类型所有历史周期"
    )
    reason: str = Field(min_length=1, max_length=500, description="开启理由")
    days: int = Field(default=7, ge=1, le=90, description="放行有效天数")
    # 放行类型：BACKFILL=逾期补填（默认），CORRECT=超期更正
    kind: str = Field(
        default="BACKFILL",
        pattern="^(BACKFILL|CORRECT)$",
        description="BACKFILL 补填放行 / CORRECT 更正放行",
    )


@router.get("/reports/graces", summary="补填 / 更正放行记录")
def list_graces(
    sub_id: int | None = None,
    kind: str | None = Query(default=None, pattern="^(BACKFILL|CORRECT)$"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ok(ReportService(db).list_graces(user, sub_id, kind))


@router.post(
    "/reports/graces",
    summary="开启逾期补填 / 超期更正",
    dependencies=[Depends(require_perm("report:grant"))],
)
def grant_grace(
    body: GraceIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """文档 4.3 / 3.4：月报超过 3 天需学院负责人开启补填；更正超期同理

    放行有有效期（默认 7 天），到期自动失效；kind 区分补填与更正。
    """
    svc = ReportService(db)
    result = svc.grant_grace(
        body.sub_id,
        body.type,
        period_start=body.period_start,
        reason=body.reason,
        days=body.days,
        operator=user,
        kind=body.kind,
    )
    db.commit()
    return ok(result)


@router.delete(
    "/reports/graces/{grace_id}",
    summary="撤销补填放行",
    dependencies=[Depends(require_perm("report:grant"))],
)
def revoke_grace(
    grace_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    svc.revoke_grace(grace_id, user)
    db.commit()
    return ok()


# ---------------------------------------------------------------- 及时率
@router.get("/reports/timeliness", summary="报表及时率统计")
def timeliness(
    start: date,
    end: date,
    type: str | None = Query(default=None, pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    major_id: int | None = None,
    owner_id: int | None = Query(default=None, description="按归属人筛选（代填算被代填人）"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    data = svc.timeliness(
        user, start=start, end=end, rtype=type, major_id=major_id, owner_id=owner_id
    )
    # detail_all 仅内部使用，不返回给前端
    data.pop("detail_all", None)
    return ok(data)


@router.get("/reports/timeliness/detail", summary="报表及时率明细（分页）")
def timeliness_detail(
    start: date,
    end: date,
    type: str | None = Query(default=None, pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    major_id: int | None = None,
    owner_id: int | None = None,
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
        owner_id=owner_id,
        page=page_no,
        page_size=page_size,
    )
    return page(rows, total, page_no, page_size)


# ---------------------------------------------------------------- 填报选项
@router.get("/reports/options", summary="填报选项（当前启用的报表类型）")
def report_options(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """供填报页使用：哪些报表类型是启用的、哪些项目单独开了日报

    日报由「全局参数 report.daily.enabled」或「项目 ProjMajor.daily_report_enabled」
    控制（需求 6.1 第 5 项：默认关、试点项目打开）。这个接口只读、登录即可访问，
    避免普通填报人为了读一个开关而去申请 system:config 权限。
    """
    svc = ReportService(db)
    daily_enabled = svc.global_daily_enabled()

    # 按当前用户可选项目返回「单独开了日报」的项目 id 列表，
    # 前端据此决定给哪些项目显示日报入口（不再只看全局开关）。
    sub_stmt = select(ProjSub.id).where(ProjSub.deleted_at.is_(None))
    sub_stmt = apply_scope(sub_stmt, user, ProjSub)
    visible_sub_ids = set(svc.db.scalars(sub_stmt))
    major_ids: set[int] = set()
    if visible_sub_ids:
        for sub in svc.db.scalars(
            select(ProjSub).where(ProjSub.id.in_(visible_sub_ids))
        ):
            if sub.major_id is not None:
                major_ids.add(sub.major_id)
    daily_major_ids: list[int] = []
    if major_ids:
        for m in svc.db.scalars(select(ProjMajor).where(ProjMajor.id.in_(major_ids))):
            if m.daily_report_enabled is True:
                daily_major_ids.append(m.id)

    types = ["WEEKLY", "MONTHLY"]
    if daily_enabled or daily_major_ids:
        types.insert(0, "DAILY")
    return ok(
        {
            "types": types,
            "daily_enabled": daily_enabled,
            # 单独开启日报的项目（None 表示跟随全局，不在此列）
            "daily_enabled_major_ids": sorted(daily_major_ids),
        }
    )


# ---------------------------------------------------------------- 周期辅助
@router.get("/reports/period", summary="查询某类型某日所属周期与截止时间")
def period_info(
    type: str = Query(pattern="^(DAILY|WEEKLY|MONTHLY)$"),
    day: date | None = None,
    user: CurrentUser = Depends(get_current_user),
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


@router.get("/reports/{report_id}/correctable", summary="该报表当前是否可更正")
def report_correctable(
    report_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """供前端决定是否显示「更正」按钮（需求 3.4）

    返回 {can_correct, reason, correct_days, days_left}：
    - 提交后 correct_days 天内可自行更正（days_left 为剩余天数）
    - 超期需学院负责人开启 CORRECT 放行
    """
    return ok(ReportService(db).correctable(report_id, user))


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
