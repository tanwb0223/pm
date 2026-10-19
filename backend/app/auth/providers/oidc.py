"""OIDC 认证（标准 OpenID Connect，兼容 Keycloak / Authing / Casdoor / Azure AD 等）

参考 Octop 的 OidcAdapter：discovery + PKCE + nonce + ID Token 验签。
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.auth.crypto import decrypt_secret

_DISCOVERY_PATH = "/.well-known/openid-configuration"
_CACHE_TTL = 3600
_ALLOWED_ALGS = ("RS256", "ES256", "RS384", "RS512")
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class _DiscoveryCache:
    """进程内缓存 discovery 文档"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, dict]] = {}

    def get(self, issuer: str) -> dict:
        issuer = issuer.rstrip("/")
        ent = self._entries.get(issuer)
        now = time.monotonic()
        if ent and ent[0] > now:
            return ent[1]
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{issuer}{_DISCOVERY_PATH}")
            resp.raise_for_status()
            doc = resp.json()
        doc_issuer = str(doc.get("issuer", "")).rstrip("/")
        if doc_issuer != issuer:
            raise ValueError("OIDC discovery 的 issuer 与配置不一致")
        self._entries[issuer] = (now + _CACHE_TTL, doc)
        return doc

    def invalidate(self, issuer: str) -> None:
        self._entries.pop(issuer.rstrip("/"), None)


class OidcAdapter:
    kind = "oidc"
    display_name = "OIDC 单点登录"
    flow = "redirect"
    callback_path = "/api/auth/oidc/callback"
    default_scopes = "openid profile email"

    _discovery = _DiscoveryCache()

    def is_configured(self, provider: Any) -> bool:
        return bool((provider.issuer or "").strip() and (provider.client_id or "").strip())

    def authorize_url(
        self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        doc = self._discovery.get(provider.issuer)
        endpoint = doc.get("authorization_endpoint")
        if not endpoint:
            raise ValueError("discovery 中缺少 authorization_endpoint")
        q = urlencode(
            {
                "response_type": "code",
                "client_id": provider.client_id,
                "redirect_uri": redirect_uri,
                "scope": provider.scopes or self.default_scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{q}"

    def complete_login(
        self, code: str, *, provider: Any, login_state: Any, redirect_uri: str
    ) -> tuple[str, dict[str, Any]]:
        doc = self._discovery.get(provider.issuer)
        token_endpoint = doc.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("discovery 中缺少 token_endpoint")

        secret = ""
        if provider.client_secret_enc:
            secret = decrypt_secret(provider.client_secret_enc)

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": provider.client_id,
                    "client_secret": secret,
                    "code_verifier": login_state.code_verifier,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            tokens = resp.json()

            id_token = tokens.get("id_token")
            if not id_token:
                raise ValueError("token 响应中没有 id_token")
            claims = self._verify_id_token(
                id_token,
                jwks_uri=doc.get("jwks_uri", ""),
                issuer=doc.get("issuer", provider.issuer),
                client_id=provider.client_id,
                nonce=login_state.nonce,
                client=client,
            )

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise ValueError("ID Token 中没有 sub")
        return subject, claims

    @staticmethod
    def _verify_id_token(
        id_token: str, *, jwks_uri: str, issuer: str, client_id: str, nonce: str, client: httpx.Client
    ) -> dict[str, Any]:
        header = jwt.get_unverified_header(id_token)
        alg = header.get("alg")
        if alg not in _ALLOWED_ALGS:
            raise jwt.InvalidTokenError(f"不支持的签名算法：{alg}")
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise jwt.InvalidTokenError("ID Token 缺少 kid")

        resp = client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list):
            raise jwt.InvalidTokenError("JWKS 中没有 keys")

        key = None
        for k in keys:
            if isinstance(k, dict) and k.get("kid") == kid:
                key = jwt.PyJWK.from_dict(k).key
                break
        if key is None:
            raise jwt.InvalidTokenError("找不到对应的签名公钥")

        claims = jwt.decode(
            id_token,
            key,
            algorithms=list(_ALLOWED_ALGS),
            audience=client_id,
            issuer=issuer,
            leeway=60,
            options={"require": ["exp", "iat"]},
        )
        if claims.get("nonce") != nonce:
            raise jwt.InvalidTokenError("nonce 校验失败")
        return claims

    def authenticate(self, provider: Any, username: str, password: str):
        raise NotImplementedError("OIDC 不走用户名密码")

    def test_connection(self, provider: Any) -> dict[str, Any]:
        self._discovery.invalidate(provider.issuer)
        try:
            doc = self._discovery.get(provider.issuer)
            jwks_uri = doc.get("jwks_uri")
            if not jwks_uri:
                return {"ok": False, "detail": "discovery 中缺少 jwks_uri"}
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.get(jwks_uri)
                r.raise_for_status()
                if not isinstance(r.json().get("keys"), list):
                    return {"ok": False, "detail": "JWKS 响应无 keys"}
            return {"ok": True, "detail": "discovery 与 JWKS 均可访问"}
        except (httpx.HTTPError, ValueError) as e:
            return {"ok": False, "detail": str(e)}
