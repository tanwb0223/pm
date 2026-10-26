# ============================================================================
# 产教融合项目管理系统 · 生产镜像（SQLite 单机版）
#
# 构建上下文必须是仓库根目录：
#   docker build -f docker/Dockerfile -t pm-app:latest .
#
# 体积控制：
#   1. 多阶段构建：Node/pnpm/前端源码不进运行镜像，只带 frontend/dist
#   2. 构建阶段用 alpine:3.21 + 系统 python3，装完清理
#   3. 最终阶段 FROM scratch，只 COPY 一次构建阶段根文件系统
#      —— 构建期删除的 pip/apk 缓存、pycache 不会作为“已删除层”留在镜像里
#   4. 不装 psycopg（SQLite 专用，省 libpq）
#   5. pip --no-compile + 删除 stdlib/site-packages 的 __pycache__
#   6. 中文字体只保留 TTC，卸掉 fontconfig/mkfontscale 等安装期依赖
#   7. 单进程 uvicorn：SQLite 单机场景无需多 worker
#
# 运行镜像内固定路径：
#   /app                 代码
#   /data/pm             SQLite / 备份 / 日志 / 锁
#   /data/pm/upload      附件（由 compose 单独挂载）
# ============================================================================

# ---------------------------------------------------------------- 前端构建
FROM node:20-alpine AS frontend
WORKDIR /src

# 与 frontend/package.json 的 packageManager 保持一致
RUN npm install -g pnpm@10.0.0

# 先只拷贝依赖清单，利用 Docker 层缓存
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/.npmrc ./
RUN pnpm install --frozen-lockfile

# 再拷贝源码构建
COPY frontend/ ./
RUN pnpm build

# ---------------------------------------------------------------- 构建阶段
FROM alpine:3.21 AS builder

# python3 + pip：Alpine 的 pip 受 PEP668 管理，安装时显式 --break-system-packages；
# 装完再 apk del py3-pip，去掉 pip/setuptools 体积。
# 中文字体：只取 wqy-zenhei.ttc 到 export_service 会查找的路径，然后卸掉
# fontconfig/mkfontscale 等安装期依赖；freetype 显式保留给 Pillow 字体渲染。
RUN apk add --no-cache ca-certificates tzdata freetype python3 py3-pip \
 && apk add --no-cache --virtual .pm-font font-wqy-zenhei \
 && mkdir -p /usr/share/fonts/truetype/wqy \
 && cp /usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
 && apk del .pm-font \
 && rm -rf /var/cache/apk/* /usr/share/doc /usr/share/man /usr/share/fontconfig \
 && ln -sf /usr/bin/python3 /usr/local/bin/python

# 非 root 运行；UID/GID 固定，deploy.sh 会按 10001:10001 调整宿主机数据目录属主。
RUN addgroup -g 10001 pm \
 && adduser -D -u 10001 -G pm -h /home/pm -s /sbin/nologin pm

WORKDIR /app

# 生产依赖：以 requirements-prod.txt 为准，去掉 psycopg（本镜像只跑 SQLite）。
# --no-compile 不生成 __pycache__；stdlib 自带的 pycache 也删掉。
COPY backend/requirements-prod.txt /tmp/requirements.txt
RUN grep -v '^[[:space:]]*psycopg' /tmp/requirements.txt > requirements.txt \
 && python3 -m pip install --break-system-packages --no-compile -r requirements.txt \
 && rm -f /tmp/requirements.txt \
 && apk del py3-pip \
 && { find /usr/lib/python3.12 -type d -name __pycache__ -prune -exec rm -rf {} \; || true; } \
 && { find /usr/lib/python3.12 -name '*.pyc' -delete || true; } \
 && rm -rf /usr/lib/python3.12/site-packages/sqlalchemy/testing \
 && rm -rf /usr/lib/python3.12/ensurepip /usr/lib/python3.12/test /usr/lib/python3.12/idlelib \
 && rm -rf /usr/lib/python3.12/tkinter /usr/lib/python3.12/lib2to3 /usr/lib/python3.12/pydoc_data \
 && rm -rf /root/.cache /tmp/*

# 后端代码（只带运行必需目录，不带 tests/README/本机 .env）
COPY --chown=pm:pm backend/app ./app
COPY --chown=pm:pm backend/alembic ./alembic
COPY --chown=pm:pm backend/alembic.ini ./alembic.ini
COPY --chown=pm:pm backend/scripts ./scripts
COPY --chown=pm:pm backend/fonts ./fonts

# 前端构建产物：只复制 dist，node_modules 留在构建阶段
COPY --from=frontend --chown=pm:pm /src/dist ./frontend/dist

# 容器内数据根目录；真正持久化靠 compose 的 bind mount
RUN mkdir -p /data/pm/upload /data/pm/backup /data/pm/log /data/pm/locks \
 && chown -R pm:pm /app /data

# ---------------------------------------------------------------- 最终镜像
# scratch + 单次 COPY：构建阶段删掉的文件不会以“白化层”留在最终镜像中。
FROM scratch AS runtime

ARG PM_VERSION=dev
LABEL org.opencontainers.image.title="产教融合项目管理系统" \
      org.opencontainers.image.version="${PM_VERSION}" \
      org.opencontainers.image.description="FastAPI + SQLite + React 单机部署镜像" \
      org.opencontainers.image.source="https://github.com/tanwb0223/pm"

ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

WORKDIR /app
COPY --from=builder / /

USER pm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

# 默认单 worker；compose 里可用 PM_WORKERS 覆盖
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]