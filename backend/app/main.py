"""FastAPI 应用入口"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import IntegrityError

from app.core.audit import AuditMiddleware
from app.core.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    description="产教融合项目管理系统后端 API",
)

# 审计：记录写操作
app.add_middleware(AuditMiddleware)

# CORS：开发期放开前端地址
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 异常处理
@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query"))
    msg = first.get("msg", "参数错误")
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": f"{loc}：{msg}" if loc else msg, "data": None},
    )


@app.exception_handler(IntegrityError)
async def on_integrity_error(request: Request, exc: IntegrityError):
    logger.warning("数据库约束冲突：%s", exc)
    text = str(exc.orig) if getattr(exc, "orig", None) else str(exc)
    if "unique" in text.lower() or "duplicate" in text.lower():
        msg = "数据重复，请检查唯一性字段"
    elif "foreign key" in text.lower():
        msg = "关联数据不存在或仍被引用"
    elif "check" in text.lower():
        msg = "数据不符合约束条件"
    else:
        msg = "数据保存失败"
    return JSONResponse(status_code=400, content={"code": 400, "message": msg, "data": None})


@app.exception_handler(Exception)
async def on_unhandled(request: Request, exc: Exception):
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    detail = str(exc) if settings.debug else "服务器内部错误"
    return JSONResponse(status_code=500, content={"code": 500, "message": detail, "data": None})


# ---------------------------------------------------------------- 路由
@app.get("/api/health", tags=["系统"], summary="健康检查")
def health():
    return {"code": 0, "message": "ok", "data": {"status": "up", "app": settings.app_name}}


from app.api.v1 import auth as auth_router  # noqa: E402
from app.api.v1 import alert as alert_router  # noqa: E402
from app.api.v1 import export as export_router  # noqa: E402
from app.api.v1 import risk as risk_router  # noqa: E402
from app.api.v1 import system as system_router  # noqa: E402
from app.api.v1 import report as report_router  # noqa: E402
from app.api.v1 import progress as progress_router  # noqa: E402
from app.api.v1 import project as project_router  # noqa: E402

app.include_router(auth_router.router)
app.include_router(project_router.router)
app.include_router(progress_router.router)
app.include_router(alert_router.router)
app.include_router(report_router.router)
app.include_router(export_router.router)
app.include_router(risk_router.router)
app.include_router(system_router.router)


# ---------------------------------------------------------------- 前端静态文件（可选）
# 生产默认由 nginx 托管前端；仅当 PM_FRONTEND_SERVE=true 时才由 FastAPI 托管
_FRONTEND_DIR = Path(settings.frontend_dir)

if settings.frontend_serve and _FRONTEND_DIR.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # 带 hash 的静态资源长期缓存
    assets_dir = _FRONTEND_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """SPA history 路由回退：非 API 路径一律返回 index.html"""
        # API 路径不该走到这里
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        # 优先返回真实存在的静态文件（favicon 等）
        candidate = (_FRONTEND_DIR / full_path).resolve()
        try:
            candidate.relative_to(_FRONTEND_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found") from None

        if candidate.is_file():
            return FileResponse(candidate)

        index = _FRONTEND_DIR / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        raise HTTPException(status_code=404, detail="Not Found")

    logger.info("已启用前端静态托管（FastAPI 模式）：%s", _FRONTEND_DIR)
else:
    logger.info("前端由 nginx 托管，FastAPI 仅提供 API")


# ---------------------------------------------------------------- 启动/关闭
@app.on_event("startup")
def on_startup():
    logger.info("%s 启动完成", settings.app_name)
    from app.jobs.scheduler import start_scheduler

    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    from app.jobs.scheduler import stop_scheduler

    stop_scheduler()
    logger.info("%s 已停止", settings.app_name)
