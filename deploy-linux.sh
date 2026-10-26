#!/bin/bash
# ============================================================================
# 产教融合项目管理系统 · Linux 原生部署脚本
#
# 用法（root 权限）：
#   sudo bash deploy.sh                 # 默认 SQLite，零额外依赖
#   sudo bash deploy.sh --postgres      # 改用 PostgreSQL（需已安装）
#   sudo bash deploy.sh --port 8000 --workers 2
#
# 前提：
#   - 已安装 Python 3.12（必须）
#   - PostgreSQL 18 仅在指定 --postgres 时需要
#
# 本脚本会依次完成：
#   创建 pm 用户与目录 → 建 venv 并离线装依赖 → 初始化数据库 → 写 .env
#   → 迁移（PG）/ 建表（SQLite 由应用启动时完成） → 配 systemd → 健康检查 → 建管理员
# ============================================================================
set -e

# ---------------------------------------------------------------- 参数
# 默认 SQLite（单机、零依赖）。需要 PostgreSQL 时显式指定 --postgres。
USE_POSTGRES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --postgres)  USE_POSTGRES=1; shift ;;
        --port)      HTTP_PORT="$2"; shift 2 ;;
        --workers)   WORKERS="$2"; shift 2 ;;
        -h|--help)
            echo "用法：sudo bash deploy.sh [选项]"
            echo "  --postgres        使用 PostgreSQL（默认 SQLite）"
            echo "  --port <端口>     监听端口，默认 8000"
            echo "  --workers <数量>  uvicorn worker 数，默认 2"
            exit 0 ;;
        *) echo "未知参数：$1（用 --help 查看）"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------- 配置区
INSTALL_DIR="/opt/pm"
DATA_DIR="/data/pm"
HTTP_PORT="${HTTP_PORT:-8000}"
WORKERS="${WORKERS:-2}"
SERVICE="pm"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[X]${NC} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    err "请用 root 或 sudo 运行"
    exit 1
fi

PKG_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "=============================================="
echo " 1/8  检查环境"
echo "=============================================="

PY=""
for c in python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1; then
        V=$($c -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")
        if [ "$V" = "3.12" ]; then PY=$c; break; fi
    fi
done
[ -z "$PY" ] && {
    err "需要 Python 3.12"
    echo "  Ubuntu/Debian: apt install python3.12 python3.12-venv"
    echo "  CentOS/RHEL:   yum install python3.12"
    exit 1
}
info "Python: $($PY --version)"

# venv 模块必须可用（Debian 系要单独装 python3.12-venv）
if ! $PY -c "import venv" >/dev/null 2>&1; then
    err "缺少 venv 模块：apt install python3.12-venv"
    exit 1
fi
info "venv 模块可用"

echo ""
echo "=============================================="
echo " 2/8  创建用户与目录"
echo "=============================================="

id -u pm >/dev/null 2>&1 || useradd -r -m -d "$INSTALL_DIR" -s /bin/bash pm
mkdir -p "$INSTALL_DIR" "$DATA_DIR"/{upload,log,backup}
chown -R pm:pm "$INSTALL_DIR" "$DATA_DIR"
info "用户 pm 与目录已就绪"

echo ""
echo "=============================================="
echo " 3/8  部署代码"
echo "=============================================="

# 目录布局沿用生产服务器既有结构：
#   /opt/pm/backend/{app,alembic,scripts,fonts,.venv,.env}
#   /opt/pm/frontend/dist
BACKEND_DIR="$INSTALL_DIR/backend"
mkdir -p "$BACKEND_DIR" "$INSTALL_DIR/frontend/dist"

cp -r "$PKG_ROOT/app"          "$BACKEND_DIR/app"
cp -r "$PKG_ROOT/alembic"      "$BACKEND_DIR/alembic"
cp -r "$PKG_ROOT/scripts"      "$BACKEND_DIR/scripts"
cp -r "$PKG_ROOT/fonts"        "$BACKEND_DIR/fonts"
cp    "$PKG_ROOT/alembic.ini"  "$BACKEND_DIR/alembic.ini"
cp    "$PKG_ROOT/requirements.txt" "$BACKEND_DIR/requirements.txt"
cp    "$PKG_ROOT/.env.example" "$BACKEND_DIR/.env.example"
cp -r "$PKG_ROOT/frontend-dist/." "$INSTALL_DIR/frontend/dist/"
chown -R pm:pm "$INSTALL_DIR"
info "代码已部署到 $BACKEND_DIR（前端 $INSTALL_DIR/frontend/dist）"

echo ""
echo "=============================================="
echo " 4/8  创建虚拟环境并安装依赖"
echo "=============================================="

VENV="$BACKEND_DIR/.venv"
[ -x "$VENV/bin/python" ] || sudo -u pm $PY -m venv "$VENV"
sudo -u pm "$VENV/bin/pip" install --upgrade pip --quiet 2>/dev/null || true

if [ -d "$PKG_ROOT/wheels" ]; then
    sudo -u pm "$VENV/bin/pip" install \
        --no-index --find-links "$PKG_ROOT/wheels" \
        -r "$BACKEND_DIR/requirements.txt" --quiet
    info "离线安装完成"
else
    warn "未找到 wheels 目录，尝试联网安装"
    sudo -u pm "$VENV/bin/pip" install -r "$BACKEND_DIR/requirements.txt" --quiet
fi

sudo -u pm "$VENV/bin/python" -c "import fastapi, sqlalchemy, alembic; print('  依赖验证通过')"

echo ""
echo "=============================================="
echo " 5/8  初始化数据库"
echo "=============================================="

DB_NAME="pm_prod"
DB_USER="pm_app"

rand_alnum() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 $(($1 * 2)) | tr -dc 'A-Za-z0-9' | head -c "$1"
    else
        head -c $(($1 * 3)) /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"
    fi
}

# 数据库选择：
#   默认 SQLite —— 单机、零额外服务，本项目用户量下完全够用。
#   指定 --postgres 且机器已装 PostgreSQL 时才走 PG。
if [ "$USE_POSTGRES" = "1" ]; then
    if ! command -v psql >/dev/null 2>&1; then
        err "指定了 --postgres，但未安装 PostgreSQL 客户端"
        echo "  Ubuntu/Debian: apt install postgresql-18"
        echo "  或去掉 --postgres 直接用 SQLite（推荐）"
        exit 1
    fi
    USE_PG=1
    info "PostgreSQL: $(psql --version | head -1)"

    if ! systemctl is-active --quiet postgresql 2>/dev/null && \
       ! systemctl is-active --quiet postgresql-18 2>/dev/null; then
        warn "PostgreSQL 服务未运行，尝试启动..."
        systemctl start postgresql 2>/dev/null || systemctl start postgresql-18 2>/dev/null || {
            err "无法启动 PostgreSQL"; exit 1
        }
    fi
    info "PostgreSQL 服务运行中"

    DB_PASS=""
    DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null || echo "")
    if [ -z "$DB_EXISTS" ]; then
        DB_PASS=$(rand_alnum 24)
        info "创建数据库与用户..."
        sudo -u postgres psql <<SQLEOF
CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\c $DB_NAME
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQLEOF
        info "数据库创建完成"
    else
        warn "数据库 $DB_NAME 已存在"
        read -p "  请输入现有 $DB_USER 密码: " DB_PASS
    fi
    DB_URL="postgresql+psycopg://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME"
else
    USE_PG=0
    DB_URL=""
    DB_FILE="$DATA_DIR/pm.db"
    info "数据库：SQLite（$DB_FILE）"
    # 首次部署由应用启动时 create_all 建表并灌种子数据，这里只保证目录就绪
    mkdir -p "$DATA_DIR"
    chown pm:pm "$DATA_DIR"
fi

SECRET=$(rand_alnum 48)

cat > "$BACKEND_DIR/.env" <<ENVEOF
# 由 deploy.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')
PM_DATABASE_URL=$DB_URL
PM_APP_NAME=产教融合项目管理系统
PM_DEBUG=false
PM_SECRET_KEY=$SECRET
# 8 小时（480 分钟）：内网系统一天一班，30 分钟会让用户反复被踢下线。
PM_ACCESS_TOKEN_EXPIRE_MINUTES=480

PM_DATA_DIR=$DATA_DIR
PM_UPLOAD_DIR=$DATA_DIR/upload
PM_UPLOAD_MAX_SIZE_MB=50

PM_FRONTEND_SERVE=true
PM_FRONTEND_DIR=$INSTALL_DIR/frontend/dist

PM_SCHEDULER_ENABLED=true
PM_DAILY_SCAN_HOUR=1
PM_DIGEST_HOUR=8
PM_DIGEST_MINUTE=30

PM_BACKUP_ENABLED=true
PM_BACKUP_HOUR=2
PM_BACKUP_DIR=$DATA_DIR/backup
PM_BACKUP_KEEP_DAYS=30

# 数据保留清理（默认关闭：确认保留期后再改成 true）
PM_AUDIT_RETENTION_YEARS=2
PM_SNAPSHOT_RETENTION_YEARS=3
PM_CLEANUP_ENABLED=false
PM_CLEANUP_HOUR=3

# 前端对外地址（SSO 回调落地页用；前后端同域可留空）
# PM_FRONTEND_BASE_URL=http://服务器IP:$HTTP_PORT

PM_SMTP_HOST=
PM_SMTP_PORT=465
PM_SMTP_USER=
PM_SMTP_PASSWORD=
PM_SMTP_USE_SSL=true
PM_MAIL_FROM=
ENVEOF

chown pm:pm "$BACKEND_DIR/.env"
chmod 600 "$BACKEND_DIR/.env"
info "配置文件已生成"

echo ""
echo "=============================================="
echo " 6/8  执行数据库迁移"
echo "=============================================="

if [ "$USE_PG" = "1" ]; then
    sudo -u pm bash -c "cd $BACKEND_DIR && set -a && . ./.env && set +a && ./.venv/bin/python -m alembic upgrade head"
    info "迁移完成"
else
    # SQLite 不需要预先迁移：应用启动时 create_all 建表 + 灌种子，
    # 之后存量库的结构升级走 app/db/alembic_sync.py 的自动修补。
    info "SQLite：跳过预迁移，由应用启动时自动建表"
fi

echo ""
echo "=============================================="
echo " 7/8  配置 systemd 服务"
echo "=============================================="

cat > /etc/systemd/system/$SERVICE.service <<SVCEOF
[Unit]
Description=产教融合项目管理系统
After=network.target

[Service]
Type=simple
User=pm
Group=pm
WorkingDirectory=$BACKEND_DIR
Environment="PATH=$BACKEND_DIR/.venv/bin"
EnvironmentFile=$BACKEND_DIR/.env
ExecStart=$BACKEND_DIR/.venv/bin/uvicorn app.main:app \\
    --host 0.0.0.0 \\
    --port $HTTP_PORT \\
    --workers $WORKERS \\
    --proxy-headers \\
    --forwarded-allow-ips='*'
Restart=always
RestartSec=5
StandardOutput=append:$DATA_DIR/log/app.log
StandardError=append:$DATA_DIR/log/app.err.log

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable $SERVICE
systemctl restart $SERVICE
info "systemd 服务已配置并启动"

echo ""
echo "=============================================="
echo " 8/8  等待服务就绪"
echo "=============================================="

READY=0
for i in $(seq 1 30); do
    sleep 2
    if curl -sf "http://127.0.0.1:$HTTP_PORT/api/health" >/dev/null 2>&1; then
        READY=1
        info "服务已就绪"
        break
    fi
    printf "."
done

if [ "$READY" != "1" ]; then
    echo ""
    err "服务启动超时"
    echo "  journalctl -u $SERVICE -n 50"
    echo "  tail -50 $DATA_DIR/log/app.err.log"
    exit 1
fi

# ---------------------------------------------------------------- 管理员
HASUSER=$(sudo -u pm bash -c "cd $BACKEND_DIR && set -a && . ./.env && set +a && \
    ./.venv/bin/python -c \"
from app.db.session import SessionLocal
from app.models.system import SysUser
from sqlalchemy import select
db = SessionLocal()
print('yes' if db.scalar(select(SysUser)) else 'no')
\"" 2>/dev/null | tr -d '\r\n ' || echo "no")

if [ "$HASUSER" != "yes" ]; then
    sudo -u pm bash -c "cd $BACKEND_DIR && set -a && . ./.env && set +a && \
        ./.venv/bin/python scripts/seed_admin.py admin 'Admin@123'"
    ADMIN_MSG="管理员账号：admin  密码：Admin@123"
else
    ADMIN_MSG="已有用户，未创建新管理员"
fi

# ---------------------------------------------------------------- 防火墙
if command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port=$HTTP_PORT/tcp >/dev/null 2>&1 && \
        firewall-cmd --reload >/dev/null 2>&1 && info "防火墙已放通 $HTTP_PORT"
elif command -v ufw >/dev/null 2>&1; then
    ufw allow $HTTP_PORT/tcp >/dev/null 2>&1 && info "防火墙已放通 $HTTP_PORT"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP="服务器IP"

echo ""
echo "=============================================="
echo " 部署完成"
echo "=============================================="
echo ""
echo "  访问地址： http://${IP}:${HTTP_PORT}"
echo "  $ADMIN_MSG"
echo ""
if [ "$USE_PG" = "1" ]; then
    echo "  数据库： PostgreSQL  $DB_NAME"
    echo "  数据库密码： $DB_PASS"
    echo "  （已写入 $BACKEND_DIR/.env，请记录备用）"
else
    echo "  数据库： SQLite  $DATA_DIR/pm.db"
    echo "  （备份：直接复制该文件，或跑 scripts/backup.py）"
fi
echo ""
echo "  常用命令："
echo "    查看状态  systemctl status $SERVICE"
echo "    查看日志  journalctl -u $SERVICE -f"
echo "    重启服务  systemctl restart $SERVICE"
echo "    手动备份  sudo -u pm $BACKEND_DIR/.venv/bin/python $BACKEND_DIR/scripts/backup.py"
echo ""
echo "  ⚠️  请登录后立即修改管理员密码"
echo ""