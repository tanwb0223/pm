"""认证 API：多协议登录、配置管理、账号绑定"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.service import AuthService
from app.core.deps import CurrentUser, get_current_user, require_perm
from app.core.exceptions import BusinessError
from app.core.response import ok
from app.core.security import create_access_token
from app.db.session import get_db

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _public_base(request: Request) -> str:
    """推导对外基础地址，支持反向代理"""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


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
    try:
        user = svc.login_password(body.username, body.password)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    token = create_access_token(user.id)
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
    try:
        user = svc.login_ldap(body.username, body.password)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    token = create_access_token(user.id)
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
    db: Session = Depends(get_db),
):
    actual_code = code or ticket or SAMLResponse
    svc = AuthService(db, _public_base(request))
    try:
        data = svc.handle_callback(kind, actual_code, state, error)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    return ok(data)


@router.post("/token", summary="用一次性登录码换 JWT")
def exchange_token(body: ExchangeIn, request: Request, db: Session = Depends(get_db)):
    svc = AuthService(db, _public_base(request))
    try:
        user = svc.exchange_login_code(body.code)
    except ValueError as e:
        raise BusinessError(str(e)) from e
    db.commit()
    token = create_access_token(user.id)
    return ok({"access_token": token, "token_type": "bearer"})


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
    return ok({"changed": True})


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
