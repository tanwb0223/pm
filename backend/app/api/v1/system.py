"""系统管理 API：用户、角色、权限、参数、免填日历、审计日志、备份"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user, require_any_perm, require_perm
from app.db.dialect import ilike
from app.core.exceptions import BusinessError, NotFoundError
from app.core.response import ok, page
from app.core.security import hash_password
from app.db.session import get_db
from app.models.system import (
    AuditLog,
    NotifyLog,
    SysConfig,
    SysHoliday,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["系统管理"])


# ================================================================ 用户
class UserIn(BaseModel):
    staff_no: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    college_id: int | None = None
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|DISABLED)$")


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    college_id: int | None = None
    phone: str | None = None
    email: str | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)
    status: str | None = Field(default=None, pattern="^(ACTIVE|DISABLED)$")


@router.get("/users/picker", summary="用户下拉选择（任意登录用户可用）")
def pick_users(
    keyword: str | None = None,
    college_id: int | None = None,
    limit: int = Query(default=30, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """供项目负责人、预警转交等场景选择人员。

    与 /users 的区别：不要求 system:user 权限，且只返回
    必要字段（id/name/staff_no/college_name），避免泄露手机邮箱。
    但必须登录后才能调用（可遍历全校通讯录，不能匿名访问）。
    """
    stmt = select(SysUser).where(SysUser.status == "ACTIVE")
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(ilike(SysUser.name, keyword), ilike(SysUser.staff_no, keyword)))
    if college_id:
        stmt = stmt.where(SysUser.college_id == college_id)
    rows = list(db.scalars(stmt.order_by(SysUser.id).limit(limit)))
    return ok(
        [
            {
                "id": u.id,
                "name": u.name,
                "staff_no": u.staff_no,
                "college_id": u.college_id,
                "college_name": u.college.name if u.college else None,
            }
            for u in rows
        ]
    )


@router.get("/users", summary="用户列表", dependencies=[Depends(require_perm("system:user"))])
def list_users(
    keyword: str | None = None,
    college_id: int | None = None,
    status: str | None = None,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(SysUser)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(ilike(SysUser.name, keyword), ilike(SysUser.staff_no, keyword)))
    if college_id:
        stmt = stmt.where(SysUser.college_id == college_id)
    if status:
        stmt = stmt.where(SysUser.status == status)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(stmt.order_by(SysUser.id).offset((page_no - 1) * page_size).limit(page_size))
    )
    return page(
        [
            {
                "id": u.id,
                "staff_no": u.staff_no,
                "name": u.name,
                "college_id": u.college_id,
                "college_name": u.college.name if u.college else None,
                "phone": u.phone,
                "email": u.email,
                "status": u.status,
                "last_login_at": u.last_login_at,
                "has_password": bool(u.password_hash),
            }
            for u in rows
        ],
        total,
        page_no,
        page_size,
    )


@router.post("/users", summary="新增用户", dependencies=[Depends(require_perm("system:user"))])
def create_user(body: UserIn, db: Session = Depends(get_db)):
    if db.scalar(select(SysUser).where(SysUser.staff_no == body.staff_no)):
        raise BusinessError(f"工号已存在：{body.staff_no}")
    data = body.model_dump()
    pwd = data.pop("password", None)
    u = SysUser(**data)
    u.password_hash = hash_password(pwd) if pwd else None
    # 管理员代设的初始密码属于「他人已知」的口令，强制首次登录后改掉
    u.must_change_password = bool(pwd)
    db.add(u)
    db.commit()
    return ok({"id": u.id})


@router.put("/users/{user_id}", summary="编辑用户", dependencies=[Depends(require_perm("system:user"))])
def update_user(user_id: int, body: UserUpdateIn, db: Session = Depends(get_db)):
    u = db.get(SysUser, user_id)
    if u is None:
        raise NotFoundError("用户不存在")
    data = body.model_dump(exclude_unset=True)
    pwd = data.pop("password", None)
    for k, v in data.items():
        setattr(u, k, v)
    if pwd:
        u.password_hash = hash_password(pwd)
        # 管理员重置密码后，用户下次登录必须自己改
        u.must_change_password = True
        # 为什么必须同步 password_updated_at：
        # 令牌里的 pv 声明 = password_updated_at 的时间戳（见 core/security.py）。
        # 原来重置密码不更新这个字段，pv 仍是旧值（未改过密为 0），
        # 被重置前的旧令牌继续有效 —— 等于"改密踢人"对管理员重置完全失效。
        now = datetime.now(UTC)
        prev = u.password_updated_at
        if prev is not None:
            prev_utc = prev.replace(tzinfo=UTC) if prev.tzinfo is None else prev
            # pv 是秒级时间戳：同一秒内重置两次会得到相同 pv，
            # 旧令牌反而不会失效，这里保证严格递增（+1 秒兜底）。
            if int(now.timestamp()) <= int(prev_utc.timestamp()):
                now = prev_utc.replace(microsecond=0) + timedelta(seconds=1)
        u.password_updated_at = now
    db.commit()
    return ok()


@router.post(
    "/users/{user_id}/unlock",
    summary="手动解锁被登录限流锁定的账号",
    dependencies=[Depends(require_perm("system:user"))],
)
def unlock_account(user_id: int, db: Session = Depends(get_db)):
    """管理员解锁入口

    背景：登录失败限流改为落库后，用户被锁只能干等 15 分钟；
    运维需要在用户报障时能立即放行（早期进程内实现连"解锁"这个概念都没有）。
    """
    from app.core.login_guard import unlock_user

    u = db.get(SysUser, user_id)
    if u is None:
        raise NotFoundError("用户不存在")
    unlock_user(db, u)
    db.commit()
    return ok({"unlocked": True})


@router.delete("/users/{user_id}", summary="停用用户", dependencies=[Depends(require_perm("system:user"))])
def disable_user(user_id: int, db: Session = Depends(get_db)):
    u = db.get(SysUser, user_id)
    if u is None:
        raise NotFoundError("用户不存在")
    u.status = "DISABLED"
    db.commit()
    return ok()


# ================================================================ 授权
class GrantIn(BaseModel):
    user_id: int
    role_id: int
    scope_type: str = Field(default="SELF", pattern="^(ALL|MAJOR|COLLEGE|SELF|CUSTOM)$")
    scope_id: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def check_scope(self):
        if self.scope_type in ("MAJOR", "COLLEGE") and not self.scope_id:
            raise ValueError(f"scope_type={self.scope_type} 时必须提供 scope_id")
        return self


@router.get("/grants", summary="授权列表", dependencies=[Depends(require_perm("system:role"))])
def list_grants(user_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(SysUserRole, SysRole, SysUser).join(
        SysRole, SysRole.id == SysUserRole.role_id
    ).join(SysUser, SysUser.id == SysUserRole.user_id)
    if user_id:
        stmt = stmt.where(SysUserRole.user_id == user_id)
    rows = db.execute(stmt.order_by(SysUserRole.user_id)).all()
    return ok(
        [
            {
                "id": ur.id,
                "user_id": ur.user_id,
                "user_name": u.name,
                "staff_no": u.staff_no,
                "role_id": ur.role_id,
                "role_code": r.code,
                "role_name": r.name,
                "scope_type": ur.scope_type,
                "scope_id": ur.scope_id,
                "valid_from": ur.valid_from,
                "valid_to": ur.valid_to,
            }
            for ur, r, u in rows
        ]
    )


@router.post("/grants", summary="授予角色", dependencies=[Depends(require_perm("system:role"))])
def add_grant(body: GrantIn, db: Session = Depends(get_db)):
    if db.get(SysUser, body.user_id) is None:
        raise BusinessError("用户不存在")
    if db.get(SysRole, body.role_id) is None:
        raise BusinessError("角色不存在")
    exists = db.scalar(
        select(SysUserRole).where(
            SysUserRole.user_id == body.user_id,
            SysUserRole.role_id == body.role_id,
            SysUserRole.scope_type == body.scope_type,
            SysUserRole.scope_id == body.scope_id,
        )
    )
    if exists:
        raise BusinessError("该授权已存在")
    ur = SysUserRole(**body.model_dump())
    db.add(ur)
    db.commit()
    return ok({"id": ur.id})


@router.delete("/grants/{grant_id}", summary="撤销授权", dependencies=[Depends(require_perm("system:role"))])
def delete_grant(grant_id: int, db: Session = Depends(get_db)):
    ur = db.get(SysUserRole, grant_id)
    if ur is None:
        raise NotFoundError("授权不存在")
    db.delete(ur)
    db.commit()
    return ok()


# ================================================================ 角色
@router.get("/roles", summary="角色列表", dependencies=[Depends(require_perm("system:role"))])
def list_roles(db: Session = Depends(get_db)):
    roles = list(db.scalars(select(SysRole).order_by(SysRole.id)))
    out = []
    for r in roles:
        perm_count = db.scalar(
            select(func.count()).select_from(SysRolePermission).where(
                SysRolePermission.role_id == r.id
            )
        ) or 0
        out.append(
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "is_system": r.is_system,
                "permission_count": perm_count,
            }
        )
    return ok(out)


class RoleIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    remark: str | None = None


@router.post("/roles", summary="新增自定义角色（一期上限 3 个）", dependencies=[Depends(require_perm("system:role"))])
def create_role(body: RoleIn, db: Session = Depends(get_db)):
    if db.scalar(select(SysRole).where(SysRole.code == body.code)):
        raise BusinessError("角色编码已存在")
    custom_count = db.scalar(
        select(func.count()).select_from(SysRole).where(SysRole.is_system.is_(False))
    ) or 0
    if custom_count >= 3:
        raise BusinessError("一期自定义角色上限为 3 个")
    r = SysRole(code=body.code, name=body.name, remark=body.remark, is_system=False)
    db.add(r)
    db.commit()
    return ok({"id": r.id})


@router.get("/permissions", summary="权限点树", dependencies=[Depends(require_perm("system:role"))])
def list_permissions(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(SysPermission).order_by(SysPermission.sort, SysPermission.id)))
    return ok(
        [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "type": p.type,
                "parent_id": p.parent_id,
                "sort": p.sort,
            }
            for p in rows
        ]
    )


class RolePermIn(BaseModel):
    permission_ids: list[int]


@router.get("/roles/{role_id}/permissions", summary="读取角色已有权限", dependencies=[Depends(require_perm("system:role"))])
def get_role_permissions(role_id: int, db: Session = Depends(get_db)):
    r = db.get(SysRole, role_id)
    if r is None:
        raise NotFoundError("角色不存在")
    ids = list(
        db.scalars(
            select(SysRolePermission.permission_id).where(SysRolePermission.role_id == role_id)
        )
    )
    return ok({"role_id": role_id, "role_code": r.code, "permission_ids": ids})


@router.put("/roles/{role_id}/permissions", summary="设置角色权限", dependencies=[Depends(require_perm("system:role"))])
def set_role_permissions(role_id: int, body: RolePermIn, db: Session = Depends(get_db)):
    r = db.get(SysRole, role_id)
    if r is None:
        raise NotFoundError("角色不存在")
    if r.code == "SYS_ADMIN":
        raise BusinessError("系统管理员权限不可修改")
    db.query(SysRolePermission).filter(SysRolePermission.role_id == role_id).delete()
    for pid in set(body.permission_ids):
        db.add(SysRolePermission(role_id=role_id, permission_id=pid))
    db.commit()
    return ok()


# ================================================================ 参数
@router.get("/configs", summary="系统参数", dependencies=[Depends(require_perm("system:config"))])
def list_configs(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(SysConfig).order_by(SysConfig.key)))
    return ok(
        [
            {
                "key": c.key,
                "value": c.value,
                "value_type": c.value_type,
                "remark": c.remark,
            }
            for c in rows
        ]
    )


class ConfigIn(BaseModel):
    value: str


@router.put("/configs/{key}", summary="修改参数", dependencies=[Depends(require_perm("system:config"))])
def set_config(key: str, body: ConfigIn, db: Session = Depends(get_db)):
    row = db.get(SysConfig, key)
    if row is None:
        raise NotFoundError(f"参数不存在：{key}")
    row.value = body.value
    db.commit()
    ConfigService.clear_cache()
    return ok()


# ================================================================ 免填日历
class HolidayIn(BaseModel):
    dates: list[date] = Field(min_length=1)
    type: str = Field(default="HOLIDAY", pattern="^(HOLIDAY|VACATION)$")
    name: str | None = None


@router.get("/holidays", summary="免填日历")
def list_holidays(
    year: int | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(SysHoliday).order_by(SysHoliday.date)
    if year:
        stmt = stmt.where(
            SysHoliday.date >= date(year, 1, 1), SysHoliday.date <= date(year, 12, 31)
        )
    rows = list(db.scalars(stmt))
    return ok(
        [{"id": h.id, "date": h.date, "type": h.type, "name": h.name} for h in rows]
    )


@router.post("/holidays", summary="批量设置免填日期", dependencies=[Depends(require_perm("system:holiday"))])
def add_holidays(body: HolidayIn, db: Session = Depends(get_db)):
    added = 0
    for d in body.dates:
        exists = db.scalar(select(SysHoliday).where(SysHoliday.date == d))
        if exists:
            exists.type = body.type
            exists.name = body.name
        else:
            db.add(SysHoliday(date=d, type=body.type, name=body.name))
            added += 1
    db.commit()
    return ok({"added": added, "total": len(body.dates)})


@router.delete("/holidays/{holiday_id}", summary="删除免填日", dependencies=[Depends(require_perm("system:holiday"))])
def delete_holiday(holiday_id: int, db: Session = Depends(get_db)):
    h = db.get(SysHoliday, holiday_id)
    if h is None:
        raise NotFoundError("记录不存在")
    db.delete(h)
    db.commit()
    return ok()


# ================================================================ 审计日志
@router.get("/audit-logs", summary="操作日志", dependencies=[Depends(require_perm("system:audit"))])
def list_audit(
    operator_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog)
    if operator_id:
        stmt = stmt.where(AuditLog.operator_id == operator_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(AuditLog.id.desc()).offset((page_no - 1) * page_size).limit(page_size)
        )
    )
    return page(
        [
            {
                "id": a.id,
                "operator_id": a.operator_id,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "detail": a.detail,
                "ip": a.ip,
                "created_at": a.created_at,
            }
            for a in rows
        ],
        total,
        page_no,
        page_size,
    )


# ================================================================ 通知日志
@router.get(
    "/notify-logs",
    summary="通知投递日志",
    dependencies=[Depends(require_perm("system:audit"))],
)
def list_notify_logs(
    channel: str | None = None,
    status: str | None = None,
    receiver_id: int | None = None,
    biz_type: str | None = None,
    page_no: int = Query(default=1, ge=1, alias="page"),
    page_size: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """邮件/短信/站内消息的投递记录

    通道未配置时状态为 SKIPPED 并写明原因，便于运维判断是「没配网关」
    还是「配了但发失败」。失败记录由每 10 分钟的重投任务重试。
    """
    stmt = select(NotifyLog)
    if channel:
        stmt = stmt.where(NotifyLog.channel == channel.upper())
    if status:
        stmt = stmt.where(NotifyLog.status == status.upper())
    if receiver_id:
        stmt = stmt.where(NotifyLog.receiver_id == receiver_id)
    if biz_type:
        stmt = stmt.where(NotifyLog.biz_type == biz_type)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(NotifyLog.id.desc()).offset((page_no - 1) * page_size).limit(page_size)
        )
    )

    # 接收人姓名一次性取回，避免逐条查询
    uids = {r.receiver_id for r in rows}
    names: dict[int, str] = {}
    if uids:
        names = {
            u.id: u.name for u in db.scalars(select(SysUser).where(SysUser.id.in_(uids)))
        }

    return page(
        [
            {
                "id": r.id,
                "biz_type": r.biz_type,
                "biz_id": r.biz_id,
                "channel": r.channel,
                "receiver_id": r.receiver_id,
                "receiver_name": names.get(r.receiver_id),
                "status": r.status,
                "error": r.error,
                "sent_at": r.sent_at,
            }
            for r in rows
        ],
        total,
        page_no,
        page_size,
    )


# ================================================================ 批量导入
@router.get(
    "/import/templates/{kind}",
    summary="下载导入模板（user / college）",
    dependencies=[Depends(require_any_perm("system:user", "system:org"))],
)
def download_template(kind: str):
    from app.services import import_service

    if kind == "user":
        content = import_service.user_template()
        name = "用户导入模板.xlsx"
    elif kind == "college":
        content = import_service.college_template()
        name = "学院导入模板.xlsx"
    else:
        raise BusinessError("kind 只能是 user 或 college")

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post(
    "/import/users",
    summary="批量导入用户（Excel/CSV）",
    dependencies=[Depends(require_perm("system:user"))],
)
async def import_users(
    file: UploadFile = File(..., description=".xlsx 或 .csv"),
    default_password: str = Query(default="Init@123456", min_length=6, max_length=64),
    update_existing: bool = Query(default=False, description="已存在工号是否更新"),
    db: Session = Depends(get_db),
):
    from app.services import import_service

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise BusinessError("文件不能超过 10MB")

    result = import_service.import_users(
        db,
        file.filename or "",
        content,
        default_password=default_password,
        update_existing=update_existing,
    )
    if result.errors:
        # 有错误则整体不导入
        db.rollback()
    else:
        db.commit()
    return ok(result.to_dict())


@router.post(
    "/import/colleges",
    summary="批量导入学院（Excel/CSV）",
    dependencies=[Depends(require_perm("system:org"))],
)
async def import_colleges(
    file: UploadFile = File(..., description=".xlsx 或 .csv"),
    update_existing: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from app.services import import_service

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise BusinessError("文件不能超过 10MB")

    result = import_service.import_colleges(
        db, file.filename or "", content, update_existing=update_existing
    )
    if result.errors:
        db.rollback()
    else:
        db.commit()
    return ok(result.to_dict())


# ================================================================ 数据库备份
# 背景（doc/闭环缺口汇总-20261024.md 3.8）：
# 备份 job 天天跑，但管理端看不到「有没有备份成功、最近一次是什么时候」，
# 也没有手动触发入口 —— 管理员只能 SSH 上去 ls 目录。
# 这里补两个只读/触发接口，真正的备份逻辑仍在 scripts/backup.py（跨平台）。
BACKUP_SUFFIXES = (".dump", ".db.gz", ".sql.gz", ".tar.gz")


def _backup_dir() -> Path:
    d = Path(settings.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_backup_files() -> list[Path]:
    out_dir = _backup_dir()
    files = [f for f in out_dir.glob("pm_*") if f.is_file() and f.name.endswith(BACKUP_SUFFIXES)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _backup_item(path: Path) -> dict:
    st = path.stat()
    return {
        "name": path.name,
        "size_mb": round(st.st_size / 1024 / 1024, 2),
        "created_at": datetime.fromtimestamp(st.st_mtime, tz=UTC),
        "path": str(path),
    }


@router.get(
    "/backups",
    summary="备份文件列表",
    dependencies=[Depends(require_perm("system:config"))],
)
def list_backups():
    """列出备份目录中的备份文件（名称/大小/时间）

    同时支持 SQLite（pm_*.db.gz）与 PostgreSQL（pm_*.dump）两种命名，
    因为系统支持双数据库部署，运维页面不该只认一种。
    """
    return ok([_backup_item(f) for f in _list_backup_files()])


@router.post(
    "/backups/run",
    summary="手动触发一次数据库备份",
    dependencies=[Depends(require_perm("system:config"))],
)
def run_backup():
    """同步执行 scripts/backup.py

    为什么同步：备份通常几秒到几十秒，管理员点一下要能立刻看到结果；
    做成异步还得引入任务队列与轮询，收益不大。
    用当前解释器执行，确保与 Web 进程同一虚拟环境、同一份配置。
    """
    backend_root = Path(__file__).resolve().parent.parent.parent.parent
    script = backend_root / "scripts" / "backup.py"
    if not script.is_file():
        raise BusinessError(f"备份脚本不存在：{script}")

    before = {f.name for f in _list_backup_files()}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            timeout=1800,
            cwd=str(backend_root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired as e:
        raise BusinessError("备份超时（超过 30 分钟）") from e
    except OSError as e:
        raise BusinessError(f"无法执行备份脚本：{e}") from e

    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    for line in stdout.strip().splitlines()[-5:]:
        logger.info("[backup] %s", line)

    if result.returncode != 0:
        raise BusinessError(f"备份失败：{(stderr or stdout).strip()[:500]}")

    # 找出本次新增的文件（备份脚本按时间戳命名，新旧文件可直接比对）
    created = [f for f in _list_backup_files() if f.name not in before]
    if not created:
        # 极端情况：脚本成功但没产出新文件（如目录配置不一致），视为失败
        raise BusinessError("备份脚本执行完成但未生成新备份文件，请检查 PM_BACKUP_DIR")
    latest = created[0]
    item = _backup_item(latest)
    return ok({"name": item["name"], "size_mb": item["size_mb"]})

