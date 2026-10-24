"""认证 API：多协议登录、配置管理、账号绑定"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.service import AuthService
from app.core.config import settings
from app.core.deps import (
    DOWNLOAD_TICKET_TTL_SECONDS,
    CurrentUser,
    get_current_user,
    issue_download_ticket,
    require_perm,
)
from app.core.exceptions import BusinessError
from app.core.login_guard import (
    check_locked,
    locked_seconds,
    login_throttle,
    record_failure,
    record_success,
)
from app.core.response import ok
from app.core.security import create_access_token, password_version
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _public_base(request: Request) -> str:
    """推导对外基础地址，支持反向代理"""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _frontend_base(request: Request) -> str:
    """前端对外地址：用于 SSO 回调 302 的落地页

    优先级：
      1) settings.frontend_base_url（部署时显式配置，最可靠）
      2) 请求头 Origin / Referer（反向代理场景下自动跟随）
      3) _public_base(request)（前后端同域时就是它）
      4) http://127.0.0.1:8000（便携版兜底）

    为什么不能直接用 settings.frontend_dir：那是「本机静态文件目录」，
    形如 D:/pm/frontend/dist，拼不出可访问的 URL。
    """
    configured = (settings.frontend_base_url or "").strip()
    if configured:
        return configured.rstrip("/")

    origin = (request.headers.get("origin") or "").strip()
    if origin and origin != "null":
        return origin.rstrip("/")

    referer = (request.headers.get("referer") or "").strip()
    if referer:
        from urllib.parse import urlparse

        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    base = _public_base(request)
    if base:
        return base.rstrip("/")
    return "http://127.0.0.1:8000"


def _wants_json(request: Request, fmt: str | None) -> bool:
    """SSO 回调是否需要返回 JSON（脚本/测试兼容）

    浏览器走 302 到前端落地页；命令行/自动化脚本传 Accept: application/json
    或 ?format=json 时仍返回统一 JSON，不改变既有集成方式。
    """
    if (fmt or "").strip().lower() == "json":
        return True
    accept = (request.headers.get("accept") or "").lower()
    # 注意：浏览器默认 Accept 是 text/html,...，只有显式声明 json 才走 JSON
    return "application/json" in accept and "text/html" not in accept


# ---------------------------------------------------------------- 请求体
class PasswordLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LdapLoginIn(BaseModel):
    username: str
    password: str


class ProviderConfigIn(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scopes: str | None = None
    server_url: str | None = None
    extra: dict[str, Any] | None = None
    auto_create_user: bool | None = None
    default_role_id: int | None = None


class ExchangeIn(BaseModel):
    code: str


# ---------------------------------------------------------------- 公共
@router.get("/providers", summary="登录页可用的认证方式")
def list_public_providers(request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    return ok(svc.public_providers())


@router.post("/login", summary="账号密码登录")
def login_password(body: PasswordLoginIn, request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    username = body.username.strip()
    user = svc.find_user(username)

    # 审计：登录接口没有 current_user，把工号写进 request.state 供中间件记录
    request.state.audit_extra = {"staff_no": username, "auth_kind": "password"}

    # 防在线撞库：计数落库（多 worker / 重启后仍有效），锁定返回 429
    check_locked(user)
    if user is None:
        # 用户不存在时数据库无处落账，用进程内计数挡住用户名枚举
        key = f"pwd:{username.lower()}"
        wait = login_throttle.locked_seconds(key)
        if wait:
            from app.core.login_guard import LoginLockedError

            raise LoginLockedError(wait)

    try:
        user = svc.login_password(username, body.password)
    except ValueError as e:
        # 只有「凭证错误」才计入限流；认证方式停用/账号停用属于管理配置，
        # 计进去会让一次误配置把全校账号锁死（见 auth/service.py CredentialError）。
        from app.auth.service import CredentialError

        if isinstance(e, CredentialError):
            if user is not None:
                request.state.audit_operator_id = user.id
                record_failure(db, user)
                db.commit()
            else:
                login_throttle.record_failure(f"pwd:{username.lower()}")
        raise BusinessError(str(e)) from e

    request.state.audit_operator_id = user.id
    record_success(db, user)
    db.commit()
    token = create_access_token(user.id, {"pv": password_version(user)})
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user.id, "staff_no": user.staff_no, "name": user.name},
            # 首次登录或管理员重置后为 true，前端据此强制弹改密框
            "must_change_password": bool(user.must_change_password),
        }
    )


@router.post("/login/ldap", summary="LDAP / AD 域登录")
def login_ldap(body: LdapLoginIn, request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    username = body.username.strip()
    request.state.audit_extra = {"staff_no": username, "auth_kind": "ldap"}

    # LDAP 的密码由域控校验，本地库只用来记失败次数与锁定状态
    user = svc.find_user(username)
    check_locked(user)
    try:
        user = svc.login_ldap(username, body.password)
    except ValueError as e:
        if user is not None:
            request.state.audit_operator_id = user.id
            record_failure(db, user)
            db.commit()
        raise BusinessError(str(e)) from e
    request.state.audit_operator_id = user.id
    record_success(db, user)
    db.commit()
    token = create_access_token(user.id, {"pv": password_version(user)})
    return ok({"access_token": token, "token_type": "bearer", "user": {"id": user.id, "name": user.name}})


@router.get("/login/{kind}/start", summary="发起跳转登录（返回授权地址）")
def start_login(
    kind: str,
    request: Request,
    redirect_after: str | None = Query(default="/"),
    db: Session = Depends(get_db),
):
    svc = AuthService(db, _public_base(request))
    try:
        data = svc.start_redirect_login(kind, redirect_after=redirect_after)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    return ok(data)


@router.get("/{kind}/callback", summary="认证回调（各协议统一入口）")
def callback(
    kind: str,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    # CAS 用 ticket；SAML 用 SAMLResponse
    ticket: str | None = Query(default=None),
    SAMLResponse: str | None = Query(default=None),  # noqa: N803
    format: str | None = Query(default=None, description="传 json 时返回 JSON（脚本兼容）"),
    db: Session = Depends(get_db),
):
    """第三方登录回调

    ## 为什么改 302

    原实现 `return ok(data)` 直接把 JSON 吐给浏览器 —— 用户在第三方登录成功后
    停在一个 JSON 页面上，登录流程端到端断裂（7 种跳转协议全部不可用）。
    现在改为 302 到前端落地页 `/login/oidc/complete?login_code=...`，
    由前端用一次性登录码换 JWT 并跳转原目标页；失败也 302 并在 query 带 `error`。

    保留 JSON 兼容：`Accept: application/json` 或 `?format=json` 时仍返回 JSON，
    便于自动化脚本与测试调用（不改既有集成方式）。
    """
    actual_code = code or ticket or SAMLResponse
    svc = AuthService(db, _public_base(request))
    json_mode = _wants_json(request, format)
    frontend_base = _frontend_base(request)

    def _redirect(params: dict[str, str]) -> RedirectResponse:
        url = f"{frontend_base}/login/oidc/complete?{urlencode(params)}"
        # 302 而非 307：浏览器会以 GET 重新请求，避免把 state 参数再 POST 一遍
        return RedirectResponse(url=url, status_code=302)

    try:
        data = svc.handle_callback(kind, actual_code, state, error)
        db.commit()
    except ValueError as e:
        db.rollback()
        msg = str(e)
        logger.warning("SSO 回调失败（%s）：%s", kind, msg)
        if json_mode:
            raise BusinessError(msg) from e
        return _redirect({"error": msg})

    if json_mode:
        return ok(data)

    params = {"login_code": str(data.get("login_code") or "")}
    redirect_after = data.get("redirect_after")
    if redirect_after:
        params["redirect_after"] = str(redirect_after)
    params["bind"] = "1" if data.get("bind") else "0"
    return _redirect(params)


@router.post("/token", summary="用一次性登录码换 JWT")
def exchange_token(body: ExchangeIn, request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    try:
        user = svc.exchange_login_code(body.code)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    token = create_access_token(user.id, {"pv": password_version(user)})
    return ok({"access_token": token, "token_type": "bearer"})


@router.post("/download-ticket", summary="换取一次性下载票据（60 秒）")
def download_ticket(user: CurrentUser = Depends(get_current_user)):
    """附件下载/预览专用短时票据

    为什么需要：`<a href>` / `<img src>` / `window.open` 无法带 Authorization
    头，早期只能把长期令牌拼在 `?token=` 上，令牌会进入浏览器历史与代理日志。
    改为先换一张 60 秒、一次性的票据，泄露影响面大幅缩小。
    前端（E 组）下载链路优先调用本接口。
    """
    return ok({"ticket": issue_download_ticket(user), "expires_in": DOWNLOAD_TICKET_TTL_SECONDS})


# ---------------------------------------------------------------- 当前用户
@router.get("/me", summary="当前登录用户信息")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    # CurrentUser 是精简会话对象（只含权限/范围），密码相关字段需回表查
    from app.models.system import SysUser

    row = db.get(SysUser, user.id)
    return ok(
        {
            "id": user.id,
            "staff_no": user.staff_no,
            "name": user.name,
            "college_id": user.college_id,
            "roles": user.roles,
            "permissions": sorted(user.permissions),
            "scopes": [{"type": s.scope_type, "id": s.scope_id} for s in user.scopes],
            "has_password": bool(row.password_hash) if row else False,
            "must_change_password": bool(row.must_change_password) if row else False,
        }
    )


# ---------------------------------------------------------------- 自助改密
class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128, description="当前密码")
    new_password: str = Field(min_length=8, max_length=128, description="新密码")


def _check_password_strength(pwd: str) -> None:
    """密码强度：至少 8 位，且同时含字母和数字

    早期只校验 min_length=6，Init@123456 这类弱口令能通过。
    """
    import re

    if len(pwd) < 8:
        raise BusinessError("新密码至少 8 位")
    if not re.search(r"[A-Za-z]", pwd):
        raise BusinessError("新密码需包含字母")
    if not re.search(r"\d", pwd):
        raise BusinessError("新密码需包含数字")
    if pwd.lower() in {"12345678", "password", "init@123456", "admin@123"}:
        raise BusinessError("新密码过于简单，请更换")


@router.post("/change-password", summary="修改自己的密码")
def change_password(
    body: ChangePasswordIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户自助改密

    - 必须校验旧密码，防止会话被劫持后直接改密
    - 新密码不能与旧密码相同
    - 改密成功后清掉 must_change_password
    """
    from datetime import UTC, datetime

    from app.core.security import hash_password, verify_password
    from app.models.system import SysUser

    row = db.get(SysUser, user.id)
    if row is None:
        raise BusinessError("用户不存在")

    # 纯第三方登录的账号可能没有本地密码
    if not row.password_hash:
        raise BusinessError("当前账号未设置本地密码，无需修改")

    if not verify_password(body.old_password, row.password_hash):
        raise BusinessError("原密码不正确")

    if body.old_password == body.new_password:
        raise BusinessError("新密码不能与原密码相同")

    _check_password_strength(body.new_password)

    row.password_hash = hash_password(body.new_password)
    row.must_change_password = False
    row.password_updated_at = datetime.now(UTC)
    db.commit()

    # 改密后旧令牌立即失效（deps 校验 iat < password_updated_at）。
    # 这里换发一个新令牌，避免用户改完密码被自己的安全策略踢下线。
    return ok({"changed": True, "access_token": create_access_token(row.id, {"pv": password_version(row)})})


@router.get("/me/identities", summary="已绑定的第三方账号")
def my_identities(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import select

    from app.models.auth import AuthIdentity, AuthProvider

    rows = db.execute(
        select(AuthIdentity, AuthProvider)
        .join(AuthProvider, AuthProvider.id == AuthIdentity.provider_id)
        .where(AuthIdentity.user_id == user.id)
    ).all()
    return ok(
        [
            {
                "provider": p.kind,
                "display_name": p.display_name,
                "subject": i.subject,
                "last_login_at": i.last_login_at,
            }
            for i, p in rows
        ]
    )


@router.get("/bind/{kind}/start", summary="绑定第三方账号（已登录）")
def start_bind(
    kind: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = AuthService(db, _public_base(request))
    try:
        data = svc.start_redirect_login(kind, redirect_after="/", bind_user_id=user.id)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    return ok(data)


# ---------------------------------------------------------------- 管理端
@router.get("/admin/providers", summary="认证方式配置列表", dependencies=[Depends(require_perm("system:role"))])
def admin_list(request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    return ok([svc.get_config(k) for k in svc.adapters.keys()])


@router.get("/admin/providers/{kind}", summary="单个认证方式配置", dependencies=[Depends(require_perm("system:role"))])
def admin_get(kind: str, request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    try:
        return ok(svc.get_config(kind))
    except ValueError as e:
        raise BusinessError(str(e)) from e


@router.put("/admin/providers/{kind}", summary="修改认证方式配置", dependencies=[Depends(require_perm("system:role"))])
def admin_put(
    kind: str, body: ProviderConfigIn, request: Request, db: Session = Depends(get_db)
):
    svc = AuthService(db, _public_base(request))
    try:
        data = svc.update_config(kind, body.model_dump(exclude_unset=True))
    except ValueError as e:
        db.rollback()
        raise BusinessError(str(e)) from e
    db.commit()
    return ok(data)


@router.post("/admin/providers/{kind}/test", summary="连通性测试", dependencies=[Depends(require_perm("system:role"))])
def admin_test(kind: str, request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    return ok(svc.test_provider(kind))


@router.delete("/admin/identities/{identity_id}", summary="解绑第三方账号", dependencies=[Depends(require_perm("system:user"))])
def admin_unbind(identity_id: int, db: Session = Depends(get_db)):
    from app.models.auth import AuthIdentity

    ident = db.get(AuthIdentity, identity_id)
    if ident is None:
        raise BusinessError("绑定记录不存在")
    db.delete(ident)
    db.commit()
    return ok()
