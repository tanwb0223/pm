"""统一响应结构"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Resp(BaseModel, Generic[T]):
    """统一响应：{"code": 0, "message": "ok", "data": ...}"""

    code: int = 0
    message: str = "ok"
    data: T | None = None


class PageData(BaseModel, Generic[T]):
    """分页数据"""

    items: list[T]
    total: int
    page: int
    page_size: int


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}


def page(items: list, total: int, page_no: int, page_size: int) -> dict:
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "items": items,
            "total": total,
            "page": page_no,
            "page_size": page_size,
        },
    }
