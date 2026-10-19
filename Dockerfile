# ============================================================================
# 产教融合项目管理系统 · 单镜像（前端 + 后端）
#
# 构建：docker build -t pm:latest .
# 运行：docker compose up -d
#
# 架构：只有一个 uvicorn 进程
#   - FastAPI 提供 /api/* 接口
#   - FastAPI 同时托管前端静态文件（SPA 路由回退）
#   - 不需要 nginx、不需要 supervisor
#
# 体积优化：
#   1. python:3.12-alpine（83MB）而非 slim（179MB）
#   2. 多阶段构建，前端只保留 dist
#   3. 只装运行时依赖（requirements-docker.txt，不含 pytest）
#   4. pip 安装后删除 pip/setuptools/wheel 与所有 __pycache__/*.pyc
#   5. apk 缓存全部清理
# ============================================================================

# ---------------------------------------------------------------- 阶段 1：构建前端
FROM node:20-alpine AS frontend-builder

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- 阶段 2：运行时
FROM python:3.12-alpine

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    PM_UPLOAD_DIR=/data/upload \
    PM_FRONTEND_SERVE=true \
    PM_FRONTEND_DIR=/app/frontend_dist

# 运行时系统依赖
#   tzdata          时区（Alpine 默认无）
#   libpq           psycopg 运行时需要
#   curl            健康检查
#   font-wqy-zenhei 中文字体（PDF 导出，约 5MB）
RUN apk add --no-cache \
        tzdata \
        libpq \
        curl \
        font-wqy-zenhei \
    && cp /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/cache/apk/* /tmp/*

WORKDIR /app

# ---------------------------------------------------------------- Python 依赖
COPY backend/requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt \
    && pip uninstall -y pip setuptools wheel 2>/dev/null || true
RUN find /usr/local/lib/python3.12/site-packages -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true; \
    find /usr/local/lib/python3.12/site-packages -name "*.pyc" -delete 2>/dev/null || true; \
    rm -rf /root/.cache /tmp/* 2>/dev/null || true

# ---------------------------------------------------------------- 后端代码
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/scripts ./scripts

RUN find /app -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------- 前端产物
COPY --from=frontend-builder /build/dist /app/frontend_dist

# ---------------------------------------------------------------- 启动脚本
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# ---------------------------------------------------------------- 非 root 运行
RUN adduser -D -u 1000 pm \
    && mkdir -p /data/upload /data/log \
    && chown -R pm:pm /app /data

USER pm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
