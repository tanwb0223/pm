"""通用 OAuth2 认证（非 OIDC，如 GitHub、Gitee、自建 OAuth2 服务）

与 OIDC 的区别：没有 discovery，没有 id_token，需要用 access_token 调 userinfo 接口。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.auth.crypto import decrypt_secret
from app.auth.kinds import ClaimMapping

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class OAuth2Adapter:
    kind = "oauth2"
    display_name = "OAuth2 登录"
    flow = "redirect"
    callback_path = "/api/auth/oauth2/callback"
    default_scopes = "read:user"

    def is_configured(self, provider: Any) -> bool:
        extra = getattr(provider, "extra", None) or {}
        return bool(
            (extra.get("authorize_url") or "").strip()
            and (extra.get("token_url") or "").strip()
            and (provider.client_id or "").strip()
        )

    def authorize_url(
        self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        extra = provider.extra or {}
        q = urlencode(
            {
                "response_type": "code",
                "client_id": provider.client_id,
                "redirect_uri": redirect_uri,
                "scope": provider.scopes or self.default_scopes,
                "state": state,
            }
        )
        return f"{extra['authorize_url']}?{q}"

    def complete_login(
        self, code: str, *, provider: Any, login_state: Any, redirect_uri: str
    ) -> tuple[str, dict[str, Any]]:
        extra = provider.extra or {}
        secret = decrypt_secret(provider.client_secret_enc) if provider.client_secret_enc else ""

        with httpx.Client(timeout=_TIMEOUT) as client:
            # 1) 换 access_token
            resp = client.post(
                extra["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": provider.client_id,
                    "client_secret": secret,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            tok = resp.json()
            access_token = tok.get("access_token")
            if not access_token:
                raise ValueError("token 响应中没有 access_token")

            # 2) 取用户信息
            userinfo_url = extra.get("userinfo_url")
            if not userinfo_url:
                raise ValueError("未配置 userinfo_url")
            ui = client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            ui.raise_for_status()
            claims = ui.json()

        # 3) 用户标识
        id_field = extra.get("user_id_field")
        if id_field and claims.get(id_field) is not None:
            subject = str(claims[id_field])
        else:
            subject = ClaimMapping.username(claims, extra)
        if not subject:
            raise ValueError("无法从 userinfo 中确定用户标识")
        return subject, claims

    def authenticate(self, provider: Any, username: str, password: str):
        raise NotImplementedError("OAuth2 不走用户名密码")

    def test_connection(self, provider: Any) -> dict[str, Any]:
        extra = provider.extra or {}
        authorize_url = extra.get("authorize_url")
        if not authorize_url:
            return {"ok": False, "detail": "未配置 authorize_url"}
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.get(authorize_url)
                if r.status_code < 500:
                    return {"ok": True, "detail": f"授权端点可达（HTTP {r.status_code}）"}
                return {"ok": False, "detail": f"授权端点返回 {r.status_code}"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)}
