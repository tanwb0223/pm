"""认证方式定义与常量

参考 Octop 的 IdentityProvider 适配器模式：
- 每种认证协议实现统一的 Protocol
- 通过注册表按 kind 取适配器
- 配置存库（密钥加密），不改代码即可切换
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------- 认证方式
# 密码登录放最前（一期默认），其余按需启用
AUTH_KINDS = (
    "password",   # 本地账号密码
    "oidc",       # 标准 OIDC（含 Keycloak、Authing、Casdoor 等）
    "oauth2",     # 通用 OAuth2（非 OIDC）
    "cas",        # CAS 2.0/3.0（很多高校用的）
    "saml2",      # SAML 2.0
    "ldap",       # LDAP / AD 域认证
    "feishu",     # 飞书
    "dingtalk",   # 钉钉
    "wecom",      # 企业微信
)

# 走浏览器跳转的协议（授权码流程）
BROWSER_FLOW_KINDS = ("oidc", "oauth2", "cas", "saml2", "feishu", "dingtalk", "wecom")
# 走表单直连的协议（用户名密码提交给第三方）
DIRECT_POST_KINDS = ("password", "ldap")


@runtime_checkable
class AuthProvider(Protocol):
    """认证提供方适配器契约（对齐 Octop 的 IdentityProvider）"""

    kind: str
    display_name: str
    flow: str  # "password" | "redirect" | "post"

    def is_configured(self, provider: Any) -> bool:
        """配置是否完整到可以启用"""
        ...

    def authorize_url(
        self,
        *,
        provider: Any,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str:
        """生成跳转地址（仅 redirect 流程）"""
        ...

    def complete_login(
        self,
        code: str,
        *,
        provider: Any,
        login_state: Any,
        redirect_uri: str,
    ) -> tuple[str, dict[str, Any]]:
        """用 code 换用户标识 + 声明（仅 redirect 流程）"""
        ...

    def authenticate(self, provider: Any, username: str, password: str) -> tuple[str, dict[str, Any]]:
        """直接校验用户名密码（仅 password/ldap 流程）"""
        ...

    def test_connection(self, provider: Any) -> dict[str, Any]:
        """连通性自检"""
        ...


# ---------------------------------------------------------------- 用户映射
class ClaimMapping:
    """把第三方返回的声明映射到系统用户字段

    不同协议的字段名不一样，统一用配置里的 extra 指定：
      - 工号: claim_username（默认 sub / uid / mail 等按协议兜底）
      - 姓名: claim_name（默认 name / displayName / cn）
      - 邮箱: claim_email（默认 email / mail）
    """

    DEFAULT_USERNAME = ("staff_no", "employee_id", "uid", "username", "preferred_username", "mail", "email", "sub")
    DEFAULT_NAME = ("name", "displayName", "cn", "real_name", "nickname")
    DEFAULT_EMAIL = ("email", "mail")

    @classmethod
    def pick(cls, claims: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for k in keys:
            v = claims.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    @classmethod
    def username(cls, claims: dict[str, Any], extra: dict | None = None) -> str | None:
        extra = extra or {}
        custom = extra.get("claim_username")
        if custom and isinstance(claims.get(custom), str):
            return claims[custom].strip() or None
        return cls.pick(claims, cls.DEFAULT_USERNAME)

    @classmethod
    def name(cls, claims: dict[str, Any], extra: dict | None = None) -> str | None:
        extra = extra or {}
        custom = extra.get("claim_name")
        if custom and isinstance(claims.get(custom), str):
            return claims[custom].strip() or None
        return cls.pick(claims, cls.DEFAULT_NAME)

    @classmethod
    def email(cls, claims: dict[str, Any], extra: dict | None = None) -> str | None:
        extra = extra or {}
        custom = extra.get("claim_email")
        if custom and isinstance(claims.get(custom), str):
            return claims[custom].strip() or None
        return cls.pick(claims, cls.DEFAULT_EMAIL)
