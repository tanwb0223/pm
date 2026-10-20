#!/bin/sh
# ============================================================================
# PostgreSQL 自动备份脚本
#
# 功能：
#   - pg_dump 自定义格式（压缩，支持 pg_restore 选择性恢复）
#   - 保留 N 天，自动清理旧备份
#   - 备份后校验文件完整性
#   - 失败时输出明确错误（便于 cron 邮件告警）
#
# 用法：
#   ./backup.sh                 正常备份
#   ./backup.sh --verify-only   只校验最近一次备份
#
# 环境变量（由 docker-compose 注入）：
#   PM_DATABASE_URL   数据库连接串
#   BACKUP_DIR        备份目录（默认 /backup）
#   BACKUP_KEEP_DAYS  保留天数（默认 30）
# ============================================================================

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backup}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
DB_URL="${PM_DATABASE_URL:-}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

fail() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 错误: $*" >&2
    exit 1
}

# ---------------------------------------------------------------- 参数校验
if [ -z "$DB_URL" ]; then
    fail "未设置 PM_DATABASE_URL"
fi

# postgresql+psycopg://user:pass@host:port/db  →  postgresql://user:pass@host:port/db
PG_DSN=$(echo "$DB_URL" | sed 's|postgresql+psycopg://|postgresql://|')

mkdir -p "$BACKUP_DIR"

# ---------------------------------------------------------------- 只校验模式
if [ "${1:-}" = "--verify-only" ]; then
    LATEST=$(ls -t "$BACKUP_DIR"/pm_*.dump 2>/dev/null | head -1 || true)
    if [ -z "$LATEST" ]; then
        fail "没有找到备份文件"
    fi
    log "校验: $LATEST"
    pg_restore --list "$LATEST" > /dev/null 2>&1 \
        && log "校验通过（文件结构完整）" \
        || fail "备份文件损坏: $LATEST"
    exit 0
fi

# ---------------------------------------------------------------- 执行备份
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
OUTFILE="$BACKUP_DIR/pm_${TIMESTAMP}.dump"

log "开始备份 → $OUTFILE"

# 用 pg_dump 自定义格式（-Fc），自带压缩
if ! pg_dump "$PG_DSN" \
        --format=custom \
        --compress=6 \
        --no-owner \
        --no-privileges \
        --file="$OUTFILE" 2>/tmp/pg_dump_err.log; then
    ERR=$(cat /tmp/pg_dump_err.log 2>/dev/null || echo "未知错误")
    rm -f "$OUTFILE"
    fail "pg_dump 失败: $ERR"
fi

# ---------------------------------------------------------------- 校验
SIZE=$(stat -c%s "$OUTFILE" 2>/dev/null || stat -f%z "$OUTFILE" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 1024 ]; then
    rm -f "$OUTFILE"
    fail "备份文件过小（${SIZE} 字节），可能失败"
fi

# 用 pg_restore --list 验证文件结构完整
if ! pg_restore --list "$OUTFILE" > /dev/null 2>&1; then
    rm -f "$OUTFILE"
    fail "备份文件校验失败，已删除"
fi

SIZE_H=$(echo "$SIZE" | awk '{printf "%.1f MB", $1/1024/1024}')
log "备份完成: $SIZE_H"

# ---------------------------------------------------------------- 清理旧备份
DELETED=$(find "$BACKUP_DIR" -name "pm_*.dump" -type f -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "清理 $DELETED 个超过 $KEEP_DAYS 天的旧备份"
fi

# ---------------------------------------------------------------- 磁盘空间检查
USAGE=$(df "$BACKUP_DIR" | awk 'NR==2 {print $5}' | tr -d '%')
log "备份目录所在分区使用率: ${USAGE}%"
if [ "$USAGE" -gt 85 ]; then
    log "警告: 磁盘使用率超过 85%，请及时清理"
fi

log "全部完成"
