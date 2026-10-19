"""本地账号密码认证（一期默认方式）"""
from __future__ import annotations

from typing import Any

from app.auth.kinds import ClaimMapping


class PasswordAdapter:
    kind = "password"
    display_name = "账号密码"
    flow = "post"
    callback_path = ""

    def is_configured(self, provider: Any) -> bool:
        # 本地认证不需要额外配置
        return True

    def authorize_url(self, **kwargs) -> str:
        raise NotImplementedError("password 认证不走跳转")

    def complete_login(self, code: str, **kwargs) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError("password 认证不走回调")

    def authenticate(
        self, provider: Any, username: str, password: str
    ) -> tuple[str, dict[str, Any]]:
        """由 SsoService 查库校验密码，这里只返回标识"""
        return username, {"sub": username, "auth_kind": "password"}

    def test_connection(self, provider: Any) -> dict[str, Any]:
        return {"ok": True, "detail": "本地认证无需连通性测试"}


class LdapAdapter:
    """LDAP / AD 域认证

    配置项（存 sso_provider.extra 或独立字段）：
      server_url   ldap://dc.school.edu.cn:389  或 ldaps://...:636
      base_dn      ou=users,dc=school,dc=edu,dc=cn
      bind_dn      服务账号（可选，匿名绑定则留空）
      bind_password 服务账号密码（加密存储）
      user_filter  (uid={username})  或 (sAMAccountName={username})
      use_ssl      是否 ldaps
      start_tls    是否 StartTLS
    """

    kind = "ldap"
    display_name = "LDAP / AD 域"
    flow = "post"
    callback_path = ""

    def is_configured(self, provider: Any) -> bool:
        extra = getattr(provider, "extra", None) or {}
        return bool(extra.get("server_url") and extra.get("base_dn"))

    def authorize_url(self, **kwargs) -> str:
        raise NotImplementedError("ldap 认证不走跳转")

    def complete_login(self, code: str, **kwargs) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError("ldap 认证不走回调")

    def authenticate(
        self, provider: Any, username: str, password: str
    ) -> tuple[str, dict[str, Any]]:
        from ldap3 import ALL, SIMPLE, Connection, Server
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        from app.auth.crypto import decrypt_secret

        extra = getattr(provider, "extra", None) or {}
        server_url = extra.get("server_url", "")
        base_dn = extra.get("base_dn", "")
        user_filter = extra.get("user_filter") or "(uid={username})"
        bind_dn = extra.get("bind_dn") or ""
        bind_password_enc = getattr(provider, "client_secret_enc", None)
        start_tls = bool(extra.get("start_tls"))

        if not server_url or not base_dn:
            raise ValueError("LDAP 配置不完整")

        safe_user = escape_filter_chars(username)
        search_filter = user_filter.replace("{username}", safe_user)

        try:
            server = Server(server_url, get_info=ALL, use_ssl=server_url.startswith("ldaps://"))
            # 1) 用服务账号（或匿名）查 DN
            if bind_dn:
                bind_pwd = decrypt_secret(bind_password_enc) if bind_password_enc else ""
                conn = Connection(server, user=bind_dn, password=bind_pwd, auto_bind=True)
            else:
                conn = Connection(server, auto_bind=True)
            if start_tls:
                conn.start_tls()
            conn.search(base_dn, search_filter, attributes=["cn", "mail", "displayName", "sAMAccountName"])
            if not conn.entries:
                raise ValueError("用户名或密码错误")
            entry = conn.entries[0]
            user_dn = entry.entry_dn
            claims = {
                "sub": str(entry.entry_dn),
                "uid": username,
                "cn": str(entry.cn) if hasattr(entry, "cn") else username,
                "mail": str(entry.mail) if hasattr(entry, "mail") else None,
            }
            conn.unbind()
            # 2) 用查到的 DN + 用户密码再绑一次，验证密码
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
        except LDAPException as e:
            raise ValueError(f"LDAP 认证失败：{e}") from e

        subject = ClaimMapping.username(claims, extra) or username
        return subject, claims

    def test_connection(self, provider: Any) -> dict[str, Any]:
        from ldap3 import ALL, Connection, Server
        from ldap3.core.exceptions import LDAPException

        from app.auth.crypto import decrypt_secret

        extra = getattr(provider, "extra", None) or {}
        server_url = extra.get("server_url", "")
        bind_dn = extra.get("bind_dn") or ""
        if not server_url:
            return {"ok": False, "detail": "未配置 server_url"}
        try:
            server = Server(server_url, get_info=ALL, use_ssl=server_url.startswith("ldaps://"))
            if bind_dn:
                enc = getattr(provider, "client_secret_enc", None)
                pwd = decrypt_secret(enc) if enc else ""
                conn = Connection(server, user=bind_dn, password=pwd, auto_bind=True)
                conn.unbind()
                return {"ok": True, "detail": "LDAP 服务账号绑定成功"}
            conn = Connection(server, auto_bind=True)
            conn.unbind()
            return {"ok": True, "detail": "LDAP 匿名绑定成功"}
        except LDAPException as e:
            return {"ok": False, "detail": str(e)}
