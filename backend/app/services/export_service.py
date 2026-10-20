"""导出服务：Excel（openpyxl）+ PDF（reportlab）

PDF 中文的关键：必须显式注册 TTF 字体（reportlab 内置字体不含中文）。
字体查找顺序：项目 fonts 目录 → Windows 系统字体 → Linux 常见路径。
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

# 候选字体（按优先级）
_FONT_CANDIDATES = [
    # 项目自带优先
    ("pm-fonts", Path(__file__).resolve().parent.parent.parent / "fonts" / "simhei.ttf"),
    ("pm-fonts", Path(__file__).resolve().parent.parent.parent / "fonts" / "simsun.ttc"),
    # Windows
    ("simhei", Path("C:/Windows/Fonts/simhei.ttf")),
    ("simsun", Path("C:/Windows/Fonts/simsun.ttc")),
    ("msyh", Path("C:/Windows/Fonts/msyh.ttc")),
    # Linux
    ("noto", Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")),
    # Debian / Ubuntu
    ("wqy", Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")),
    # 常见 Linux 发行版的中文字体安装位置
    ("wqy", Path("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc")),
    ("wqy", Path("/usr/share/fonts/wqy/wqy-zenhei.ttc")),
]

_font_registered: str | None = None


def _ensure_cjk_font() -> str:
    """注册中文字体，返回字体名"""
    global _font_registered
    if _font_registered:
        return _font_registered

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, path in _FONT_CANDIDATES:
        if not path.exists():
            continue
        try:
            # .ttc 需要指定子字体索引
            if str(path).lower().endswith(".ttc"):
                pdfmetrics.registerFont(TTFont("PMCJK", str(path), subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont("PMCJK", str(path)))
            _font_registered = "PMCJK"
            logger.info("PDF 中文字体已注册：%s", path)
            return _font_registered
        except Exception:
            logger.warning("字体注册失败：%s", path, exc_info=True)
            continue

    raise RuntimeError(
        "找不到可用的中文字体。请把 simhei.ttf 或 NotoSansCJK 放到 backend/fonts/ 目录"
    )


# ================================================================ Excel
EXCEL_HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
EXCEL_HEADER_FONT = Font(name="宋体", size=10, bold=True, color="FFFFFF")
EXCEL_BODY_FONT = Font(name="宋体", size=10)
EXCEL_THIN = Side(style="thin", color="D0D7DE")
EXCEL_BORDER = Border(left=EXCEL_THIN, right=EXCEL_THIN, top=EXCEL_THIN, bottom=EXCEL_THIN)


def build_excel(
    title: str,
    columns: Sequence[tuple[str, str]],   # [(key, 表头), ...]
    rows: Sequence[dict],
    *,
    widths: Sequence[int] | None = None,
    sheet_name: str = "Sheet1",
) -> bytes:
    """通用 Excel 生成"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    # 标题行
    ws.append([title])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    title_cell = ws.cell(row=1, column=1)
    title_cell.font = Font(name="宋体", size=14, bold=True, color="1E3A5F")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # 导出时间
    ws.append([f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"])
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
    ws.cell(row=2, column=1).font = Font(name="宋体", size=9, color="666666")
    ws.cell(row=2, column=1).alignment = Alignment(horizontal="right")

    # 表头
    header_row = 3
    for idx, (_key, label) in enumerate(columns, start=1):
        cell = ws.cell(row=header_row, column=idx, value=label)
        cell.fill = EXCEL_HEADER_FILL
        cell.font = EXCEL_HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = EXCEL_BORDER
    ws.row_dimensions[header_row].height = 24

    # 数据
    for r_idx, row in enumerate(rows, start=header_row + 1):
        for c_idx, (key, _label) in enumerate(columns, start=1):
            value = row.get(key)
            if isinstance(value, (date, datetime)):
                value = value.strftime("%Y-%m-%d")
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            cell.font = EXCEL_BODY_FONT
            cell.border = EXCEL_BORDER
            cell.alignment = Alignment(
                vertical="center",
                horizontal="center" if isinstance(value, (int, float)) else "left",
                wrap_text=False,
            )

    # 列宽
    for idx, (_key, label) in enumerate(columns, start=1):
        w = widths[idx - 1] if widths and idx - 1 < len(widths) else max(12, len(label) * 2 + 4)
        ws.column_dimensions[get_column_letter(idx)].width = w

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(columns))}{header_row + len(rows)}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ================================================================ PDF
def build_pdf(
    title: str,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[dict],
    *,
    subtitle: str = "",
    col_widths: Sequence[float] | None = None,
) -> bytes:
    """通用 PDF 表格（横向 A4）"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font = _ensure_cjk_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PMTitle", parent=styles["Title"], fontName=font, fontSize=16, leading=22, spaceAfter=6
    )
    sub_style = ParagraphStyle(
        "PMSub", parent=styles["Normal"], fontName=font, fontSize=9,
        textColor=colors.HexColor("#666666"), alignment=2, spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        "PMCell", parent=styles["Normal"], fontName=font, fontSize=8, leading=11
    )
    head_style = ParagraphStyle(
        "PMHead", parent=cell_style, textColor=colors.white, fontSize=8.5
    )

    story: list[Any] = [Paragraph(title, title_style)]
    if subtitle:
        story.append(Paragraph(subtitle, sub_style))

    # 表头
    data: list[list[Any]] = [[Paragraph(label, head_style) for _k, label in columns]]
    for row in rows:
        line = []
        for key, _label in columns:
            v = row.get(key)
            if isinstance(v, (date, datetime)):
                v = v.strftime("%Y-%m-%d")
            line.append(Paragraph("" if v is None else str(v), cell_style))
        data.append(line)

    total_width = (297 - 24) * mm  # A4 横向减去边距
    if col_widths:
        cw = col_widths
    else:
        cw = [total_width / len(columns)] * len(columns)

    table = Table(data, colWidths=cw, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D7DE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"共 {len(rows)} 条　导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            ParagraphStyle("PMFoot", parent=sub_style, alignment=1, fontSize=8),
        )
    )

    doc.build(story)
    return buf.getvalue()


# ================================================================ 字段定义
# 项目台账导出字段（文档 3.6：字段可勾选）
MAJOR_FIELDS: list[tuple[str, str]] = [
    ("name", "项目名称"),
    ("type", "项目类型"),
    ("partner_company", "合作企业"),
    ("build_period", "建设周期"),
    ("owner_name", "总负责人"),
    ("status_label", "项目状态"),
    ("original_start", "计划开始"),
    ("original_end", "计划结束"),
    ("college_count", "参与学院数"),
    ("sub_count", "子项目数"),
    ("progress", "当前进度%"),
    ("incomplete", "待完善项"),
]

PROGRESS_FIELDS: list[tuple[str, str]] = [
    ("major_name", "所属项目"),
    ("college_name", "学院"),
    ("name", "子项目"),
    ("owner_name", "负责人"),
    ("plan_start", "计划开始"),
    ("plan_end", "计划结束"),
    ("time_progress", "时间进度%"),
    ("current_progress", "实际进度%"),
    ("deviation", "偏差"),
    ("status_label", "状态"),
    ("last_update_at", "最近更新"),
    ("days_since_update", "未更新天数"),
]

REPORT_FIELDS: list[tuple[str, str]] = [
    ("sub_name", "子项目"),
    ("type_label", "报表类型"),
    ("period_start", "周期开始"),
    ("period_end", "周期结束"),
    ("progress", "进度%"),
    ("status_label", "状态"),
    ("submitted_at", "提交时间"),
    ("correct_count", "更正次数"),
    ("summary", "内容摘要"),
]
