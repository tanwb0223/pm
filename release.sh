#!/usr/bin/env bash
# ============================================================================
# 产教融合项目管理系统 —— 一键重新发布
#
# 用法：
#   cd /opt/pm && ./release.sh
#
# 做四件事：
#   1. 拉取最新代码（GitHub 不通时自动回退到 tarball）
#   2. 安装/更新 Python 依赖
#   3. 构建前端
#   4. 创建数据库备份 → 执行迁移 → 重启服务 → 健康检查
#
# 任何一步失败都会中止，不会留下半成品。
# ============================================================================

set -euo pipefail

APP_DIR="/opt/pm"
DATA_DIR="/data/pm"
BRANCH="main"
REPO_TARBALL="https://github.com/tanwb0223/pm/archive/refs/heads/${BRANCH}.tar.gz"
SERVICE="pm"

C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_END="\033[0m"
ok()   { echo -e "${C_OK}[OK]${C_END} $*"; }
warn() { echo -e "${C_WARN}[!]${C_END} $*"; }
err()  { echo -e "${C_ERR}[X]${C_END} $*"; }
dim()  { echo -e "${C_DIM}$*${C_END}"; }

step() {
    echo ""
    echo "=============================================================="
    echo " $*"
    echo "=============================================================="
}

current_version() {
    if [ -d "$APP_DIR/.git" ]; then
        git -C "$APP_DIR" log --oneline -1 2>/dev/null | cut -c1-60
    elif [ -f "$APP_DIR/.version" ]; then
        cat "$APP_DIR/.version"
    else
        echo "(未知)"
    fi
}

cd "$APP_DIR"

echo "=============================================================="
echo " 产教融合项目管理系统 —— 重新发布"
echo "=============================================================="
dim " 当前版本: $(current_version)"
dim " 开始时间: $(date '+%Y-%m-%d %H:%M:%S')"

# ---------------------------------------------------------------- 1. 拉代码
step "1/5  拉取最新代码"

# 先备份当前代码（回滚用）
BACKUP_DIR="/opt/pm-backup-$(date +%Y%m%d_%H%M%S)"
if [ -d "$APP_DIR/backend" ]; then
    dim " 备份当前代码到 $BACKUP_DIR（仅代码，不含 venv/dist）"
    mkdir -p "$BACKUP_DIR"
    cp -a "$APP_DIR/backend/app"     "$BACKUP_DIR/app" 2>/dev/null || true
    cp -a "$APP_DIR/backend/alembic" "$BACKUP_DIR/alembic" 2>/dev/null || true
    cp -a "$APP_DIR/backend/scripts" "$BACKUP_DIR/scripts" 2>/dev/null || true
    cp -a "$APP_DIR/backend/alembic.ini" "$BACKUP_DIR/" 2>/dev/null || true
fi

FETCHED=0

# 方式 A：git pull（如果之前是 clone 的）
if [ -d "$APP_DIR/.git" ]; then
    dim " 尝试 git pull ..."
    if git fetch --depth 1 origin "$BRANCH" 2>/dev/null && \
       git reset --hard "origin/$BRANCH" 2>/dev/null; then
        ok "git pull 成功"
        FETCHED=1
    else
        warn "git pull 失败，改用 tarball"
    fi
fi

# 方式 B：tarball（服务器上 github.com:443 常被墙，但 codeload 可用）
if [ "$FETCHED" -eq 0 ]; then
    dim " 下载 tarball ..."
    TMP_TGZ="$(mktemp /tmp/pm-release-XXXXXX.tar.gz)"
    if curl -fsSL -m 180 --retry 3 --retry-delay 2 -o "$TMP_TGZ" "$REPO_TARBALL"; then
        NEW_CODE_DIR="$(mktemp -d /tmp/pm-code-XXXXXX)"
        tar xzf "$TMP_TGZ" -C "$NEW_CODE_DIR" --strip-components=1

        # 只替换代码，保留 venv / dist / .env
        rm -rf "$APP_DIR/backend/app" "$APP_DIR/backend/alembic" "$APP_DIR/backend/scripts"
        cp -a "$NEW_CODE_DIR/backend/app"     "$APP_DIR/backend/app"
        cp -a "$NEW_CODE_DIR/backend/alembic" "$APP_DIR/backend/alembic"
        cp -a "$NEW_CODE_DIR/backend/scripts" "$APP_DIR/backend/scripts"
        cp -a "$NEW_CODE_DIR/backend/alembic.ini" "$APP_DIR/backend/"
        cp -a "$NEW_CODE_DIR/backend/requirements-prod.txt" "$APP_DIR/backend/" 2>/dev/null || true

        # 前端源码（构建用）
        rm -rf "$APP_DIR/frontend/src" "$APP_DIR/frontend/package.json" \
               "$APP_DIR/frontend/pnpm-lock.yaml" "$APP_DIR/frontend/vite.config.ts" \
               "$APP_DIR/frontend/tsconfig.json" "$APP_DIR/frontend/index.html"
        cp -a "$NEW_CODE_DIR/frontend/." "$APP_DIR/frontend/"

        cp -a "$NEW_CODE_DIR/README.md" "$APP_DIR/" 2>/dev/null || true

        echo "$(date '+%Y-%m-%d %H:%M:%S')  tarball" > "$APP_DIR/.version"

        rm -rf "$NEW_CODE_DIR" "$TMP_TGZ"
        ok "tarball 更新成功"
        FETCHED=1
    else
        rm -f "$TMP_TGZ"
        err "下载失败，请检查网络"
        exit 1
    fi
fi

# ---------------------------------------------------------------- 2. 依赖
step "2/5  更新 Python 依赖"

VENV="$APP_DIR/backend/.venv"
if [ ! -x "$VENV/bin/python" ]; then
    err "虚拟环境不存在：$VENV"
    exit 1
fi

dim " 检查依赖变化..."
"$VENV/bin/pip" install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || true
if "$VENV/bin/pip" install -q -r "$APP_DIR/backend/requirements-prod.txt" \
        -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -5; then
    ok "依赖已更新"
else
    err "依赖安装失败"
    exit 1
fi

# ---------------------------------------------------------------- 3. 前端
step "3/5  构建前端"

if [ "${SKIP_FRONTEND:-0}" = "1" ]; then
    warn "已跳过（SKIP_FRONTEND=1）"
else
    cd "$APP_DIR/frontend"
    if ! command -v pnpm >/dev/null 2>&1; then
        err "pnpm 未安装：npm install -g pnpm --registry=https://registry.npmmirror.com"
        exit 1
    fi
    pnpm config set registry https://registry.npmmirror.com >/dev/null 2>&1 || true

    dim " 安装依赖..."
    if pnpm install --frozen-lockfile 2>&1 | tail -3; then
        ok "前端依赖就绪"
    else
        warn "frozen-lockfile 失败，改用普通 install"
        pnpm install 2>&1 | tail -3 || { err "前端依赖安装失败"; exit 1; }
    fi

    dim " 构建..."
    if pnpm build 2>&1 | tail -8; then
        ok "前端构建完成"
    else
        err "前端构建失败"
        exit 1
    fi
    cd "$APP_DIR"
fi

# ---------------------------------------------------------------- 4. 备份+迁移
step "4/5  备份数据库并执行迁移"

# 确保服务属于 pm 用户
chown -R pm:pm "$APP_DIR/backend" "$APP_DIR/frontend/dist" 2>/dev/null || true
chown -R pm:pm "$DATA_DIR" 2>/dev/null || true

DB_FILE="$DATA_DIR/pm.db"
if [ -f "$DB_FILE" ]; then
    SNAP="$DATA_DIR/backup/pm_before_release_$(date +%Y%m%d_%H%M%S).db"
    mkdir -p "$DATA_DIR/backup"
    chown pm:pm "$DATA_DIR/backup" 2>/dev/null || true
    if cp "$DB_FILE" "$SNAP"; then
        chown pm:pm "$SNAP" 2>/dev/null || true
        ok "数据库已备份：$(basename "$SNAP")"
        dim " 备份文件: $SNAP"
    else
        warn "备份失败（继续执行）"
    fi
else
    warn "数据库尚未创建，跳过备份"
fi

dim " 执行 Alembic 迁移..."
cd "$APP_DIR/backend"
# shellcheck disable=SC2046
if sudo -u pm env $(grep -v '^#' .env | grep -v '^$' | xargs) \
        "$VENV/bin/python" -m alembic upgrade head 2>&1 | tail -6; then
    ok "迁移完成"
else
    warn "迁移失败或无迁移可执行（SQLite 下首次会自动建表）"
fi
cd "$APP_DIR"

# ---------------------------------------------------------------- 5. 重启
step "5/5  重启服务"

systemctl restart "$SERVICE"
dim " 等待启动..."
sleep 6

FAIL=0
for i in $(seq 1 10); do
    HEALTH=$(curl -s -m 5 http://127.0.0.1:8000/api/health 2>/dev/null || true)
    if echo "$HEALTH" | grep -q '"code":0'; then
        ok "服务已启动"
        FAIL=0
        break
    fi
    FAIL=1
    sleep 2
done

if [ "$FAIL" -eq 1 ]; then
    err "服务启动失败，最近日志："
    echo "--------------------------------------------------------------"
    tail -30 /data/pm/log/app.err.log 2>/dev/null || journalctl -u "$SERVICE" -n 30 --no-pager
    echo "--------------------------------------------------------------"
    warn "如需回滚代码："
    warn "  cp -a $BACKUP_DIR/app /opt/pm/backend/app"
    warn "  systemctl restart pm"
    exit 1
fi

# ---------------------------------------------------------------- 结果
echo ""
echo "=============================================================="
echo " 发布完成"
echo "=============================================================="
echo " 版本:     $(current_version)"
echo " 时间:     $(date '+%Y-%m-%d %H:%M:%S')"
echo " 健康检查: http://127.0.0.1:8000/api/health"
echo " 访问地址: http://chemicalagent.site"
echo "           http://123.206.121.190"
echo " 代码备份: $BACKUP_DIR"
echo "=============================================================="

# 只保留最近 5 个代码备份
ls -dt /opt/pm-backup-* 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
ok "旧备份已清理（保留最近 5 个）"
