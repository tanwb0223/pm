"""导出 API：项目台账、进度报表、报表材料"""
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.response import ok
from app.db.session import get_db
from app.services.export_service import (
    MAJOR_FIELDS,
    PROGRESS_FIELDS,
    REPORT_FIELDS,
    build_excel,
    build_pdf,
)
from app.services.progress_service import ProgressService
from app.services.project_service import ProjectService
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/export", tags=["数据导出"])

STATUS_LABEL = {
    "ACTIVE": "在建",
    "CLOSED": "已结项",
    "ABORTED": "已中止",
    "NOT_STARTED": "未开始",
    "IN_PROGRESS": "进行中",
}
TYPE_LABEL = {"DAILY": "日报", "WEEKLY": "周报", "MONTHLY": "月报"}


def _filename(base: str, ext: str) -> str:
    return f"{base}_{datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"


def _download_headers(name: str) -> dict[str, str]:
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Access-Control-Expose-Headers": "Content-Disposition",
    }


def _pick_fields(all_fields: list[tuple[str, str]], wanted: str | None) -> list[tuple[str, str]]:
    """字段可勾选：逗号分隔的 key 列表"""
    if not wanted:
        return all_fields
    keys = {k.strip() for k in wanted.split(",") if k.strip()}
    picked = [f for f in all_fields if f[0] in keys]
    return picked or all_fields


# ================================================================ 项目台账
@router.get("/majors", summary="导出项目台账（Excel）")
def export_majors(
    fmt: str = Query(default="xlsx", pattern="^(xlsx|pdf)$"),
    fields: str | None = Query(default=None, description="要导出的字段，逗号分隔"),
    keyword: str | None = None,
    type_: str | None = Query(default=None, alias="type"),
    status: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    proj = ProjectService(db)
    rows, _ = proj.list_majors(
        user, keyword=keyword, type_=type_, status=status, page=1, page_size=10000
    )
    from app.services.aggregation_service import AggregationService

    agg = AggregationService(db)
    data = []
    for m in rows:
        cols = proj.list_major_colleges(m.id)
        incomplete = (m.incomplete_reason or {}).get("items") or []
        data.append(
            {
                "name": m.name,
                "type": m.type,
                "partner_company": m.partner_company or "",
                "build_period": m.build_period or "",
                "owner_name": m.owner.name if m.owner else "",
                "status_label": STATUS_LABEL.get(m.status, m.status),
                "original_start": m.original_start,
                "original_end": m.original_end,
                "college_count": len(cols),
                "sub_count": sum(c["sub_count"] for c in cols),
                # 当前进度：按权重汇总实时计算（incomplete_reason 里没有这个键）
                "progress": agg.major_progress(m.id),
                "incomplete": "、".join(x.get("label", "") for x in incomplete) or "完整",
            }
        )

    picked = _pick_fields(MAJOR_FIELDS, fields)
    title = "产教融合项目台账"
    if fmt == "pdf":
        content = build_pdf(title, picked, data)
        return Response(
            content=content,
            media_type="application/pdf",
            headers=_download_headers(_filename("项目台账", "pdf")),
        )
    content = build_excel(title, picked, data, sheet_name="项目台账")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(_filename("项目台账", "xlsx")),
    )


# ================================================================ 进度报表
@router.get("/progress", summary="导出进度报表（Excel/PDF）")
def export_progress(
    fmt: str = Query(default="xlsx", pattern="^(xlsx|pdf)$"),
    fields: str | None = None,
    major_id: int | None = None,
    college_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    only_abnormal: bool = False,
    sort: str = Query(
        default="deviation",
        pattern="^(deviation|update|progress)$",
        description="与页面一致的排序方式",
    ),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ProgressService(db)
    rows, _ = svc.table(
        user,
        major_id=major_id,
        college_id=college_id,
        status=status,
        keyword=keyword,
        only_abnormal=only_abnormal,
        page=1,
        page_size=10000,
        sort=sort,
    )
    data = []
    for r in rows:
        data.append(
            {
                **r,
                "status_label": STATUS_LABEL.get(r["status"], r["status"]),
                "time_progress": r["time_progress"] if r["time_progress"] is not None else "—",
                "deviation": r["deviation"] if r["deviation"] is not None else "—",
                "last_update_at": r["last_update_at"],
                "days_since_update": r["days_since_update"] if r["days_since_update"] is not None else "",
            }
        )

    picked = _pick_fields(PROGRESS_FIELDS, fields)
    title = "项目进度报表"
    subtitle = f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　共 {len(data)} 条"

    if fmt == "pdf":
        content = build_pdf(title, picked, data, subtitle=subtitle)
        return Response(
            content=content,
            media_type="application/pdf",
            headers=_download_headers(_filename("进度报表", "pdf")),
        )
    content = build_excel(title, picked, data, sheet_name="进度明细")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(_filename("进度报表", "xlsx")),
    )


# ================================================================ 报表材料
@router.get("/reports", summary="导出报表材料（Excel）")
def export_reports(
    fmt: str = Query(default="xlsx", pattern="^(xlsx|pdf)$"),
    fields: str | None = None,
    major_id: int | None = None,
    type_: str | None = Query(default=None, alias="type"),
    start: date | None = None,
    end: date | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ReportService(db)
    rows, _ = svc.list_reports(
        user,
        rtype=type_,
        major_id=major_id,
        submitted_from=start,
        submitted_to=end,
        page=1,
        page_size=10000,
    )
    data = []
    for r in rows:
        content = r.content or {}
        summary = content.get("summary") or content.get("today") or content.get("completed") or ""
        if isinstance(summary, str) and len(summary) > 200:
            summary = summary[:200] + "…"
        data.append(
            {
                "sub_name": r.sub.name if r.sub else "",
                "type_label": TYPE_LABEL.get(r.type, r.type),
                "period_start": r.period_start,
                "period_end": r.period_end,
                "progress": r.progress if r.progress is not None else "",
                "status_label": {"DRAFT": "草稿", "SUBMITTED": "已提交", "VOIDED": "已作废"}.get(
                    r.status, r.status
                ),
                "submitted_at": r.submitted_at,
                "correct_count": r.correct_count,
                "summary": summary,
            }
        )

    picked = _pick_fields(REPORT_FIELDS, fields)
    title = "报表材料汇编"
    subtitle = f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　共 {len(data)} 条"

    if fmt == "pdf":
        content = build_pdf(title, picked, data, subtitle=subtitle)
        return Response(
            content=content,
            media_type="application/pdf",
            headers=_download_headers(_filename("报表材料", "pdf")),
        )
    content = build_excel(title, picked, data, sheet_name="报表材料")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(_filename("报表材料", "xlsx")),
    )


# ================================================================ 字段清单
@router.get("/fields", summary="可导出字段清单（供前端勾选）")
def export_fields():
    return ok(
        {
            "major": [{"key": k, "label": v} for k, v in MAJOR_FIELDS],
            "progress": [{"key": k, "label": v} for k, v in PROGRESS_FIELDS],
            "report": [{"key": k, "label": v} for k, v in REPORT_FIELDS],
        }
    )
