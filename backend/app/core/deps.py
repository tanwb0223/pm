"""认证依赖：当前用户、权限校验、数据范围"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token, password_version
from app.db.session import get_db
from app.models.system import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole

bearer = HTTPBearer(auto_error=False)


@dataclass
class Scope:
    """一条数据范围授权"""

    scope_type: str          # ALL / MAJOR / COLLEGE / SELF / CUSTOM
    scope_id: int | None


@dataclass
class CurrentUser:
    """当前登录用户及其权限上下文"""

    id: int
    staff_no: str
    name: str
    college_id: int | None
    roles: list[str] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)
    scopes: list[Scope] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return "SYS_ADMIN" in self.roles

    def has_perm(self, code: str) -> bool:
        return self.is_admin or code in self.permissions

    def has_all_scope(self) -> bool:
        return self.is_admin or any(s.scope_type == "ALL" for s in self.scopes)

    def major_ids(self) -> set[int]:
        return {s.scope_id for s in self.scopes if s.scope_type == "MAJOR" and s.scope_id}

    def college_ids(self) -> set[int]:
        return {s.scope_id for s in self.scopes if s.scope_type == "COLLEGE" and s.scope_id}


def _load_user_context(db: Session, user: SysUser) -> CurrentUser:
    today = date.today()
    rows = db.execute(
        select(SysUserRole, SysRole)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(SysUserRole.user_id == user.id)
    ).all()

    roles: list[str] = []
    scopes: list[Scope] = []
    role_ids: list[int] = []
    for ur, role in rows:
        # 有效期检查
        if ur.valid_to and ur.valid_to < today:
            continue
        if ur.valid_from and ur.valid_from > today:
            continue
        roles.append(role.code)
        role_ids.append(role.id)
        scopes.append(Scope(scope_type=ur.scope_type, scope_id=ur.scope_id))

    perms: set[str] = set()
    if role_ids:
        perms = set(
            db.scalars(
                select(SysPermission.code)
                .join(SysRolePermission, SysRolePermission.permission_id == SysPermission.id)
                .where(SysRolePermission.role_id.in_(role_ids))
            )
        )

    return CurrentUser(
        id=user.id,
        staff_no=user.staff_no,
        name=user.name,
        college_id=user.college_id,
        roles=roles,
        permissions=perms,
        scopes=scopes,
    )


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    token = None
    if creds and creds.scheme.lower() == "bearer":
        token = creds.credentials
    if not token:
        # 兼容 SSE / 下载等不便带 header 的场景
        token = request.query_params.get("token")
    if not token:
        raise UnauthorizedError()

    payload = decode_token(token)
    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedError()

    user = db.get(SysUser, int(sub))
    if user is None or user.status != "ACTIVE":
        raise UnauthorizedError("账号不可用")

    # 改密后旧令牌立即失效：令牌里的 pv 必须等于当前口令版本
    if int(payload.get("pv", 0)) != password_version(user):
        raise UnauthorizedError("密码已变更，请重新登录")

    ctx = _load_user_context(db, user)
    # 缓存到 request.state，审计日志可用
    request.state.current_user = ctx
    return ctx


def require_perm(code: str):
    """功能权限校验依赖"""

    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has_perm(code):
            raise ForbiddenError(f"缺少权限：{code}")
        return user

    return _checker


def require_any_perm(*codes: str):
    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(user.has_perm(c) for c in codes):
            raise ForbiddenError(f"缺少权限：{' 或 '.join(codes)}")
        return user

    return _checker
