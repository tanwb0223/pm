#!/usr/bin/env bash
# ============================================================================
# 产教融合项目管理系统 · Docker 运维命令
#
# 用法：
#   ./pmctl.sh status
#   ./pmctl.sh logs [app|caddy]
#   ./pmctl.sh restart
#   ./pmctl.sh backup [--with-files]
#   ./pmctl.sh restore <备份文件名>
#   ./pmctl.sh verify
#   ./pmctl.sh seed-admin [账号] [密码]
#   ./pmctl.sh cert-check
#   ./pmctl.sh upgrade <pm-images-xxx.tar.gz>
#   ./pmctl.sh shell
# ============================================================================
set -euo pipefail

source "$(dirname "$0")/lib.sh"
load_config
require_docker

CMD="${1:-help}"
shift || true

case "$CMD" in
    status)
        compose ps
        echo ""
        echo "访问地址： ${PM_PUBLIC_URL:-（未生成，请运行 deploy.sh）}"
        echo "数据目录： $PM_DATA_DIR"
        echo "附件目录： $PM_UPLOAD_DIR"
        echo "备份目录： $PM_DATA_DIR/backup"
        echo ""
        du -sh "$PM_DATA_DIR" "$PM_UPLOAD_DIR" "$PM_DATA_DIR/backup" 2>/dev/null || true
        df -h "$PM_DATA_DIR" "$PM_UPLOAD_DIR" 2>/dev/null | awk 'NR==1 || !seen[$0]++' || true
        ;;
    logs)
        compose logs -f --tail=200 "$@"
        ;;
    up)
        compose up -d --remove-orphans
        wait_healthy
        ;;
    restart)
        compose restart
        wait_healthy
        ;;
    down)
        compose down
        info "已停止（数据目录保留：$PM_DATA_DIR）"
        ;;
    backup)
        info "开始备份数据库..."
        compose exec -T app python scripts/backup.py
        if [ "${1:-}" = "--with-files" ]; then
            STAMP="$(date +%Y%m%d_%H%M%S)"
            info "打包附件到备份目录（大附件会较慢）..."
            compose exec -T app tar -czf "/data/pm/backup/upload_${STAMP}.tar.gz" -C /data/pm/upload .
            info "附件备份：upload_${STAMP}.tar.gz"
        fi
        echo ""
        ls -lh "$PM_DATA_DIR/backup" | tail -n 8
        ;;
    backups)
        ls -lh "$PM_DATA_DIR/backup"
        ;;
    verify)
        compose exec -T app python scripts/backup.py --verify
        ;;
    restore)
        FILE="${1:-}"
        [ -n "$FILE" ] || die "用法：./pmctl.sh restore <备份文件名>（先用 ./pmctl.sh backups 查看）"
        case "$FILE" in
            /*) CONTAINER_FILE="$FILE" ;;
            *)  CONTAINER_FILE="/data/pm/backup/$FILE" ;;
        esac
        compose exec -T app test -f "$CONTAINER_FILE" \
            || die "备份文件不存在：$CONTAINER_FILE"
        warn "恢复会覆盖当前数据库（脚本会先自动备份当前库）；附件不会被恢复"
        compose exec -T app python scripts/backup.py --restore "$CONTAINER_FILE"
        compose restart app
        wait_healthy
        info "数据库已恢复，服务已重启"
        ;;
    seed-admin)
        USER_ARG="${1:-$PM_ADMIN_USER}"
        PASS_ARG="${2:-$PM_ADMIN_PASSWORD}"
        compose exec -T app python scripts/seed_admin.py "$USER_ARG" "$PASS_ARG"
        ;;
    cert-check)
        case "$PM_TLS_MODE" in
            file)
                CERT="$DOCKER_DIR/certs/$PM_TLS_CERT_NAME"
                [ -f "$CERT" ] || die "证书不存在：$CERT"
                if ! command -v openssl >/dev/null 2>&1; then
                    warn "未安装 openssl，无法检查"
                    exit 0
                fi
                openssl x509 -in "$CERT" -noout -subject -issuer -dates
                if openssl x509 -in "$CERT" -noout -checkend 2592000 >/dev/null 2>&1; then
                    info "证书 30 天内有效"
                else
                    warn "证书将在 30 天内过期，请尽快更换"
                fi
                ;;
            auto)
                info "auto 模式由 Caddy 自动续期；最近日志："
                compose logs --tail=80 caddy 2>/dev/null | grep -Ei 'certificate|acme|tls' || true
                ;;
            internal)
                info "internal 模式使用 Caddy 内部 CA，证书由 Caddy 自动管理"
                ;;
            off)
                info "off 模式未启用 TLS，证书由前置网关管理"
                ;;
        esac
        ;;
    upgrade)
        TARBALL="${1:-}"
        [ -n "$TARBALL" ] || die "用法：./pmctl.sh upgrade <pm-images-xxx.tar.gz>"
        [ -f "$TARBALL" ] || die "文件不存在：$TARBALL"
        info "升级前先备份数据库..."
        compose exec -T app python scripts/backup.py || warn "备份失败，继续升级（请人工确认）"
        info "加载新镜像..."
        case "$TARBALL" in
            *.gz) gzip -dc "$TARBALL" | docker load ;;
            *)    docker load -i "$TARBALL" ;;
        esac
        compose up -d --remove-orphans
        wait_healthy
        info "升级完成"
        ;;
    shell)
        compose exec app sh
        ;;
    config)
        compose config
        ;;
    help|*)
        cat <<'EOF'
用法：./pmctl.sh <命令>

  status                     查看容器状态、访问地址、磁盘占用
  logs [app|caddy]           跟踪日志
  up                         启动/重新拉起
  restart                    重启
  down                       停止（保留数据）
  backup [--with-files]      立即备份数据库（可选同时打包附件）
  backups                    列出备份文件
  verify                     校验最近一次数据库备份
  restore <文件名>           从备份恢复数据库并重启
  seed-admin [账号] [密码]   创建/重置管理员（幂等）
  cert-check                 检查证书有效期
  upgrade <镜像包>           离线升级：加载镜像 + 重启 + 健康检查
  shell                      进入 app 容器
  config                     打印最终 compose 配置
EOF
        ;;
esac
