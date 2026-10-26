#!/usr/bin/env bash
# ============================================================================
# 产教融合项目管理系统 · Docker 一键部署
#
# 用法（在 docker/ 目录）：
#   cp .env.docker.example .env
#   vi .env
#   sudo bash deploy.sh
#
# 做这些事：
#   1. 校验 .env（域名/证书/端口/目录）
#   2. 自动生成 PM_SECRET_KEY、补全 PM_PUBLIC_URL
#   3. 创建宿主机数据/附件/备份目录并调整属主（UID 10001）
#   4. 没有本地镜像时自动 docker load images/pm-images-*.tar.gz
#   5. 生成 Caddyfile，启动 app + caddy
#   6. 等待健康检查，首次部署初始化管理员
# ============================================================================
set -euo pipefail

source "$(dirname "$0")/lib.sh"

# 离线包从 Windows 打包可能丢失可执行位；补一次，方便后续 ./pmctl.sh
chmod +x "$DOCKER_DIR"/*.sh 2>/dev/null || true

if [ ! -f "$ENV_FILE" ]; then
    cp .env.docker.example .env
    chmod 600 .env 2>/dev/null || true
    cat <<'EOF'

已生成 docker/.env（来自 .env.docker.example）。

请先修改以下必填项，再重新运行：
  PM_DOMAIN        访问域名
  PM_TLS_MODE      auto / file / internal / off
  PM_DATA_DIR      SQLite 数据目录（建议绝对路径，如 /data/pm）
  PM_UPLOAD_DIR    附件目录（可与数据目录分盘）

file 模式还需把证书放到 docker/certs/：
  certs/server.crt  证书链（PEM，含中间证书）
  certs/server.key  私钥（PEM，未加密）

修改完成后执行：
  sudo bash deploy.sh
EOF
    exit 0
fi

load_config
require_docker

echo "=============================================================="
echo " 产教融合项目管理系统 · Docker 部署"
echo "=============================================================="
echo "  项目名    : $PM_PROJECT_NAME"
echo "  镜像      : $PM_IMAGE"
echo "  TLS 模式  : $PM_TLS_MODE"
echo "  域名      : ${PM_DOMAIN:-（未配置）}"
echo "  数据目录  : $PM_DATA_DIR"
echo "  附件目录  : $PM_UPLOAD_DIR"
echo ""

# ---------------------------------------------------------------- 1. 校验
case "$PM_TLS_MODE" in
    auto|file|internal)
        case "$PM_DOMAIN" in
            ""|pm.example.edu.cn|example.com|your.domain)
                die "PM_TLS_MODE=$PM_TLS_MODE 需要填写真实域名（当前：'${PM_DOMAIN}'）"
                ;;
        esac
        case "$PM_DOMAIN" in
            http://*|https://*) die "PM_DOMAIN 只填域名，不要带 http:// 或 https://" ;;
            *:*) die "PM_DOMAIN 不要带端口；端口用 PM_HTTP_PORT / PM_HTTPS_PORT" ;;
        esac
        ;;
    off)
        # off 模式允许不填域名；示例值视为空，用服务器 IP 生成访问地址
        case "$PM_DOMAIN" in
            pm.example.edu.cn|example.com|your.domain) PM_DOMAIN="" ;;
        esac
        case "$PM_DOMAIN" in
            http://*|https://*) die "PM_DOMAIN 只填域名，不要带协议" ;;
        esac
        ;;
    *)
        die "PM_TLS_MODE 只能是 auto / file / internal / off"
        ;;
esac

# 证书文件名必须是单纯文件名，避免拼出 ../ 路径
case "$PM_TLS_CERT_NAME" in
    ""|*/*|*..*|*[!A-Za-z0-9._-]*) die "PM_TLS_CERT_NAME 只能是 certs/ 下的文件名（字母数字 . _ -），例如 server.crt" ;;
esac
case "$PM_TLS_KEY_NAME" in
    ""|*/*|*..*|*[!A-Za-z0-9._-]*) die "PM_TLS_KEY_NAME 只能是 certs/ 下的文件名（字母数字 . _ -），例如 server.key" ;;
esac

# ---------------------------------------------------------------- 2. 密钥与访问地址
SECRET="$(env_get PM_SECRET_KEY "")"
case "$SECRET" in
    ""|dev-secret-change-me-in-production|change-me|changeme|secret|*"请替换"*)
        SECRET="$(gen_secret)"
        env_set PM_SECRET_KEY "$SECRET"
        info "已生成 PM_SECRET_KEY 并写入 .env"
        ;;
esac
if [ "${#SECRET}" -lt 32 ]; then
    SECRET="$(gen_secret)"
    env_set PM_SECRET_KEY "$SECRET"
    info "PM_SECRET_KEY 长度不足 32，已重新生成"
fi

# 附件目录为空时补默认值并写回，compose 才能做 bind mount
if [ -z "$(env_get PM_UPLOAD_DIR "")" ]; then
    env_set PM_UPLOAD_DIR "$PM_UPLOAD_DIR"
    info "PM_UPLOAD_DIR 未配置，已写入默认值：$PM_UPLOAD_DIR"
fi

if [ -z "$PM_PUBLIC_URL" ]; then
    if [ "$PM_TLS_MODE" = "off" ]; then
        URL_SCHEME="http"
        URL_HOST="$PM_DOMAIN"
        [ -n "$URL_HOST" ] || URL_HOST="$(host_ip)"
        [ -n "$URL_HOST" ] || URL_HOST="<服务器IP>"
        case "$URL_HOST" in
            *:*) URL_HOST="[$URL_HOST]" ;;
        esac
        URL_PORT="$PM_HTTP_PORT"
    else
        URL_SCHEME="https"
        URL_HOST="$PM_DOMAIN"
        URL_PORT="$PM_HTTPS_PORT"
    fi
    PM_PUBLIC_URL="$URL_SCHEME://$URL_HOST"
    if { [ "$URL_SCHEME" = "http" ] && [ "$URL_PORT" != "80" ]; } || \
       { [ "$URL_SCHEME" = "https" ] && [ "$URL_PORT" != "443" ]; }; then
        PM_PUBLIC_URL="$PM_PUBLIC_URL:$URL_PORT"
    fi
    env_set PM_PUBLIC_URL "$PM_PUBLIC_URL"
    info "已生成 PM_PUBLIC_URL：$PM_PUBLIC_URL"
fi

# ---------------------------------------------------------------- 3. 证书校验
if [ "$PM_TLS_MODE" = "file" ]; then
    CERT_FILE="$DOCKER_DIR/certs/$PM_TLS_CERT_NAME"
    KEY_FILE="$DOCKER_DIR/certs/$PM_TLS_KEY_NAME"
    [ -f "$CERT_FILE" ] || die "找不到证书：$CERT_FILE（请放到 docker/certs/ 或改 PM_TLS_CERT_NAME）"
    [ -f "$KEY_FILE" ]  || die "找不到私钥：$KEY_FILE（请放到 docker/certs/ 或改 PM_TLS_KEY_NAME）"

    if command -v openssl >/dev/null 2>&1; then
        openssl x509 -in "$CERT_FILE" -noout >/dev/null 2>&1 \
            || die "证书无法解析（需要 PEM 格式）：$CERT_FILE"
        openssl pkey -in "$KEY_FILE" -noout >/dev/null 2>&1 \
            || die "私钥无法解析（需要 PEM 格式且不能加密）：$KEY_FILE"
        openssl x509 -in "$CERT_FILE" -noout -checkend 0 >/dev/null 2>&1 \
            || die "证书已过期：$CERT_FILE"
        if ! openssl x509 -in "$CERT_FILE" -noout -checkend 2592000 >/dev/null 2>&1; then
            warn "证书将在 30 天内过期，请提前更换：$(openssl x509 -in "$CERT_FILE" -noout -enddate | cut -d= -f2)"
        else
            info "证书有效期至：$(openssl x509 -in "$CERT_FILE" -noout -enddate | cut -d= -f2)"
        fi
    else
        warn "未安装 openssl，跳过证书解析/有效期检查"
    fi
elif [ "$PM_TLS_MODE" = "auto" ]; then
    warn "auto 模式需要：域名 DNS 已解析到本机，且公网可访问 80/443；否则证书签发会失败"
    [ -n "$PM_ACME_EMAIL" ] && info "ACME 邮箱：$PM_ACME_EMAIL"
elif [ "$PM_TLS_MODE" = "internal" ]; then
    warn "internal 模式使用 Caddy 内部 CA，浏览器默认不信任；正式环境建议用 file 模式"
fi

# ---------------------------------------------------------------- 4. 目录与权限
mkdir -p "$PM_DATA_DIR" "$PM_UPLOAD_DIR" \
         "$PM_DATA_DIR/backup" "$PM_DATA_DIR/log" "$PM_DATA_DIR/locks" \
         "$DOCKER_DIR/certs"

if [ "$(id -u)" -eq 0 ]; then
    if chown -R 10001:10001 "$PM_DATA_DIR" "$PM_UPLOAD_DIR" 2>/dev/null; then
        info "数据/附件目录属主已设为 10001:10001"
    else
        warn "chown 失败（NFS/root-squash？）；请确认容器内 UID 10001 可写这两个目录"
    fi
else
    warn "当前不是 root，跳过 chown；若应用报 'unable to open database file'，请用 sudo 运行或手工 chown -R 10001:10001"
fi

# ---------------------------------------------------------------- 5. 加载镜像
if ! docker image inspect "$PM_IMAGE" >/dev/null 2>&1 || \
   ! docker image inspect "$PM_CADDY_IMAGE" >/dev/null 2>&1; then
    IMAGE_TARBALL="$(find images -maxdepth 1 -type f \( -name 'pm-images-*.tar.gz' -o -name 'pm-images-*.tar' \) -print 2>/dev/null | sort | tail -n 1 || true)"
    [ -n "$IMAGE_TARBALL" ] || die "本地缺少镜像 $PM_IMAGE / $PM_CADDY_IMAGE，且 images/ 下没有 pm-images-*.tar.gz。请先在有网机器构建离线包，或手工 docker load。"
    info "加载离线镜像：$IMAGE_TARBALL（首次约 1-3 分钟）..."
    case "$IMAGE_TARBALL" in
        *.gz) gzip -dc "$IMAGE_TARBALL" | docker load ;;
        *)    docker load -i "$IMAGE_TARBALL" ;;
    esac
    docker image inspect "$PM_IMAGE" >/dev/null 2>&1 \
        || die "镜像加载后仍找不到 $PM_IMAGE，请检查离线包与 PM_IMAGE 是否一致"
    docker image inspect "$PM_CADDY_IMAGE" >/dev/null 2>&1 \
        || die "镜像加载后仍找不到 $PM_CADDY_IMAGE，请检查离线包"
    info "镜像加载完成"
else
    info "本地已有镜像，跳过加载"
fi

# ---------------------------------------------------------------- 6. 生成 Caddyfile
render_caddyfile() {
    local site tls_block email_line="" escaped_site escaped_tls escaped_email
    case "$PM_TLS_MODE" in
        off)      site=":80"; tls_block="" ;;
        auto)     site="$PM_DOMAIN"; tls_block="" ;;
        file)     site="$PM_DOMAIN"; tls_block="    tls /certs/$PM_TLS_CERT_NAME /certs/$PM_TLS_KEY_NAME" ;;
        internal) site="$PM_DOMAIN"; tls_block="    tls internal" ;;
    esac
    [ -n "$PM_ACME_EMAIL" ] && email_line="    email $PM_ACME_EMAIL"

    escaped_site="${site//&/\\&}"; escaped_site="${escaped_site//|/\\|}"
    escaped_tls="${tls_block//&/\\&}"; escaped_tls="${escaped_tls//|/\\|}"
    escaped_email="${email_line//&/\\&}"; escaped_email="${escaped_email//|/\\|}"

    sed -e "s|__SITE_ADDRESS__|$escaped_site|g" \
        -e "s|__TLS_BLOCK__|$escaped_tls|g" \
        -e "s|__ACME_EMAIL__|$escaped_email|g" \
        Caddyfile.tpl > Caddyfile
}
render_caddyfile
info "Caddyfile 已生成"

# ---------------------------------------------------------------- 7. 启动
info "启动容器..."
compose up -d --remove-orphans
wait_healthy 180

# Caddy 启动失败时给出日志，而不是只看到 app 健康就以为成功
sleep 2
if ! docker inspect -f '{{.State.Running}}' "$CADDY_CONTAINER" 2>/dev/null | grep -q true; then
    docker logs --tail=100 "$CADDY_CONTAINER" >&2 || true
    die "Caddy 启动失败，请查看上面的日志（常见：证书文件名/权限、80/443 被占用）"
fi
info "Caddy 运行中"

# ---------------------------------------------------------------- 8. 首次初始化管理员
HAS_USER="$(compose exec -T app python -c "from sqlalchemy import select; from app.db.session import SessionLocal; from app.models.system import SysUser; db=SessionLocal(); print('yes' if db.scalar(select(SysUser)) else 'no')" 2>/dev/null | tr -d '\r\n' || true)"
ADMIN_CREATED=0
if [ "$HAS_USER" != "yes" ]; then
    info "数据库暂无用户，初始化管理员：$PM_ADMIN_USER"
    if compose exec -T app python scripts/seed_admin.py "$PM_ADMIN_USER" "$PM_ADMIN_PASSWORD"; then
        ADMIN_CREATED=1
    else
        warn "管理员初始化失败；可稍后执行：./pmctl.sh seed-admin"
    fi
else
    info "数据库已有用户，跳过管理员初始化（不会重置密码）"
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true

# ---------------------------------------------------------------- 9. 汇总
echo ""
echo "=============================================================="
echo " 部署完成"
echo "=============================================================="
echo ""
echo "  访问地址： $PM_PUBLIC_URL"
echo "  管理员  ： $PM_ADMIN_USER"
if [ "$ADMIN_CREATED" = "1" ]; then
    echo "  初始密码： $PM_ADMIN_PASSWORD"
    echo ""
    echo "  ⚠️  请登录后立即修改管理员密码"
fi
echo ""
echo "  数据目录： $PM_DATA_DIR"
echo "  附件目录： $PM_UPLOAD_DIR"
echo "  备份目录： $PM_DATA_DIR/backup"
echo ""
echo "  常用命令："
echo "    ./pmctl.sh status          查看状态"
echo "    ./pmctl.sh logs            查看日志"
echo "    ./pmctl.sh restart         重启"
echo "    ./pmctl.sh backup          立即备份数据库"
echo "    ./pmctl.sh cert-check      检查证书有效期"
echo "    ./pmctl.sh help            全部命令"
echo ""
if [ "$PM_TLS_MODE" = "off" ]; then
    echo "  提示：off 模式未启用 TLS；若前面有网关，请把网关的 X-Forwarded-Proto 设为 https，"
    echo "        并在 .env 中把 PM_PUBLIC_URL 设为实际的 https 地址。"
    echo ""
fi
echo "  提示：云服务器还需在安全组/防火墙放通 $PM_HTTP_PORT 和 $PM_HTTPS_PORT 端口。"
echo ""
