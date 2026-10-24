"""认证依赖：当前用户、权限校验、数据范围

## 关于 `?token=` 传令牌（为什么保留 + 风险说明）

浏览器里 `<a href>` / `<img src>` / `window.open` 无法附加 `Authorization`
请求头，附件的下载与预览只能用 query 传令牌（前端 AttachmentPanel 已优先走
header，但 SSE 与直接打开链接仍需兜底）。

风险：令牌会出现在浏览器历史、代理日志、Referer 头里，一旦泄露等于账号被接管。
缓解措施：
1. `POST /api/auth/download-ticket` 换取 **60 秒、一次性** 的下载票据，
   前端下载链路应优先用 `?ticket=`；
2. 审计中间件对 `token` / `ticket` 查询参数一律脱敏为 `***`；
3. `?token=` 仅作为最后兜底，长期方案是前端全部改 header（见审计建议）。
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token, password_version
from app.db.session import get_db
from app.models.system import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole

bearer = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

# 下载票据：60 秒有效
DOWNLOAD_TICKET_TTL_SECONDS = 60
_DOWNLOAD_TICKET_SCOPE = "download"


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


# ================================================================ 下载票据
class _TicketStore:
    """已消费票据的短期黑名单（一次性语义）

    为什么用内存：票据本身是自包含的签名 JWT（多 worker 都能验签），
    这里只记"已经用过的 jti"，防止同一票据在 60 秒内被重复使用。
    多 worker 下各进程各自记录，重放窗口最坏等于票据有效期（60 秒），
    对"下载链接"这个场景可接受；真要严格一次性需要落库，属二期。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._used: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        expired = [k for k, exp in self._used.items() if exp <= now]
        for k in expired:
            self._used.pop(k, None)

    def consume(self, jti: str, expires_at: float) -> bool:
        """标记为已用；已用过返回 False"""
        now = time.time()
        with self._lock:
            self._prune(now)
            if jti in self._used:
                return False
            self._used[jti] = expires_at
            return True


_ticket_store = _TicketStore()


def issue_download_ticket(user: CurrentUser) -> str:
    """签发一次性下载票据（60 秒）"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "scope": _DOWNLOAD_TICKET_SCOPE,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(seconds=DOWNLOAD_TICKET_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def _user_from_ticket(db: Session, ticket: str) -> SysUser:
    """校验并消费下载票据"""
    try:
        payload = jwt.decode(ticket, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("下载链接已过期，请重新下载") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError("下载凭证无效") from e

    if payload.get("scope") != _DOWNLOAD_TICKET_SCOPE:
        raise UnauthorizedError("下载凭证无效")
    jti = payload.get("jti") or ""
    exp = float(payload.get("exp") or 0)
    if not _ticket_store.consume(jti, exp):
        raise UnauthorizedError("下载凭证已被使用，请重新下载")

    user = db.get(SysUser, int(payload.get("sub") or 0))
    if user is None or user.status != "ACTIVE":
        raise UnauthorizedError("账号不可用")
    return user


# ================================================================ 用户上下文
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


def _query_param(request: Request, name: str) -> str | None:
    """安全读取查询参数

    为什么不直接 `request.query_params`：单元测试用最小 scope 构造 Request
    （没有 query_string 键），Starlette 会 KeyError。这里做一层兜底，
    正常 HTTP 请求行为完全一致。
    """
    try:
        return request.query_params.get(name)
    except (KeyError, AttributeError):
        raw = request.scope.get("query_string") or b""
        if isinstance(raw, bytes):
            raw = raw.decode("latin-1")
        if not raw:
            return None
        from urllib.parse import parse_qs

        values = parse_qs(raw).get(name)
        return values[0] if values else None


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    # 令牌来源优先级：
    #   1) Authorization: Bearer（最安全，推荐）
    #   2) ?ticket=（60 秒一次性下载票据，用于 <a>/<img> 等无法带 header 的场景）
    #   3) ?token=（历史兜底，风险见文件头注释；新代码不要再用）
    user: SysUser | None = None

    token = None
    if creds and creds.scheme.lower() == "bearer":
        token = creds.credentials

    ticket = _query_param(request, "ticket")
    if not token and ticket:
        user = _user_from_ticket(db, ticket)

    if not token and user is None:
        token = _query_param(request, "token")
    if not token and user is None:
        raise UnauthorizedError()

    if user is None:
        payload = decode_token(token or "")
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
