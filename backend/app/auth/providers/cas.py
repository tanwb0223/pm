"""CAS 2.0 / 3.0 认证（高校常用）"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import httpx

from app.auth.kinds import ClaimMapping

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_CAS_NS = "http://www.yale.edu/tp/cas"


class CasAdapter:
    kind = "cas"
    display_name = "CAS 单点登录"
    flow = "redirect"
    callback_path = "/api/auth/cas/callback"
    default_scopes = ""

    def is_configured(self, provider: Any) -> bool:
        return bool((provider.server_url or "").strip())

    def authorize_url(
        self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        base = provider.server_url.rstrip("/")
        # CAS 用 service 参数，state 塞进 service 的 query 里以便回调后取回
        sep = "&" if "?" in redirect_uri else "?"
        service = f"{redirect_uri}{sep}state={quote(state)}"
        return f"{base}/login?{urlencode({'service': service})}"

    def complete_login(
        self, code: str, *, provider: Any, login_state: Any, redirect_uri: str
    ) -> tuple[str, dict[str, Any]]:
        """CAS 的 code 其实是 ticket"""
        base = provider.server_url.rstrip("/")
        sep = "&" if "?" in redirect_uri else "?"
        service = f"{redirect_uri}{sep}state={quote(login_state.state)}"
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{base}/serviceValidate",
                params={"ticket": code, "service": service},
            )
            resp.raise_for_status()
            text = resp.text

        # 解析 CAS 2.0 的 XML 响应
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as e:
            raise ValueError(f"CAS 响应不是合法 XML：{e}") from e

        ns = {"cas": _CAS_NS}
        success = root.find("cas:authenticationSuccess", ns)
        if success is None:
            failure = root.find("cas:authenticationFailure", ns)
            msg = failure.text if failure is not None else "认证失败"
            raise ValueError(f"CAS 认证失败：{msg}")

        user_el = success.find("cas:user", ns)
        if user_el is None or not user_el.text:
            raise ValueError("CAS 响应中没有 user")

        claims: dict[str, Any] = {"sub": user_el.text, "uid": user_el.text}
        attrs = success.find("cas:attributes", ns)
        if attrs is not None:
            for child in attrs:
                tag = child.tag.split("}")[-1]
                vals = [c.text for c in child if c.text]
                claims[tag] = vals[0] if len(vals) == 1 else vals
        return user_el.text, claims

    def authenticate(self, provider: Any, username: str, password: str):
        raise NotImplementedError("CAS 不走用户名密码")

    def test_connection(self, provider: Any) -> dict[str, Any]:
        if not provider.server_url:
            return {"ok": False, "detail": "未配置 CAS 服务地址"}
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.get(f"{provider.server_url.rstrip('/')}/login")
                # CAS 登录页通常返回 200 或 302
                if r.status_code < 500:
                    return {"ok": True, "detail": f"CAS 服务可达（HTTP {r.status_code}）"}
                return {"ok": False, "detail": f"CAS 返回 {r.status_code}"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)}


def parse_cas_user(text: str) -> str | None:
    """兼容 CAS 1.0 的纯文本响应"""
    text = text.strip()
    if text.startswith("yes\n"):
        lines = text.splitlines()
        return lines[1].strip() if len(lines) > 1 else None
    return None
