#!/bin/bash
# ============================================================================
# Linux 部署脚本
#
# 用法（root 权限）：
#   sudo bash install.sh
#
# 前提：
#   - 已安装 Python 3.12
#   - 已安装 PostgreSQL 18
# ============================================================================
set -e

# ---------------------------------------------------------------- 配置区
INSTALL_DIR="/opt/pm"
DATA_DIR="/data/pm"
HTTP_PORT="8000"
WORKERS="2"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[X]${NC} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    err "请用 root 或 sudo 运行"
    exit 1
fi

echo "=============================================="
echo " 1/8  检查环境"
echo "=============================================="

PY=""
for c in python3.12 python3; do
    if command -v $c >/dev/null 2>&1; then
        V=$($c -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if [ "$V" = "3.12" ]; then PY=$c; break; fi
    fi
done
[ -z "$PY" ] && { err "需要 Python 3.12"; exit 1; }
info "Python: $($PY --version)"

if ! command -v psql >/dev/null 2>&1; then
    err "未安装 PostgreSQL 客户端"
    echo "  Ubuntu:  apt install postgresql-18"
    echo "  CentOS:  yum install postgresql18-server"
    exit 1
fi
info "PostgreSQL: $(psql --version | head -1)"

if ! systemctl is-active --quiet postgresql 2>/dev/null && \
   ! systemctl is-active --quiet postgresql-18 2>/dev/null; then
    warn "PostgreSQL 服务未运行，尝试启动..."
    systemctl start postgresql 2>/dev/null || systemctl start postgresql-18 2>/dev/null || {
        err "无法启动 PostgreSQL"; exit 1
    }
fi
info "PostgreSQL 服务运行中"

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

PKG_ROOT="$(cd "$(dirname "$0")" && pwd)"
cp -r "$PKG_ROOT/app" "$INSTALL_DIR/"
cp -r "$PKG_ROOT/frontend" "$INSTALL_DIR/"
chown -R pm:pm "$INSTALL_DIR"
info "代码已部署到 $INSTALL_DIR"

echo ""
echo "=============================================="
echo " 4/8  创建虚拟环境并安装依赖"
echo "=============================================="

cd "$INSTALL_DIR/app"
$PY -m venv .venv
./.venv/bin/pip install --upgrade pip --quiet 2>/dev/null || true

if [ -d "$PKG_ROOT/wheels" ]; then
    ./.venv/bin/pip install --no-index --find-links "$PKG_ROOT/wheels" -r requirements.txt --quiet
    info "离线安装完成"
else
    warn "未找到 wheels 目录，尝试联网安装"
    ./.venv/bin/pip install -r requirements.txt --quiet
fi

./.venv/bin/python -c "import fastapi, sqlalchemy, psycopg, alembic; print('  验证通过')"

echo ""
echo "=============================================="
echo " 5/8  初始化数据库"
echo "=============================================="

DB_NAME="pm_prod"
DB_USER="pm_app"

# 生成随机密码（纯字母数字，避免连接串解析问题）
rand_alnum() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 $(( $1 * 2 )) | tr -dc 'A-Za-z0-9' | head -c "$1"
    else
        head -c $(( $1 * 3 )) /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"
    fi
}
DB_PASS=$(rand_alnum 24)
SECRET=$(rand_alnum 48)

DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null || echo "")

if [ -z "$DB_EXISTS" ]; then
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
    read -p "  请输入现有 pm_app 密码: " DB_PASS
fi

cd "$INSTALL_DIR/app"
cat > .env <<ENVEOF
PM_DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME
PM_APP_NAME=产教融合项目管理系统
PM_DEBUG=false
PM_SECRET_KEY=$SECRET
PM_ACCESS_TOKEN_EXPIRE_MINUTES=30

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

PM_SMTP_HOST=
PM_SMTP_PORT=465
PM_SMTP_USER=
PM_SMTP_PASSWORD=
PM_SMTP_USE_SSL=true
PM_MAIL_FROM=
ENVEOF

chown pm:pm .env
chmod 600 .env
info "配置文件已生成"

echo ""
echo "=============================================="
echo " 6/8  执行数据库迁移"
echo "=============================================="

sudo -u pm bash -c "cd $INSTALL_DIR/app && ./.venv/bin/alembic upgrade head"
info "迁移完成"

echo ""
echo "=============================================="
echo " 7/8  配置 systemd 服务"
echo "=============================================="

cat > /etc/systemd/system/pm.service <<SVCEOF
[Unit]
Description=产教融合项目管理系统
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=pm
Group=pm
WorkingDirectory=$INSTALL_DIR/app
Environment="PATH=$INSTALL_DIR/app/.venv/bin"
EnvironmentFile=$INSTALL_DIR/app/.env
ExecStart=$INSTALL_DIR/app/.venv/bin/uvicorn app.main:app \\
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
systemctl enable pm
systemctl restart pm
info "systemd 服务已配置并启动"

echo ""
echo "=============================================="
echo " 8/8  等待服务就绪"
echo "=============================================="

for i in $(seq 1 30); do
    sleep 2
    if curl -sf "http://127.0.0.1:$HTTP_PORT/api/health" >/dev/null 2>&1; then
        info "服务已就绪"
        break
    fi
    printf "."
    if [ $i -eq 30 ]; then
        echo ""
        err "服务启动超时"
        echo "  journalctl -u pm -n 50"
        echo "  tail -50 $DATA_DIR/log/app.err.log"
        exit 1
    fi
done

HASUSER=$(sudo -u pm bash -c "cd $INSTALL_DIR/app && ./.venv/bin/python -c \"
from app.db.session import SessionLocal
from app.models.system import SysUser
from sqlalchemy import select
db = SessionLocal()
print('yes' if db.scalar(select(SysUser)) else 'no')
\"" 2>/dev/null | tr -d '\r\n ' || echo "no")

if [ "$HASUSER" != "yes" ]; then
    sudo -u pm bash -c "cd $INSTALL_DIR/app && ./.venv/bin/python scripts/seed_admin.py admin 'Admin@123'"
    ADMIN_MSG="管理员账号：admin  密码：Admin@123"
else
    ADMIN_MSG="已有用户，未创建新管理员"
fi

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
echo "  数据库密码： $DB_PASS"
echo "  （已写入 $INSTALL_DIR/app/.env，请记录备用）"
echo ""
echo "  常用命令："
echo "    查看状态  systemctl status pm"
echo "    查看日志  journalctl -u pm -f"
echo "    重启服务  systemctl restart pm"
echo "    手动备份  sudo -u pm $INSTALL_DIR/app/.venv/bin/python $INSTALL_DIR/app/scripts/backup.py"
echo ""
echo "  ⚠️  请登录后立即修改管理员密码"
echo ""
