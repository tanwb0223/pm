#!/bin/bash
# ============================================================================
# 产教融合项目管理系统 · Linux 部署包构建
#
# 在能联网的打包机上执行，产出一个标准的 Python 应用部署包（含源码、
# 离线依赖 wheel、前端构建产物）。目标机只需预装 Python 3.12 即可离线安装。
#
# 用法：
#   bash pack.sh
#
# 产出：
#   dist/pm-linux-<版本>.tar.gz   部署包
#   dist/pm-linux-<版本>.tar.gz.sha256
#
# 目标机部署：
#   tar xzf pm-linux-<版本>.tar.gz
#   cd pm && sudo bash deploy.sh
#
# 打包机需要：Python 3.12、pnpm（前端未构建时）、网络
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(date +%Y%m%d)"
WORK="$(mktemp -d)"
OUT="$ROOT/dist"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[X]${NC} $*"; }
step() { echo ""; echo "=============================================================="; echo " $*"; echo "=============================================================="; }

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

cd "$ROOT"

# ---------------------------------------------------------------- 1. 环境
step "1/6  检查构建环境"

PY=""
for c in python3.12 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$($c -c 'import sys;print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
    if [ "$v" = "3.12" ]; then PY="$c"; break; fi
done
[ -z "$PY" ] && { err "需要 Python 3.12（.pyc 与解释器版本强绑定，且目标机按 3.12 约定）"; exit 1; }
info "Python: $($PY --version)"

for p in backend/app backend/alembic backend/scripts backend/requirements-prod.txt deploy-linux.sh; do
    [ -e "$p" ] || { err "源码不完整，缺少：$p"; exit 1; }
done
info "源码完整"

mkdir -p "$OUT"
info "版本号：$VERSION"

# ---------------------------------------------------------------- 2. 前端
step "2/6  构建前端"

DIST_INDEX="$ROOT/frontend/dist/index.html"
if [ -f "$DIST_INDEX" ]; then
    if find "$ROOT/frontend/src" -type f -newer "$DIST_INDEX" | grep -q .; then
        info "源码有更新，重新构建"
        BUILD_FE=1
    else
        info "dist 已是最新，跳过"
        BUILD_FE=0
    fi
else
    BUILD_FE=1
fi

if [ "$BUILD_FE" = "1" ]; then
    command -v pnpm >/dev/null 2>&1 || { err "未安装 pnpm：npm install -g pnpm"; exit 1; }
    ( cd frontend && pnpm install --frozen-lockfile && pnpm build )
    info "前端构建完成"
fi
[ -f "$DIST_INDEX" ] || { err "frontend/dist/index.html 不存在"; exit 1; }

# ---------------------------------------------------------------- 3. 组装
step "3/6  组装部署目录"

PKG="$WORK/pm"
mkdir -p "$PKG"

# 包结构对齐生产布局：解压后即 backend/ + frontend/dist/
mkdir -p "$PKG/backend" "$PKG/frontend/dist"

cp -r backend/app           "$PKG/backend/app"
cp -r backend/alembic       "$PKG/backend/alembic"
cp -r backend/scripts       "$PKG/backend/scripts"
cp -r backend/fonts         "$PKG/backend/fonts"
cp    backend/alembic.ini   "$PKG/backend/alembic.ini"
cp    backend/requirements-prod.txt "$PKG/backend/requirements.txt"
cp    backend/.env.example  "$PKG/backend/.env.example"
cp -r frontend/dist/.       "$PKG/frontend/dist/"
cp    deploy-linux.sh       "$PKG/deploy.sh"

# 清掉开发期缓存，避免把本机产物带进包
find "$PKG" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PKG" -name "*.pyc" -delete 2>/dev/null || true
find "$PKG" -name "*.pyo" -delete 2>/dev/null || true

info "代码与前端产物已就位"

# ---------------------------------------------------------------- 4. 依赖
step "4/6  下载离线依赖 wheel"

WHEELS="$PKG/wheels"
mkdir -p "$WHEELS"

# 目标平台 Linux x86_64；manylinux2014 兼容面最广
if $PY -m pip download \
        -r "$PKG/requirements.txt" \
        -d "$WHEELS" \
        --only-binary=:all: \
        --platform manylinux2014_x86_64 \
        --platform manylinux_2_28_x86_64 \
        --platform manylinux_2_17_x86_64 \
        --python-version 312 \
        --implementation cp \
        --abi cp312 \
        --quiet 2>/dev/null; then
    info "已下载 $(find "$WHEELS" -name '*.whl' | wc -l) 个 wheel（Linux x86_64）"
else
    warn "跨平台下载失败，改用打包机本机平台（目标机需为同架构且联网补装）"
    $PY -m pip download -r "$PKG/requirements.txt" -d "$WHEELS" --quiet
fi

# ---------------------------------------------------------------- 5. 说明
step "5/6  生成部署说明"

cat > "$PKG/README.md" <<'READMEEOF'
# 部署说明

本包为标准 Python 应用部署包，**目标机需要预装 Python 3.12**（不再内置运行时）。

## 安装

```bash
tar xzf pm-linux-<版本>.tar.gz
cd pm
sudo bash deploy.sh
```

`deploy.sh` 会依次完成：创建 `pm` 用户与目录 → 建 venv → 离线装依赖 →
建库 → 写 `.env` → Alembic 迁移 → 配置 systemd → 健康检查。

## 包内容

| 路径 | 说明 |
|---|---|
| `backend/app/` | 后端源码（FastAPI） |
| `backend/alembic/` | 数据库迁移 |
| `backend/scripts/` | 运维脚本（建管理员、备份、清理） |
| `backend/fonts/` | PDF 导出用中文字体（需自行放入 ttf/ttc） |
| `backend/requirements.txt` | 依赖清单 |
| `backend/.env.example` | 环境变量样例 |
| `frontend/dist/` | 前端构建产物 |
| `wheels/` | 离线依赖 wheel（Linux x86_64） |
| `deploy.sh` | 一键部署脚本 |

## 常用命令

```bash
systemctl status pm          # 状态
journalctl -u pm -f          # 日志
systemctl restart pm         # 重启
```

## 升级

替换 `backend/app/` `backend/alembic/` `frontend/dist/` 后：

```bash
sudo -u pm /opt/pm/backend/.venv/bin/python -m alembic upgrade head
sudo systemctl restart pm
```
READMEEOF

info "部署说明已生成"

# ---------------------------------------------------------------- 6. 压缩
step "6/6  生成部署包"

TAR_NAME="pm-linux-$VERSION.tar.gz"
TAR_PATH="$OUT/$TAR_NAME"
rm -f "$TAR_PATH" "$TAR_PATH.sha256"

( cd "$WORK" && tar czf "$TAR_PATH" pm )

SIZE="$(du -h "$TAR_PATH" | cut -f1)"
if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$OUT" && sha256sum "$TAR_NAME" > "$TAR_NAME.sha256" )
else
    ( cd "$OUT" && shasum -a 256 "$TAR_NAME" > "$TAR_NAME.sha256" )
fi

echo ""
echo "=============================================================="
echo " 打包完成"
echo "=============================================================="
echo "  文件： $TAR_PATH"
echo "  大小： $SIZE"
echo ""
echo "  目标机部署（需预装 Python 3.12）："
echo "    tar xzf $TAR_NAME"
echo "    cd pm && sudo bash deploy.sh"
echo ""