"""批量导入服务：用户、学院

支持两种输入：
1. Excel（.xlsx）：按表头自动识别列，容错列名别名
2. CSV（.csv）：同上

导入策略：先校验全部行，全部通过才写库（避免导入一半失败留下脏数据）。
已存在的工号/学院编码可选择跳过或更新。
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.core.security import hash_password
from app.models.system import SysCollege, SysRole, SysUser, SysUserRole

logger = logging.getLogger(__name__)

# 列名别名映射（表头 → 标准字段名）
USER_HEADERS = {
    "staff_no": ["工号", "职工号", "教工号", "staff_no", "staffno", "账号"],
    "name": ["姓名", "名字", "name"],
    "college": ["学院", "所属学院", "二级学院", "college"],
    "phone": ["手机", "手机号", "电话", "phone", "mobile"],
    "email": ["邮箱", "电子邮箱", "email", "mail"],
    "role": ["角色", "角色编码", "role"],
}

COLLEGE_HEADERS = {
    "name": ["学院名称", "学院", "名称", "name"],
    "code": ["学院编码", "编码", "code"],
    "parent": ["上级学院", "上级", "parent"],
}


@dataclass
class ImportResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "error_count": len(self.errors),
            "errors": self.errors[:100],  # 最多返回 100 条错误
        }


def _read_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    """读 Excel 或 CSV，返回 [{'列名': '值'}]"""
    lower = filename.lower()

    if lower.endswith(".csv"):
        # 尝试 UTF-8-BOM → UTF-8 → GBK（Excel 导出的中文 CSV 常是 GBK）
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise BusinessError("CSV 编码无法识别，请另存为 UTF-8 后重试")

        reader = csv.DictReader(io.StringIO(text))
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in reader
            if any((v or "").strip() for v in row.values())
        ]

    if lower.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            raise BusinessError("Excel 内容为空")

        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        out = []
        for r in rows[1:]:
            if not any(c is not None and str(c).strip() for c in r):
                continue
            out.append(
                {
                    header[i]: (str(v).strip() if v is not None else "")
                    for i, v in enumerate(r)
                    if i < len(header)
                }
            )
        return out

    raise BusinessError("仅支持 .xlsx 或 .csv 文件")


def _pick(row: dict[str, str], aliases: list[str]) -> str:
    """按别名列表取值"""
    for a in aliases:
        if a in row:
            v = row[a]
            if v:
                return v
    # 忽略大小写再试一次
    low = {k.lower(): v for k, v in row.items()}
    for a in aliases:
        if a.lower() in low and low[a.lower()]:
            return low[a.lower()]
    return ""


# ================================================================ 用户导入
def import_users(
    db: Session,
    filename: str,
    content: bytes,
    *,
    default_password: str = "Init@123456",
    update_existing: bool = False,
) -> ImportResult:
    rows = _read_rows(filename, content)
    result = ImportResult(total=len(rows))

    colleges = {c.name: c.id for c in db.scalars(select(SysCollege))}
    colleges.update({c.code: c.id for c in db.scalars(select(SysCollege)) if c.code})
    roles = {r.code: r.id for r in db.scalars(select(SysRole))}

    # 先全部校验
    parsed: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=2):  # Excel 第 2 行起
        staff_no = _pick(row, USER_HEADERS["staff_no"])
        name = _pick(row, USER_HEADERS["name"])

        if not staff_no:
            result.errors.append({"row": idx, "error": "缺少工号"})
            continue
        if not name:
            result.errors.append({"row": idx, "error": f"工号 {staff_no} 缺少姓名"})
            continue

        college_raw = _pick(row, USER_HEADERS["college"])
        college_id = None
        if college_raw:
            college_id = colleges.get(college_raw)
            if college_id is None:
                result.errors.append(
                    {"row": idx, "error": f"学院不存在：{college_raw}"}
                )
                continue

        role_raw = _pick(row, USER_HEADERS["role"])
        role_id = None
        if role_raw:
            role_id = roles.get(role_raw) or roles.get(role_raw.upper())
            if role_id is None:
                result.errors.append({"row": idx, "error": f"角色不存在：{role_raw}"})
                continue

        parsed.append(
            {
                "row": idx,
                "staff_no": staff_no,
                "name": name,
                "college_id": college_id,
                "phone": _pick(row, USER_HEADERS["phone"]) or None,
                "email": _pick(row, USER_HEADERS["email"]) or None,
                "role_id": role_id,
            }
        )

    if result.errors:
        # 有错误就整体不导入，避免脏数据
        return result

    # 写入
    for item in parsed:
        existing = db.scalar(
            select(SysUser).where(SysUser.staff_no == item["staff_no"])
        )
        if existing is not None:
            if not update_existing:
                result.skipped += 1
                continue
            existing.name = item["name"]
            if item["college_id"] is not None:
                existing.college_id = item["college_id"]
            if item["phone"]:
                existing.phone = item["phone"]
            if item["email"]:
                existing.email = item["email"]
            result.updated += 1
            user = existing
        else:
            user = SysUser(
                staff_no=item["staff_no"],
                name=item["name"],
                college_id=item["college_id"],
                phone=item["phone"],
                email=item["email"],
                password_hash=hash_password(default_password),
                status="ACTIVE",
            )
            db.add(user)
            db.flush()
            result.created += 1

        # 授权（默认子项目负责人 + 本人范围）
        if item["role_id"]:
            exists = db.scalar(
                select(SysUserRole).where(
                    SysUserRole.user_id == user.id,
                    SysUserRole.role_id == item["role_id"],
                )
            )
            if exists is None:
                db.add(
                    SysUserRole(
                        user_id=user.id,
                        role_id=item["role_id"],
                        scope_type="SELF",
                        scope_id=None,
                    )
                )

    db.flush()
    logger.info(
        "批量导入用户：总 %s 新建 %s 更新 %s 跳过 %s",
        result.total,
        result.created,
        result.updated,
        result.skipped,
    )
    return result


# ================================================================ 学院导入
def import_colleges(
    db: Session, filename: str, content: bytes, *, update_existing: bool = False
) -> ImportResult:
    rows = _read_rows(filename, content)
    result = ImportResult(total=len(rows))

    parsed: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=2):
        name = _pick(row, COLLEGE_HEADERS["name"])
        if not name:
            result.errors.append({"row": idx, "error": "缺少学院名称"})
            continue
        parsed.append(
            {
                "row": idx,
                "name": name,
                "code": _pick(row, COLLEGE_HEADERS["code"]) or None,
                "parent": _pick(row, COLLEGE_HEADERS["parent"]) or None,
            }
        )

    if result.errors:
        return result

    # 第一轮：建本学院
    for item in parsed:
        existing = None
        if item["code"]:
            existing = db.scalar(select(SysCollege).where(SysCollege.code == item["code"]))
        if existing is None:
            existing = db.scalar(select(SysCollege).where(SysCollege.name == item["name"]))

        if existing is not None:
            if update_existing:
                existing.name = item["name"]
                if item["code"]:
                    existing.code = item["code"]
                result.updated += 1
            else:
                result.skipped += 1
        else:
            db.add(SysCollege(name=item["name"], code=item["code"], sort=0))
            result.created += 1
    db.flush()

    # 第二轮：处理上级学院
    all_colleges = {c.name: c for c in db.scalars(select(SysCollege))}
    for item in parsed:
        if not item["parent"]:
            continue
        parent = all_colleges.get(item["parent"])
        child = all_colleges.get(item["name"])
        if parent and child:
            child.parent_id = parent.id
    db.flush()

    logger.info("批量导入学院：总 %s 新建 %s 更新 %s", result.total, result.created, result.updated)
    return result


# ================================================================ 模板生成
def user_template() -> bytes:
    """生成用户导入模板（含示例行与填写说明）"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "用户导入"

    headers = ["工号", "姓名", "学院", "手机", "邮箱", "角色"]
    ws.append(headers)
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i)
        c.font = Font(name="宋体", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E3A5F")
        c.alignment = Alignment(horizontal="center")

    # 示例行
    ws.append(["T1001", "张三", "物流管理学院", "13800138000", "zhangsan@school.edu.cn", "SUB_OWNER"])
    ws.append(["T1002", "李四", "运输管理学院", "", "", "REPORTER"])

    widths = [12, 10, 18, 16, 28, 14]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    # 说明 sheet
    ws2 = wb.create_sheet("填写说明")
    notes = [
        ["列名", "是否必填", "说明"],
        ["工号", "必填", "唯一标识，重复工号默认跳过"],
        ["姓名", "必填", ""],
        ["学院", "选填", "须与系统中已有学院名称或编码一致"],
        ["手机", "选填", ""],
        ["邮箱", "选填", ""],
        ["角色", "选填", "角色编码，如 SUB_OWNER / REPORTER / VIEWER；留空则不授权"],
        ["", "", ""],
        ["可用角色编码", "", "SYS_ADMIN 系统管理员 / PROJECT_OWNER 项目总负责人 / MAJOR_OWNER 大项目负责人"],
        ["", "", "COLLEGE_OWNER 学院负责人 / SUB_OWNER 子项目负责人 / REPORTER 填报人 / VIEWER 只读"],
        ["", "", ""],
        ["初始密码", "", "导入用户默认密码 Init@123456，首次登录请修改"],
    ]
    for r in notes:
        ws2.append(r)
    ws2.column_dimensions["A"].width = 16
    ws2.column_dimensions["B"].width = 12
    ws2.column_dimensions["C"].width = 70

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def college_template() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "学院导入"
    headers = ["学院名称", "学院编码", "上级学院"]
    ws.append(headers)
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i)
        c.font = Font(name="宋体", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E3A5F")
        c.alignment = Alignment(horizontal="center")

    ws.append(["物流管理学院", "WL", ""])
    ws.append(["运输管理学院", "YS", ""])
    ws.append(["物流管理系", "WL-01", "物流管理学院"])

    for i, w in enumerate([22, 14, 20], start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
