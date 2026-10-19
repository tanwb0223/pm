"""PKCE、state、nonce 等登录流程辅助"""
from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlparse


def new_pkce_pair() -> tuple[str, str]:
    """返回 (code_verifier, code_challenge)，S256 方法"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def new_login_code() -> str:
    return secrets.token_urlsafe(32)


def safe_redirect_after(raw: str | None, default: str = "/") -> str:
    """防开放重定向：只允许站内相对路径"""
    if not raw:
        return default
    raw = raw.strip()
    # 必须是 / 开头且不是 //（协议相对 URL）
    if not raw.startswith("/") or raw.startswith("//"):
        return default
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return default
    return raw


def build_redirect_uri(public_base: str, callback_path: str) -> str:
    return public_base.rstrip("/") + callback_path
