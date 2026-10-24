"""导出 API：项目台账、进度报表、报表材料"""
from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
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

logger = logging.getLogger(__name__)

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


def _mark_export(request: Request, rows: list[dict], picked: list[tuple[str, str]]) -> None:
    """把本次导出的条数与字段写入 request.state，供审计中间件记录

    背景（审计 P1-8）：导出是敏感操作，但审计日志只记了「发生了一次 GET」，
    没有「导出了多少条、哪些字段」。这里由接口侧提供元数据，中间件读取后
    并入 AuditLog.detail。GET 导出本来不进审计，中间件侧由 D 组加入白名单。
    """
    request.state.export_meta = {
        "count": len(rows),
        "fields": [k for k, _ in picked],
    }


# ================================================================ 项目台账
@router.get(
    "/majors",
    summary="导出项目台账（Excel）",
    # 附录二：数据导出权限按角色区分——填报人=「—」。
    # 早期三个导出接口只要求登录，export:ledger / export:progress 成了死权限点，
    # 任何登录用户（含只读的填报人）都能导全量台账。
    dependencies=[Depends(require_perm("export:ledger"))],
)
def export_majors(
    request: Request,
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
    _mark_export(request, data, picked)
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
@router.get(
    "/progress",
    summary="导出进度报表（Excel/PDF）",
    dependencies=[Depends(require_perm("export:progress"))],
)
def export_progress(
    request: Request,
    fmt: str = Query(default="xlsx", pattern="^(xlsx|pdf)$"),
    fields: str | None = None,
    major_id: int | None = None,
    college_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    only_abnormal: bool = False,
    with_timeliness: bool = Query(
        default=True, description="是否附带「报表及时率%」列（需求 3.6）"
    ),
    start: date | None = Query(default=None, description="及时率统计起始日（默认本年年初）"),
    end: date | None = Query(default=None, description="及时率统计截止日（默认今天）"),
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

    # 【P1-10】报表及时率：一次算出统计期内所有期次明细，再按 sub_id 聚合成
    # {sub_id: 及时率%}，避免每行调一次 timeliness（那会变成 O(行数×期次)）。
    timeliness_map: dict[int, float | None] = {}
    if with_timeliness and rows:
        try:
            from app.services.report_service import ReportService

            tl_start = start or date(date.today().year, 1, 1)
            tl_end = end or date.today()
            detail = ReportService(db).timeliness(
                user, start=tl_start, end=tl_end
            ).get("detail_all", [])
            agg: dict[int, list[int]] = {}
            for item in detail:
                bucket = agg.setdefault(item["sub_id"], [0, 0])  # [按期, 应报]
                bucket[1] += 1
                if item.get("state") == "ON_TIME":
                    bucket[0] += 1
            for sub_id, (ok_cnt, total_cnt) in agg.items():
                timeliness_map[sub_id] = (
                    round(ok_cnt / total_cnt * 100, 1) if total_cnt else None
                )
        except Exception:  # 及时率算不出来不能拖垮导出
            logger.warning("导出进度报表：报表及时率计算失败，降级为空列", exc_info=True)

    data = []
    for r in rows:
        row = {
            **r,
            "status_label": STATUS_LABEL.get(r["status"], r["status"]),
            "time_progress": r["time_progress"] if r["time_progress"] is not None else "—",
            "deviation": r["deviation"] if r["deviation"] is not None else "—",
            "last_update_at": r["last_update_at"],
            "days_since_update": r["days_since_update"] if r["days_since_update"] is not None else "",
        }
        if with_timeliness:
            row["report_timeliness"] = (
                timeliness_map.get(r["id"]) if timeliness_map.get(r["id"]) is not None else "—"
            )
        data.append(row)

    picked = _pick_fields(PROGRESS_FIELDS, fields)
    # 未请求及时率列时不要导出空列（避免「勾了但没数据」的误解）
    if not with_timeliness:
        picked = [f for f in picked if f[0] != "report_timeliness"]
    _mark_export(request, data, picked)
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
@router.get(
    "/reports",
    summary="导出报表材料（Excel）",
    dependencies=[Depends(require_perm("export:progress"))],
)
def export_reports(
    request: Request,
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
    _mark_export(request, data, picked)
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
def export_fields(user: CurrentUser = Depends(get_current_user)):
    return ok(
        {
            "major": [{"key": k, "label": v} for k, v in MAJOR_FIELDS],
            "progress": [{"key": k, "label": v} for k, v in PROGRESS_FIELDS],
            "report": [{"key": k, "label": v} for k, v in REPORT_FIELDS],
        }
    )
