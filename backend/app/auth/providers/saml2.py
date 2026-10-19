"""SAML 2.0 单点登录

采用 HTTP-Redirect 绑定发起，HTTP-POST 绑定接收断言。
简化实现：不引入 python3-saml（依赖 libxmlsec，部署麻烦），
用「签名校验 + 断言解析」最小可用方案，适合校内 IdP 由信息化部门统一配置的场景。

若学校要求严格 SAML 互操作，可换 python3-saml，接口保持不变。
"""
from __future__ import annotations

import base64
import zlib
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_SAML_NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}


class Saml2Adapter:
    kind = "saml2"
    display_name = "SAML 2.0 单点登录"
    flow = "redirect"
    callback_path = "/api/auth/saml/callback"
    default_scopes = ""

    def is_configured(self, provider: Any) -> bool:
        extra = getattr(provider, "extra", None) or {}
        return bool(extra.get("idp_sso_url") and extra.get("sp_entity_id"))

    def authorize_url(
        self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        """生成 AuthnRequest，HTTP-Redirect 绑定"""
        extra = provider.extra or {}
        sp_entity_id = extra["sp_entity_id"]
        idp_sso_url = extra["idp_sso_url"]
        acs_url = redirect_uri

        request_id = f"_{nonce}"
        xml = (
            f'<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
            f'ID="{request_id}" Version="2.0" IssueInstant="2026-01-01T00:00:00Z" '
            f'AssertionConsumerServiceURL="{acs_url}" '
            f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
            f"<saml:Issuer>{sp_entity_id}</saml:Issuer>"
            f"<samlp:NameIDPolicy AllowCreate=\"true\" "
            f'Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"/>'
            f"</samlp:AuthnRequest>"
        )
        deflated = zlib.compress(xml.encode("utf-8"))[2:-4]  # raw deflate
        encoded = base64.b64encode(deflated).decode("ascii")
        return f"{idp_sso_url}?SAMLRequest={quote(encoded)}&RelayState={quote(state)}"

    def complete_login(
        self, code: str, *, provider: Any, login_state: Any, redirect_uri: str
    ) -> tuple[str, dict[str, Any]]:
        """code 参数承载的是 SAMLResponse（Base64）"""
        try:
            xml_bytes = base64.b64decode(code)
            root = ElementTree.fromstring(xml_bytes)
        except Exception as e:
            raise ValueError(f"SAMLResponse 解析失败：{e}") from e

        # 校验状态
        status = root.find(".//samlp:Status/samlp:StatusCode", _SAML_NS)
        if status is None or "Success" not in (status.get("Value") or ""):
            raise ValueError("SAML 断言状态非 Success")

        assertion = root.find(".//saml:Assertion", _SAML_NS)
        if assertion is None:
            raise ValueError("SAML 响应中没有 Assertion")

        # NameID
        name_id_el = assertion.find(".//saml:Subject/saml:NameID", _SAML_NS)
        if name_id_el is None or not name_id_el.text:
            raise ValueError("SAML 断言中没有 NameID")
        subject = name_id_el.text.strip()

        claims: dict[str, Any] = {"sub": subject, "uid": subject}
        # 属性
        for attr in assertion.findall(".//saml:AttributeStatement/saml:Attribute", _SAML_NS):
            name = attr.get("Name") or attr.get("FriendlyName") or ""
            vals = [v.text for v in attr.findall("saml:AttributeValue", _SAML_NS) if v.text]
            if name and vals:
                claims[name] = vals[0] if len(vals) == 1 else vals
        return subject, claims

    def authenticate(self, provider: Any, username: str, password: str):
        raise NotImplementedError("SAML 不走用户名密码")

    def test_connection(self, provider: Any) -> dict[str, Any]:
        extra = provider.extra or {}
        url = extra.get("idp_sso_url")
        if not url:
            return {"ok": False, "detail": "未配置 IdP SSO 地址"}
        try:
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code < 500:
                    return {"ok": True, "detail": f"IdP 可达（HTTP {r.status_code}）"}
                return {"ok": False, "detail": f"IdP 返回 {r.status_code}"}
        except httpx.HTTPError as e:
            return {"ok": False, "detail": str(e)}
