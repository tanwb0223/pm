"""加密：敏感配置（client_secret、LDAP bind 密码）落库前加密

沿��� Octop 的 Fernet 方案，但密钥来自环境变量，避免密钥和数据同库。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    """用 secret_key 派生 Fernet 密钥（32 字节 urlsafe base64）"""
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise ValueError("配置密钥无法解密，请检查 PM_SECRET_KEY 是否变更") from e


def mask_secret(plain: str | None) -> str:
    """回显用：只显示末 4 位"""
    if not plain:
        return ""
    if len(plain) <= 4:
        return "*" * len(plain)
    return "*" * (len(plain) - 4) + plain[-4:]
