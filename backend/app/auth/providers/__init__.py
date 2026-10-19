"""认证适配器注册表（对齐 Octop 的 build_adapters）"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.auth.kinds import AUTH_KINDS, AuthProvider

if TYPE_CHECKING:
    pass


def build_adapters() -> dict[str, AuthProvider]:
    from app.auth.providers.cas import CasAdapter
    from app.auth.providers.cn_vendors import DingTalkAdapter, FeishuAdapter, WeComAdapter
    from app.auth.providers.oauth2 import OAuth2Adapter
    from app.auth.providers.oidc import OidcAdapter
    from app.auth.providers.password_ldap import LdapAdapter, PasswordAdapter
    from app.auth.providers.saml2 import Saml2Adapter

    adapters: dict[str, AuthProvider] = {
        "password": PasswordAdapter(),
        "oidc": OidcAdapter(),
        "oauth2": OAuth2Adapter(),
        "cas": CasAdapter(),
        "saml2": Saml2Adapter(),
        "ldap": LdapAdapter(),
        "feishu": FeishuAdapter(),
        "dingtalk": DingTalkAdapter(),
        "wecom": WeComAdapter(),
    }
    # 校验注册完整性
    missing = set(AUTH_KINDS) - set(adapters)
    if missing:
        raise RuntimeError(f"缺少认证适配器：{missing}")
    return adapters


_ADAPTERS: dict[str, AuthProvider] | None = None


def get_adapters() -> dict[str, AuthProvider]:
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = build_adapters()
    return _ADAPTERS


def get_adapter(kind: str) -> AuthProvider:
    adapters = get_adapters()
    adapter = adapters.get(kind)
    if adapter is None:
        raise ValueError(f"未知的认证方式：{kind}")
    return adapter


__all__ = ["build_adapters", "get_adapters", "get_adapter", "AUTH_KINDS"]
