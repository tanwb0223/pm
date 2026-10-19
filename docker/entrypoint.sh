#!/bin/sh
# 容器启动脚本：等数据库 → 执行迁移 → 启动 uvicorn
set -e

log() { echo "[entrypoint] $*"; }

# ---------------------------------------------------------------- 等待数据库
log "等待数据库就绪..."
python - <<'PY'
import os
import sys
import time

import psycopg

url = os.environ.get("PM_DATABASE_URL", "")
if not url:
    print("[entrypoint] 未设置 PM_DATABASE_URL，跳过等待", flush=True)
    sys.exit(0)

dsn = url.replace("postgresql+psycopg://", "postgresql://")

for i in range(60):
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        print("[entrypoint] 数据库已就绪", flush=True)
        sys.exit(0)
    except Exception as e:
        if i == 0:
            print(f"[entrypoint] 数据库未就绪，等待中... ({e})", flush=True)
        time.sleep(2)

print("[entrypoint] 数据库 120 秒内未就绪，退出", file=sys.stderr, flush=True)
sys.exit(1)
PY

# ---------------------------------------------------------------- 数据库迁移
if [ "${PM_SKIP_MIGRATE:-false}" != "true" ]; then
    log "执行数据库迁移..."
    cd /app
    alembic upgrade head
    log "迁移完成"
fi

# ---------------------------------------------------------------- 启动服务
log "启动 Uvicorn..."
exec "$@"
