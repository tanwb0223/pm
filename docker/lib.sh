#!/usr/bin/env bash
# ============================================================================
# 公共函数：读取 .env、选择 compose 覆盖文件、等待健康检查
# 由 deploy.sh / pmctl.sh source，不单独执行。
# ============================================================================
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DOCKER_DIR"

ENV_FILE="$DOCKER_DIR/.env"

die()  { printf '\033[31m[X]\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }

# 读取 .env 中某个键（支持单/双引号，不支持行尾注释）
env_get() {
    local key="$1" default="${2-}" line val
    line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
    if [ -z "$line" ]; then
        printf '%s' "$default"
        return 0
    fi
    val="${line#*=}"
    val="${val%$'\r'}"
    case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    printf '%s' "$val"
}

# 写回 .env（键不存在则追加）
env_set() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
        BEGIN { found = 0 }
        $0 ~ "^[[:space:]]*" k "=" { print k "=" v; found = 1; next }
        { print }
        END { if (!found) print k "=" v }
    ' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
}

load_config() {
    [ -f "$ENV_FILE" ] || die "缺少 .env：请先 cp .env.docker.example .env 并编辑"

    PM_PROJECT_NAME="$(env_get PM_PROJECT_NAME pm)"
    PM_IMAGE="$(env_get PM_IMAGE pm-app:latest)"
    PM_CADDY_IMAGE="$(env_get PM_CADDY_IMAGE caddy:2-alpine)"
    PM_TLS_MODE="$(env_get PM_TLS_MODE auto)"
    PM_DOMAIN="$(env_get PM_DOMAIN "")"
    PM_HTTP_PORT="$(env_get PM_HTTP_PORT 80)"
    PM_HTTPS_PORT="$(env_get PM_HTTPS_PORT 443)"
    PM_DATA_DIR="$(env_get PM_DATA_DIR ./data)"
    PM_UPLOAD_DIR="$(env_get PM_UPLOAD_DIR "")"
    PM_PUBLIC_URL="$(env_get PM_PUBLIC_URL "")"
    PM_TLS_CERT_NAME="$(env_get PM_TLS_CERT_NAME server.crt)"
    PM_TLS_KEY_NAME="$(env_get PM_TLS_KEY_NAME server.key)"
    PM_ACME_EMAIL="$(env_get PM_ACME_EMAIL "")"
    PM_ADMIN_USER="$(env_get PM_ADMIN_USER admin)"
    PM_ADMIN_PASSWORD="$(env_get PM_ADMIN_PASSWORD Admin@123)"
    PM_WORKERS="$(env_get PM_WORKERS 1)"

    # 附件目录留空时默认跟随数据目录（deploy.sh 会写回 .env，compose 才能读到）
    [ -n "$PM_UPLOAD_DIR" ] || PM_UPLOAD_DIR="$PM_DATA_DIR/upload"

    APP_CONTAINER="${PM_PROJECT_NAME}-app"
    CADDY_CONTAINER="${PM_PROJECT_NAME}-caddy"

    case "$PM_TLS_MODE" in
        off)                 COMPOSE_FILES=(-f compose.yml -f compose.http.yml) ;;
        auto|file|internal)  COMPOSE_FILES=(-f compose.yml -f compose.tls.yml) ;;
        *) die "PM_TLS_MODE 只能是 auto / file / internal / off，当前：$PM_TLS_MODE" ;;
    esac
}

compose() {
    docker compose "${COMPOSE_FILES[@]}" "$@"
}

require_docker() {
    command -v docker >/dev/null 2>&1 || die "未安装 Docker"
    docker info >/dev/null 2>&1 || die "Docker 未运行，或当前用户无权访问 Docker（可试 sudo）"
    docker compose version >/dev/null 2>&1 || die "未安装 Docker Compose v2（docker compose）"
}

wait_healthy() {
    local timeout="${1:-180}" elapsed=0 status
    info "等待应用健康检查（最长 ${timeout}s）..."
    while [ "$elapsed" -lt "$timeout" ]; do
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$APP_CONTAINER" 2>/dev/null || echo missing)"
        case "$status" in
            healthy)
                info "应用已就绪"
                return 0
                ;;
            unhealthy)
                docker logs --tail=100 "$APP_CONTAINER" >&2 || true
                die "应用健康检查失败，请查看上面的日志"
                ;;
        esac
        sleep 3
        elapsed=$((elapsed + 3))
    done
    docker logs --tail=100 "$APP_CONTAINER" >&2 || true
    die "等待应用就绪超时（${timeout}s）"
}

# 生成 URL 安全随机串
gen_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

host_ip() {
    local ips ip
    ips="$(hostname -I 2>/dev/null || true)"
    ip="$(printf '%s\n' "$ips" | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -n1 || true)"
    [ -n "$ip" ] || ip="$(printf '%s\n' "$ips" | awk '{print $1}')"
    printf '%s' "$ip"
}
