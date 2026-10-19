"""业务异常与统一错误响应"""
from __future__ import annotations

from fastapi import HTTPException, status


class BusinessError(HTTPException):
    """业务规则错误：400"""

    def __init__(self, detail: str, code: str = "BUSINESS_ERROR"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        self.code = code


class NotFoundError(HTTPException):
    """资源不存在：404"""

    def __init__(self, detail: str = "记录不存在"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ForbiddenError(HTTPException):
    """无权限：403"""

    def __init__(self, detail: str = "无权限执行该操作"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class UnauthorizedError(HTTPException):
    """未登录或凭证失效：401"""

    def __init__(self, detail: str = "未登录或登录已过期"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
