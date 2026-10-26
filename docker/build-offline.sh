#!/usr/bin/env bash
# ============================================================================
# 构建离线 Docker 部署包（在有网、有 Docker 的机器上运行）
#
# 产出：
#   docker/dist/pm-docker-offline-<版本>.tar.gz
#     ├── images/pm-images-<版本>.tar.gz   app + caddy 镜像（docker load 用）
#     ├── compose.yml / compose.tls.yml / compose.http.yml
#     ├── Caddyfile.tpl / .env.docker.example
#     ├── deploy.sh / pmctl.sh / lib.sh
#     ├── certs/README.md
#     └── README.md
#
# 目标机只需要：解压 → 改 .env → sudo bash deploy.sh
# 不需要源码、Node、Python、网络。
#
# 用法：
#   bash build-offline.sh
#   PM_VERSION=20261026 bash build-offline.sh
#   PM_PLATFORM=linux/arm64 bash build-offline.sh
# ============================================================================
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$DOCKER_DIR/.." && pwd)"
cd "$DOCKER_DIR"

die()  { printf '\033[31m[X]\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }

command -v docker >/dev/null 2>&1 || die "未安装 Docker"
docker info >/dev/null 2>&1 || die "Docker 未运行，或当前用户无权访问 Docker"

# ---------------------------------------------------------------- 参数
if [ -n "${PM_VERSION:-}" ]; then
    VERSION="$PM_VERSION"
elif git -C "$ROOT_DIR" rev-parse --short HEAD >/dev/null 2>&1; then
    VERSION="$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
else
    VERSION="$(date +%Y%m%d-%H%M)"
fi
VERSION="$(printf '%s' "$VERSION" | tr -c 'A-Za-z0-9_.-' '-' | sed 's/^-*//;s/-*$//')"
[ -n "$VERSION" ] || VERSION="$(date +%Y%m%d-%H%M)"

APP_IMAGE="${PM_IMAGE_NAME:-pm-app}"
CADDY_IMAGE="${PM_CADDY_IMAGE:-caddy:2-alpine}"
PLATFORM="${PM_PLATFORM:-linux/amd64}"
OUT_DIR="${PM_OUT_DIR:-$DOCKER_DIR/dist}"

echo "=============================================================="
echo " 构建离线 Docker 部署包"
echo "=============================================================="
echo "  版本    : $VERSION"
echo "  平台    : $PLATFORM"
echo "  应用镜像: $APP_IMAGE:$VERSION（同时打 latest）"
echo "  Caddy   : $CADDY_IMAGE"
echo "  输出目录: $OUT_DIR"
echo ""

# ---------------------------------------------------------------- 1. 构建应用镜像
info "构建应用镜像（docker compose build；首次会下载基础镜像并编译前端，约 3-8 分钟）..."
export PM_VERSION="$VERSION"
export PM_IMAGE_NAME="$APP_IMAGE"
export DOCKER_DEFAULT_PLATFORM="$PLATFORM"
docker compose -f compose.build.yml build
docker tag "$APP_IMAGE:$VERSION" "$APP_IMAGE:latest"

# ---------------------------------------------------------------- 2. 拉取 Caddy
info "拉取 Caddy 镜像..."
docker pull --platform "$PLATFORM" "$CADDY_IMAGE"

# ---------------------------------------------------------------- 3. 导出镜像
mkdir -p "$OUT_DIR/images"
IMAGE_TAR="$OUT_DIR/images/pm-images-$VERSION.tar"
info "导出镜像（app + caddy）..."
docker save "$APP_IMAGE:$VERSION" "$APP_IMAGE:latest" "$CADDY_IMAGE" -o "$IMAGE_TAR"
info "压缩镜像包（gzip -9，几分钟）..."
gzip -9 -c "$IMAGE_TAR" > "$IMAGE_TAR.gz"
rm -f "$IMAGE_TAR"

# ---------------------------------------------------------------- 4. 组装离线包
STAGE="$OUT_DIR/pm-docker-offline-$VERSION"
rm -rf "$STAGE"
mkdir -p "$STAGE/images" "$STAGE/certs"

cp compose.yml compose.tls.yml compose.http.yml Caddyfile.tpl \
   .env.docker.example deploy.sh pmctl.sh lib.sh README.md "$STAGE/"
cp certs/README.md "$STAGE/certs/"
cp "$IMAGE_TAR.gz" "$STAGE/images/"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"

info "打包部署文件..."
tar -czf "$OUT_DIR/pm-docker-offline-$VERSION.tar.gz" -C "$OUT_DIR" "pm-docker-offline-$VERSION"

# 校验和，方便传输后核对
if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$OUT_DIR" && sha256sum "pm-docker-offline-$VERSION.tar.gz" > "pm-docker-offline-$VERSION.tar.gz.sha256" )
elif command -v shasum >/dev/null 2>&1; then
    ( cd "$OUT_DIR" && shasum -a 256 "pm-docker-offline-$VERSION.tar.gz" > "pm-docker-offline-$VERSION.tar.gz.sha256" )
fi

BUNDLE="$OUT_DIR/pm-docker-offline-$VERSION.tar.gz"
SIZE="$(du -h "$BUNDLE" | awk '{print $1}')"

echo ""
echo "=============================================================="
echo " 完成"
echo "=============================================================="
echo ""
echo "  离线包： $BUNDLE  ($SIZE)"
echo "  校验和： ${BUNDLE}.sha256"
echo ""
echo "  目标机部署："
echo "    tar xzf $(basename "$BUNDLE")"
echo "    cd pm-docker-offline-$VERSION"
echo "    cp .env.docker.example .env"
echo "    vi .env                       # 填域名、证书、数据/附件目录"
echo "    sudo bash deploy.sh"
echo ""
echo "  目标机不需要源码、Python、Node、外网，只需要 Docker + 本离线包。"
echo ""
