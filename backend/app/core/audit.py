"""审计日志：中间件 + 后台异步写入

只记录写操作（POST/PUT/PATCH/DELETE），避免日志爆炸。
审计失败不影响主流程。
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.db.session import SessionLocal
from app.models.system import AuditLog

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 不需要审计的路径
SKIP_PATHS = {"/api/auth/login", "/api/auth/login/ldap", "/api/auth/token"}

# 路径 → 动作名（更易读）
ACTION_MAP = [
    ("/api/auth/", "AUTH"),
    ("/api/majors", "PROJECT"),
    ("/api/colleges", "COLLEGE"),
    ("/api/subs", "SUB_PROJECT"),
    ("/api/progress", "PROGRESS"),
    ("/api/alerts", "ALERT"),
    ("/api/reports", "REPORT"),
    ("/api/risks", "RISK"),
    ("/api/export", "EXPORT"),
    ("/api/system", "SYSTEM"),
]


def _action_of(path: str, method: str) -> str:
    for prefix, name in ACTION_MAP:
        if path.startswith(prefix):
            return f"{name}_{method}"
    return method


def _target_of(path: str) -> tuple[str | None, int | None]:
    """从路径猜 target_type / target_id"""
    parts = [p for p in path.split("/") if p and p != "api"]
    if not parts:
        return None, None
    target_type = parts[0]
    target_id = None
    if len(parts) >= 2:
        try:
            target_id = int(parts[-1])
        except ValueError:
            target_id = None
    return target_type, target_id


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        try:
            if request.method not in WRITE_METHODS:
                return response
            path = request.url.path
            if path in SKIP_PATHS:
                return response
            # 只记录成功与业务失败的写操作（4xx 也记，便于排查）
            if response.status_code >= 500:
                return response

            user = getattr(request.state, "current_user", None)
            operator_id = user.id if user is not None else None

            detail: dict[str, Any] = {"status": response.status_code}
            query = dict(request.query_params)
            if query:
                detail["query"] = query

            target_type, target_id = _target_of(path)

            # 独立会话写入，避免影响请求事务
            db = SessionLocal()
            try:
                db.add(
                    AuditLog(
                        operator_id=operator_id,
                        action=_action_of(path, request.method),
                        target_type=target_type,
                        target_id=target_id,
                        detail=detail,
                        ip=(request.client.host if request.client else None),
                        user_agent=(request.headers.get("user-agent") or "")[:255],
                    )
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("审计日志写入失败", exc_info=True)
            finally:
                db.close()
        except Exception:
            logger.warning("审计中间件异常", exc_info=True)

        return response
