"""国内三家办公平台的扫码/免登：飞书、钉钉、企业微信

三者都是「OAuth2 变体」：
- 飞书：app_id + app_secret，换 tenant_access_token / user_access_token
- 钉钉：app_key + app_secret，换 access_token，再取 userid
- 企业微信：corp_id + agent_id + secret，换 access_token，再取 userid

差异都在端点、参数名、返回结构上，用同一个基类 + 子类覆盖端点即可。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.auth.crypto import decrypt_secret
from app.auth.kinds import ClaimMapping

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class _OAuthVendorBase:
    flow = "redirect"
    kind = ""
    display_name = ""
    callback_path = ""

    def is_configured(self, provider: Any) -> bool:
        return bool((provider.client_id or "").strip() and provider.client_secret_enc)

    def _secret(self, provider: Any) -> str:
        return decrypt_secret(provider.client_secret_enc) if provider.client_secret_enc else ""

    def authorize_url(self, **kwargs) -> str:
        raise NotImplementedError

    def complete_login(self, code: str, *, provider: Any, login_state: Any, redirect_uri: str):
        raise NotImplementedError

    def authenticate(self, provider: Any, username: str, password: str):
        raise NotImplementedError("该方式不走用户名密码")

    def test_connection(self, provider: Any) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                self._preflight(client, provider)
            return {"ok": True, "detail": "应用凭证可换取 access_token"}
        except (httpx.HTTPError, ValueError) as e:
            return {"ok": False, "detail": str(e)}

    def _preflight(self, client: httpx.Client, provider: Any) -> None:
        raise NotImplementedError


class FeishuAdapter(_OAuthVendorBase):
    kind = "feishu"
    display_name = "飞书"
    callback_path = "/api/auth/feishu/callback"

    def is_configured(self, provider: Any) -> bool:
        return super().is_configured(provider)

    def authorize_url(self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        q = urlencode(
            {
                "app_id": provider.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"https://open.feishu.cn/open-apis/authen/v1/authorize?{q}"

    def complete_login(self, code: str, *, provider: Any, login_state: Any, redirect_uri: str):
        with httpx.Client(timeout=_TIMEOUT) as client:
            # 1) app_access_token
            r1 = client.post(
                "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                json={"app_id": provider.client_id, "app_secret": self._secret(provider)},
            )
            r1.raise_for_status()
            app_token = r1.json().get("app_access_token")
            if not app_token:
                raise ValueError("飞书未返回 app_access_token")

            # 2) user_access_token
            r2 = client.post(
                "https://open.feishu.cn/open-apis/authen/v1/access_token",
                headers={"Authorization": f"Bearer {app_token}"},
                json={"grant_type": "authorization_code", "code": code},
            )
            r2.raise_for_status()
            body = r2.json()
            if body.get("code") != 0:
                raise ValueError(f"飞书换取 token 失败：{body.get('msg')}")
            data = body.get("data") or {}
            user_token = data.get("access_token")
            if not user_token:
                raise ValueError("飞书未返回 user_access_token")

            # 3) 用户信息
            r3 = client.get(
                "https://open.feishu.cn/open-apis/authen/v1/user_info",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            r3.raise_for_status()
            info = (r3.json().get("data") or {})

        claims = {
            "sub": info.get("open_id") or info.get("user_id") or info.get("union_id"),
            "user_id": info.get("user_id"),
            "open_id": info.get("open_id"),
            "union_id": info.get("union_id"),
            "name": info.get("name"),
            "email": info.get("email"),
            "mobile": info.get("mobile"),
            "employee_no": info.get("employee_no"),
        }
        subject = ClaimMapping.username(claims, provider.extra) or claims["sub"]
        if not subject:
            raise ValueError("飞书未返回可用的用户标识")
        return str(subject), claims

    def _preflight(self, client: httpx.Client, provider: Any) -> None:
        r = client.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": provider.client_id, "app_secret": self._secret(provider)},
        )
        r.raise_for_status()
        if r.json().get("code") != 0:
            raise ValueError(r.json().get("msg", "飞书凭证无效"))


class DingTalkAdapter(_OAuthVendorBase):
    kind = "dingtalk"
    display_name = "钉钉"
    callback_path = "/api/auth/dingtalk/callback"

    def authorize_url(self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        q = urlencode(
            {
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "client_id": provider.client_id,
                "scope": "openid",
                "state": state,
                "prompt": "consent",
            }
        )
        return f"https://login.dingtalk.com/oauth2/auth?{q}"

    def complete_login(self, code: str, *, provider: Any, login_state: Any, redirect_uri: str):
        with httpx.Client(timeout=_TIMEOUT) as client:
            # 1) 换 user access token
            r1 = client.post(
                "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                json={
                    "clientId": provider.client_id,
                    "clientSecret": self._secret(provider),
                    "code": code,
                    "grantType": "authorization_code",
                },
            )
            r1.raise_for_status()
            tok = r1.json()
            access_token = tok.get("accessToken")
            if not access_token:
                raise ValueError("钉钉未返回 accessToken")

            # 2) 取用户信息
            r2 = client.get(
                "https://api.dingtalk.com/v1.0/contact/users/me",
                headers={"x-acs-dingtalk-access-token": access_token},
            )
            r2.raise_for_status()
            me = r2.json()

        claims = {
            "sub": me.get("unionId") or me.get("openId"),
            "unionid": me.get("unionId"),
            "openid": me.get("openId"),
            "nick": me.get("nick"),
            "name": me.get("nick"),
            "email": me.get("email"),
            "mobile": me.get("mobile"),
        }
        subject = ClaimMapping.username(claims, provider.extra) or claims["sub"]
        if not subject:
            raise ValueError("钉钉未返回可用的用户标识")
        return str(subject), claims

    def _preflight(self, client: httpx.Client, provider: Any) -> None:
        r = client.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={
                "appKey": provider.client_id,
                "appSecret": self._secret(provider),
            },
        )
        r.raise_for_status()
        if not r.json().get("accessToken"):
            raise ValueError("钉钉凭证无效")


class WeComAdapter(_OAuthVendorBase):
    kind = "wecom"
    display_name = "企业微信"
    callback_path = "/api/auth/wecom/callback"

    def authorize_url(self, *, provider: Any, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        extra = provider.extra or {}
        corp_id = extra.get("corp_id") or provider.client_id
        agent_id = extra.get("agent_id", "")
        q = urlencode(
            {
                "appid": corp_id,
                "agentid": agent_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": "snsapi_base",
            }
        )
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{q}#wechat_redirect"

    def complete_login(self, code: str, *, provider: Any, login_state: Any, redirect_uri: str):
        extra = provider.extra or {}
        corp_id = extra.get("corp_id") or provider.client_id
        with httpx.Client(timeout=_TIMEOUT) as client:
            # 1) access_token
            r1 = client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": corp_id, "corpsecret": self._secret(provider)},
            )
            r1.raise_for_status()
            body = r1.json()
            if body.get("errcode") != 0:
                raise ValueError(f"企业微信获取 token 失败：{body.get('errmsg')}")
            access_token = body["access_token"]

            # 2) code -> userid
            r2 = client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
                params={"access_token": access_token, "code": code},
            )
            r2.raise_for_status()
            info = r2.json()
            if info.get("errcode") != 0:
                raise ValueError(f"企业微信取用户失败：{info.get('errmsg')}")

            userid = info.get("userid") or info.get("UserId")
            claims: dict[str, Any] = {
                "sub": userid,
                "userid": userid,
                "openid": info.get("openid") or info.get("OpenId"),
            }

            # 3) 补姓名/邮箱
            if userid:
                r3 = client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                    params={"access_token": access_token, "userid": userid},
                )
                if r3.status_code == 200:
                    u = r3.json()
                    claims["name"] = u.get("name", "")
                    claims["email"] = u.get("email", "")
                    claims["mobile"] = u.get("mobile", "")

        subject = ClaimMapping.username(claims, provider.extra) or claims["sub"]
        if not subject:
            raise ValueError("企业微信未返回 userid")
        return str(subject), claims

    def _preflight(self, client: httpx.Client, provider: Any) -> None:
        extra = provider.extra or {}
        corp_id = extra.get("corp_id") or provider.client_id
        r = client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": self._secret(provider)},
        )
        r.raise_for_status()
        if r.json().get("errcode") != 0:
            raise ValueError(r.json().get("errmsg", "企业微信凭证无效"))
