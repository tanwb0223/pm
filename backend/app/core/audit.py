"""审计日志：中间件 + 后台异步写入

## 为什么改（对齐 doc/闭环缺口汇总-20261024.md 第 26/27 条）

早期实现有三个漏记，恰好是需求点名的合规红线：
1. 只记 POST/PUT/PATCH/DELETE → 三个导出接口全是 GET，**导出留痕一条都没有**；
2. `SKIP_PATHS` 把登录接口排除了 → **登录成功/失败都不可追溯**；
3. 5xx 直接 `return response` 不记 → **最需要追责的场景反而没有日志**。

现在的规则：
- 写操作全记；`/api/export/*` 的 GET 也记（导出属于敏感数据出口）；
- 登录成功记 `LOGIN`、失败记 `LOGIN_FAIL`（不记密码，只记工号）；
- 5xx 也记，detail.status 保留真实状态码；
- 导出接口通过 `request.state.export_meta` 上报 `{count, fields}`，
  中间件并入 detail（导出接口不在本文件范围内，未上报时自动降级）。
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

# GET 也记录的路径前缀：导出接口是 GET，但导出的是全量业务数据，
# 需求明确要求「谁在什么时候导出了什么」可追溯。
AUDIT_GET_PREFIXES = ("/api/export/",)

# 不需要审计的路径
# 注意：登录接口**不再**放在这里（原实现跳过了 /api/auth/login 等三个路径，
# 导致登录留痕完全缺失）。仅保留健康检查这类无业务含义的探活接口。
SKIP_PATHS = {"/api/health", "/healthz", "/api/healthz"}

# 登录类路径：action 用 LOGIN / LOGIN_FAIL，而不是 AUTH_POST
LOGIN_PATHS = {"/api/auth/login", "/api/auth/login/ldap", "/api/auth/token"}

# 敏感查询参数：值必须脱敏后才能进审计日志
# （早期把 token 原样存进 detail.query，审计日志本身成了凭证泄露源）
SENSITIVE_PARAMS = {"token", "password", "pwd", "secret", "default_password", "credential", "credentials", "ticket"}

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
    ("/api/messages", "MESSAGE"),
    ("/api/attachments", "ATTACHMENT"),
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


def _should_audit(method: str, path: str) -> bool:
    if path in SKIP_PATHS:
        return False
    if method in WRITE_METHODS:
        return True
    # GET 只记录导出类接口
    return any(path.startswith(p) for p in AUDIT_GET_PREFIXES)


def _safe_meta(value: Any) -> Any:
    """request.state 上的自定义数据必须可 JSON 序列化，否则整条审计写不进去"""
    if value is None:
        return None
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except (TypeError, ValueError):
        return str(value)[:500]


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        status_code = 500
        error_text: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            # 未处理异常会被外层 ServerErrorMiddleware 转成 500，
            # 但它在本中间件之外，所以这里必须自己记一笔再抛出。
            error_text = f"{type(exc).__name__}: {exc}"[:500]
            self._write(request, 500, error_text)
            raise

        try:
            self._write(request, status_code)
        except Exception:
            logger.warning("审计中间件异常", exc_info=True)
        return response

    # ---------------------------------------------------------------- 内部
    def _write(self, request: Request, status_code: int, error_text: str | None = None) -> None:
        path = request.url.path
        if not _should_audit(request.method, path):
            return

        user = getattr(request.state, "current_user", None)
        operator_id = user.id if user is not None else None
        # 登录接口没有 current_user（登录前本来就没身份），
        # 由接口在成功/失败时把已确认的用户 ID 写进 audit_operator_id，
        # 这样「谁登录了」在审计里能直接按 operator_id 过滤。
        if operator_id is None:
            operator_id = getattr(request.state, "audit_operator_id", None)

        detail: dict[str, Any] = {"status": status_code}
        if error_text:
            detail["error"] = error_text

        query = dict(request.query_params)
        if query:
            detail["query"] = {
                k: ("***" if k.lower() in SENSITIVE_PARAMS else v)
                for k, v in query.items()
            }

        # 导出接口在响应里上报 {count, fields}；B 组未接入时自动降级为无此字段
        export_meta = _safe_meta(getattr(request.state, "export_meta", None))
        if export_meta:
            detail["export"] = export_meta

        # 登录接口没有 current_user，由接口自己把工号写进 audit_extra
        extra = _safe_meta(getattr(request.state, "audit_extra", None))
        if extra:
            detail.update(extra)

        if path in LOGIN_PATHS:
            action = "LOGIN" if status_code < 400 else "LOGIN_FAIL"
        else:
            action = _action_of(path, request.method)

        target_type, target_id = _target_of(path)

        # 独立会话写入，避免影响请求事务
        db = SessionLocal()
        try:
            db.add(
                AuditLog(
                    operator_id=operator_id,
                    action=action,
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
