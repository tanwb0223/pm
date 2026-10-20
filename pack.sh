#!/bin/bash
# ============================================================================
# 离线打包（在能联网的机器上运行）
#
# 用法：
#   bash pack.sh              # 同时打包 Windows 和 Linux
#   bash pack.sh linux        # 只打包 Linux
#   bash pack.sh windows      # 只打包 Windows
#
# 产出：
#   pm-offline-linux.tar.gz     Linux 部署包
#   pm-offline-windows.zip      Windows 部署包
# ============================================================================
set -e

TARGET="${1:-both}"
WORK=$(mktemp -d)

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[X]${NC} $*"; }

echo "=============================================="
echo " 1/5  检查环境"
echo "=============================================="

# 找 Python 3.12
PY=""
for c in python3.12 python3 python; do
    if command -v $c >/dev/null 2>&1; then
        V=$($c -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")
        if [ "$V" = "3.12" ]; then PY=$c; break; fi
    fi
done
[ -z "$PY" ] && { err "需要 Python 3.12"; exit 1; }
info "Python: $($PY --version)"

echo ""
echo "=============================================="
echo " 2/5  构建前端"
echo "=============================================="
if [ -d frontend/dist ] && [ -f frontend/dist/index.html ]; then
    info "已有构建产物，跳过"
else
    command -v npm >/dev/null || { err "未安装 npm"; exit 1; }
    cd frontend && npm ci --no-audit --no-fund && npm run build && cd ..
    info "前端构建完成"
fi

echo ""
echo "=============================================="
echo " 3/5  下载 Python 依赖"
echo "=============================================="

download_wheels() {
    # $1=目标目录  $2=平台标签
    local dir="$1" plat="$2"
    mkdir -p "$dir"
    $PY -m pip download \
        -r backend/requirements-prod.txt \
        -d "$dir" \
        --only-binary=:all: \
        --platform "$plat" \
        --python-version 312 \
        --implementation cp \
        --abi cp312 \
        --quiet 2>/dev/null
}

build_package() {
    # $1=输出目录
    local pkg="$1"
    mkdir -p "$pkg"/{app,frontend}

    cp -r backend/app "$pkg/app/app"
    cp -r backend/alembic "$pkg/app/alembic"
    cp backend/alembic.ini "$pkg/app/"
    cp -r backend/scripts "$pkg/app/scripts"
    cp backend/requirements-prod.txt "$pkg/app/requirements.txt"
    cp backend/.env.example "$pkg/app/.env.example"
    cp -r frontend/dist "$pkg/frontend/dist"

    # 清理缓存
    find "$pkg" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    find "$pkg" -name "*.pyc" -delete 2>/dev/null || true
}

# ---------------------------------------------------------------- Linux
if [ "$TARGET" = "both" ] || [ "$TARGET" = "linux" ]; then
    echo "  打包 Linux（x86_64）..."
    PKG_L=$WORK/linux
    build_package "$PKG_L"

    if download_wheels "$PKG_L/wheels" "manylinux_2_28_x86_64"; then
        info "Linux 依赖下载完成（$(ls -1 "$PKG_L/wheels" | wc -l) 个包）"
    else
        warn "跨平台下载失败，改用本机平台"
        $PY -m pip download -r backend/requirements-prod.txt -d "$PKG_L/wheels" --quiet
    fi

    cp deploy-linux.sh "$PKG_L/install.sh"
    chmod +x "$PKG_L/install.sh"
fi

# ---------------------------------------------------------------- Windows
if [ "$TARGET" = "both" ] || [ "$TARGET" = "windows" ]; then
    echo "  打包 Windows（x64）..."
    PKG_W=$WORK/windows
    build_package "$PKG_W"

    if download_wheels "$PKG_W/wheels" "win_amd64"; then
        info "Windows 依赖下载完成（$(ls -1 "$PKG_W/wheels" | wc -l) 个包）"
    else
        warn "Windows 依赖下载失败，将不包含 wheels（目标机需联网）"
    fi

    cp deploy-windows.ps1 "$PKG_W/install.ps1"
fi

echo ""
echo "=============================================="
echo " 4/5  打包压缩"
echo "=============================================="

if [ -d "$WORK/linux" ]; then
    cd "$WORK"
    mv linux package
    tar czf pm-offline-linux.tar.gz package
    mv package linux
    cd - >/dev/null
    mv "$WORK/pm-offline-linux.tar.gz" ./
    info "pm-offline-linux.tar.gz  $(du -h pm-offline-linux.tar.gz | cut -f1)"
fi

if [ -d "$WORK/windows" ]; then
    cd "$WORK"
    mv windows package
    if command -v zip >/dev/null 2>&1; then
        zip -qr pm-offline-windows.zip package
    else
        tar czf pm-offline-windows.tar.gz package
        warn "未安装 zip，改用 tar.gz"
    fi
    mv package windows
    cd - >/dev/null
    [ -f "$WORK/pm-offline-windows.zip" ] && mv "$WORK/pm-offline-windows.zip" ./ && \
        info "pm-offline-windows.zip  $(du -h pm-offline-windows.zip | cut -f1)"
    [ -f "$WORK/pm-offline-windows.tar.gz" ] && mv "$WORK/pm-offline-windows.tar.gz" ./ && \
        info "pm-offline-windows.tar.gz  $(du -h pm-offline-windows.tar.gz | cut -f1)"
fi

rm -rf "$WORK"

echo ""
echo "=============================================="
echo " 5/5  完成"
echo "=============================================="
echo ""
echo "  Linux 部署："
echo "    tar xzf pm-offline-linux.tar.gz"
echo "    cd package && sudo bash install.sh"
echo ""
echo "  Windows 部署（管理员 PowerShell）："
echo "    解压 pm-offline-windows.zip"
echo "    cd package"
echo "    .\\install.ps1"
echo ""
echo "  前提：目标机器已装 Python 3.12 + PostgreSQL 18"
echo ""
