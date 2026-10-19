"""认证服务：统一编排各种协议的登录流程（对齐 Octop 的 SsoService）"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.auth.flow import (
    build_redirect_uri,
    new_login_code,
    new_nonce,
    new_pkce_pair,
    new_state,
    safe_redirect_after,
)
from app.auth.kinds import AUTH_KINDS, BROWSER_FLOW_KINDS, ClaimMapping
from app.auth.providers import get_adapter, get_adapters
from app.core.security import hash_password, verify_password
from app.models.auth import AuthIdentity, AuthLoginState, AuthProvider
from app.models.system import SysRole, SysUser

logger = logging.getLogger(__name__)

LOGIN_STATE_TTL = 600       # 10 分钟
LOGIN_CODE_TTL = 60         # 一次性登录码 60 秒


@dataclass
class LoginResult:
    user: SysUser
    is_new: bool = False


class AuthService:
    def __init__(self, db: Session, public_base: str):
        self.db = db
        self.public_base = public_base.rstrip("/")
        self.adapters = get_adapters()

    # ------------------------------------------------------------ 查询
    def list_providers(self, only_enabled: bool = False) -> list[AuthProvider]:
        stmt = select(AuthProvider).order_by(AuthProvider.sort)
        if only_enabled:
            stmt = stmt.where(AuthProvider.enabled.is_(True))
        return list(self.db.scalars(stmt))

    def get_provider(self, kind: str) -> AuthProvider | None:
        return self.db.scalar(select(AuthProvider).where(AuthProvider.kind == kind))

    def public_providers(self) -> list[dict[str, Any]]:
        """前端登录页用：只返回启用的、配置完整的"""
        out = []
        for p in self.list_providers(only_enabled=True):
            adapter = self.adapters.get(p.kind)
            if adapter is None:
                continue
            try:
                configured = adapter.is_configured(p)
            except Exception:
                configured = False
            if not configured:
                continue
            out.append(
                {
                    "kind": p.kind,
                    "display_name": p.display_name or adapter.display_name,
                    "flow": adapter.flow,
                    "callback_path": adapter.callback_path,
                }
            )
        return out

    # ------------------------------------------------------------ 配置管理
    def get_config(self, kind: str) -> dict[str, Any]:
        p = self.get_provider(kind)
        adapter = self.adapters.get(kind)
        if p is None:
            raise ValueError(f"未初始化该认证方式：{kind}")
        callback = adapter.callback_path if adapter else ""
        redirect_uri = build_redirect_uri(self.public_base, callback) if callback else ""
        return {
            "kind": p.kind,
            "enabled": p.enabled,
            "display_name": p.display_name,
            "issuer": p.issuer,
            "client_id": p.client_id,
            "has_client_secret": bool(p.client_secret_enc),
            "scopes": p.scopes,
            "server_url": p.server_url,
            "extra": p.extra or {},
            "auto_create_user": p.auto_create_user,
            "default_role_id": p.default_role_id,
            "redirect_uri": redirect_uri,
            "configured": adapter.is_configured(p) if adapter else False,
        }

    def update_config(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        p = self.get_provider(kind)
        if p is None:
            raise ValueError(f"未知认证方式：{kind}")

        if "enabled" in body:
            p.enabled = bool(body["enabled"])
        if "display_name" in body:
            p.display_name = str(body["display_name"])[:64]
        if "issuer" in body:
            p.issuer = str(body["issuer"] or "")
        if "client_id" in body:
            p.client_id = str(body["client_id"] or "")
        if "scopes" in body:
            p.scopes = str(body["scopes"] or "")
        if "server_url" in body:
            p.server_url = str(body["server_url"] or "")
        if "extra" in body and isinstance(body["extra"], dict):
            p.extra = body["extra"]
        if "auto_create_user" in body:
            p.auto_create_user = bool(body["auto_create_user"])
        if "default_role_id" in body:
            v = body["default_role_id"]
            p.default_role_id = int(v) if v else None

        # 密钥单独处理：传空字符串表示不修改
        secret = body.get("client_secret")
        if isinstance(secret, str) and secret.strip():
            p.client_secret_enc = encrypt_secret(secret.strip())

        self.db.flush()
        # 启用前校验配置完整性
        adapter = self.adapters.get(kind)
        if p.enabled and adapter and not adapter.is_configured(p):
            p.enabled = False
            self.db.flush()
            raise ValueError("配置不完整，已保存但未启用")
        return self.get_config(kind)

    def test_provider(self, kind: str) -> dict[str, Any]:
        p = self.get_provider(kind)
        if p is None:
            return {"ok": False, "detail": "未初始化该认证方式"}
        adapter = self.adapters.get(kind)
        if adapter is None:
            return {"ok": False, "detail": "无对应适配器"}
        try:
            return adapter.test_connection(p)
        except Exception as e:
            return {"ok": False, "detail": str(e)}

    # ------------------------------------------------------------ 本地密码登录
    def login_password(self, username: str, password: str) -> SysUser:
        user = self.db.scalar(select(SysUser).where(SysUser.staff_no == username))
        if user is None or not verify_password(password, user.password_hash):
            # 统一错误信息，不暴露用户是否存在
            raise ValueError("用户名或密码错误")
        if user.status != "ACTIVE":
            raise ValueError("账号已停用，请联系管理员")
        user.last_login_at = datetime.now(UTC)
        self.db.flush()
        return user

    def login_ldap(self, username: str, password: str) -> SysUser:
        p = self.get_provider("ldap")
        if p is None or not p.enabled:
            raise ValueError("未启用 LDAP 认证")
        adapter = get_adapter("ldap")
        subject, claims = adapter.authenticate(p, username, password)
        return self._resolve_or_create(p, subject, claims)

    # ------------------------------------------------------------ 跳转登录
    def start_redirect_login(
        self, kind: str, redirect_after: str | None = None, bind_user_id: int | None = None
    ) -> dict[str, Any]:
        if kind not in BROWSER_FLOW_KINDS:
            raise ValueError(f"{kind} 不支持跳转登录")
        p = self.get_provider(kind)
        if p is None or not p.enabled:
            raise ValueError("该登录方式未启用")
        adapter = get_adapter(kind)
        if not adapter.is_configured(p):
            raise ValueError("该登录方式配置不完整")

        state = new_state()
        nonce = new_nonce()
        verifier, challenge = new_pkce_pair()
        redirect_uri = build_redirect_uri(self.public_base, adapter.callback_path)

        try:
            url = adapter.authorize_url(
                provider=p,
                state=state,
                nonce=nonce,
                code_challenge=challenge,
                redirect_uri=redirect_uri,
            )
        except Exception as e:
            # 第三方服务不可达、discovery 失败等
            logger.warning("生成授权地址失败（%s）：%s", kind, e)
            raise ValueError(f"无法连接认证服务：{e}") from e

        self.db.add(
            AuthLoginState(
                state=state,
                provider_id=p.id,
                nonce=nonce,
                code_verifier=verifier,
                redirect_after=safe_redirect_after(redirect_after),
                bind_user_id=bind_user_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=LOGIN_STATE_TTL),
            )
        )
        self.db.flush()
        return {"authorization_url": url, "state": state}

    def handle_callback(
        self, kind: str, code: str | None, state: str | None, error: str | None
    ) -> dict[str, Any]:
        """处理回调：校验 state → 换用户 → 发放一次性登录码"""
        self._cleanup_expired()
        if error:
            raise ValueError("用户取消授权" if error == "access_denied" else f"授权失败：{error}")
        if not state:
            raise ValueError("缺少 state 参数")

        login_state = self._take_login_state(state)
        if login_state is None:
            raise ValueError("登录请求已失效，请重新登录")

        p = self.db.get(AuthProvider, login_state.provider_id)
        if p is None or not p.enabled:
            raise ValueError("该登录方式已停用")
        adapter = get_adapter(kind)
        if not adapter.is_configured(p) or not code:
            raise ValueError("登录配置不完整或缺少授权码")

        redirect_uri = build_redirect_uri(self.public_base, adapter.callback_path)
        try:
            subject, claims = adapter.complete_login(
                code, provider=p, login_state=login_state, redirect_uri=redirect_uri
            )
        except Exception as e:
            logger.warning("认证回调处理失败（%s）：%s", kind, e)
            raise ValueError(f"认证失败：{e}") from e

        if login_state.bind_user_id:
            user = self._bind_identity(p, login_state.bind_user_id, subject, claims)
        else:
            user = self._resolve_or_create(p, subject, claims, claims_source=claims)

        login_code = new_login_code()
        login_state.login_code = login_code
        login_state.user_id = user.id
        login_state.consumed_at = None
        login_state.expires_at = datetime.now(UTC) + timedelta(seconds=LOGIN_CODE_TTL)
        self.db.flush()

        return {
            "login_code": login_code,
            "redirect_after": login_state.redirect_after,
            "bind": bool(login_state.bind_user_id),
        }

    def exchange_login_code(self, login_code: str) -> SysUser:
        st = self.db.scalar(
            select(AuthLoginState).where(
                AuthLoginState.login_code == login_code,
                AuthLoginState.consumed_at.is_(None),
                AuthLoginState.expires_at > datetime.now(UTC),
            )
        )
        if st is None or st.user_id is None:
            raise ValueError("登录码无效或已过期")
        st.consumed_at = datetime.now(UTC)
        user = self.db.get(SysUser, st.user_id)
        if user is None or user.status != "ACTIVE":
            raise ValueError("账号不可用")
        user.last_login_at = datetime.now(UTC)
        self.db.flush()
        return user

    # ------------------------------------------------------------ 内部
    def _take_login_state(self, state: str) -> AuthLoginState | None:
        st = self.db.scalar(
            select(AuthLoginState).where(
                AuthLoginState.state == state,
                AuthLoginState.consumed_at.is_(None),
                AuthLoginState.expires_at > datetime.now(UTC),
                AuthLoginState.login_code.is_(None),
            )
        )
        if st is not None:
            st.consumed_at = datetime.now(UTC)   # 原子占用
            self.db.flush()
        return st

    def _cleanup_expired(self) -> None:
        from sqlalchemy import delete

        self.db.execute(
            delete(AuthLoginState).where(AuthLoginState.expires_at <= datetime.now(UTC))
        )

    def _resolve_or_create(
        self,
        provider: AuthProvider,
        subject: str,
        claims: dict[str, Any],
        claims_source: dict | None = None,
    ) -> SysUser:
        """按第三方身份找用户；找不到且允许自动创建则建号"""
        ident = self.db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider_id == provider.id,
                AuthIdentity.subject == subject,
            )
        )
        if ident is not None:
            ident.last_login_at = datetime.now(UTC)
            ident.claims = claims or {}
            user = self.db.get(SysUser, ident.user_id)
            if user is None or user.status != "ACTIVE":
                raise ValueError("账号不可用")
            return user

        # 未绑定：尝试按工号/邮箱匹配已有用户
        username = ClaimMapping.username(claims, provider.extra)
        if username:
            user = self.db.scalar(select(SysUser).where(SysUser.staff_no == username))
            if user is not None:
                self._bind_identity(provider, user.id, subject, claims)
                return user

        email = ClaimMapping.email(claims, provider.extra)
        if email:
            user = self.db.scalar(select(SysUser).where(SysUser.email == email))
            if user is not None:
                self._bind_identity(provider, user.id, subject, claims)
                return user

        if not provider.auto_create_user:
            raise ValueError("系统中没有对应账号，请联系管理员开通")
        if not username:
            raise ValueError("第三方未返回工号/账号标识，无法自动创建用户")

        # 自动创建
        role_id = provider.default_role_id
        if role_id is None:
            viewer = self.db.scalar(select(SysRole).where(SysRole.code == "VIEWER"))
            role_id = viewer.id if viewer else None

        user = SysUser(
            staff_no=username,
            name=ClaimMapping.name(claims, provider.extra) or username,
            email=email,
            password_hash=None,     # 第三方登录用户无本地密码
            status="ACTIVE",
        )
        self.db.add(user)
        self.db.flush()

        if role_id:
            from app.models.system import SysUserRole

            self.db.add(
                SysUserRole(
                    user_id=user.id,
                    role_id=role_id,
                    scope_type="SELF",
                    scope_id=None,
                )
            )
            self.db.flush()

        self._bind_identity(provider, user.id, subject, claims)
        logger.info("自动创建用户：%s（来源 %s）", username, provider.kind)
        return user

    def _bind_identity(
        self, provider: AuthProvider, user_id: int, subject: str, claims: dict[str, Any]
    ) -> SysUser:
        exists = self.db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider_id == provider.id,
                AuthIdentity.subject == subject,
            )
        )
        if exists is not None and exists.user_id != user_id:
            raise ValueError("该第三方账号已绑定其他用户")
        if exists is None:
            self.db.add(
                AuthIdentity(
                    user_id=user_id,
                    provider_id=provider.id,
                    subject=subject,
                    claims=claims or {},
                    last_login_at=datetime.now(UTC),
                )
            )
            self.db.flush()
        user = self.db.get(SysUser, user_id)
        if user is None:
            raise ValueError("用户不存在")
        return user
