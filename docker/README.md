# Docker 部署（产教融合项目管理系统）

> 目标：**有网机器构建一个离线包 → 目标机只需要 Docker + 拷贝离线包 → 一条命令跑起来**。
> 运行镜像只含 FastAPI + 前端静态文件 + SQLite 运行依赖，不含 Node、编译工具、PostgreSQL 驱动。

---

## 一、方案

```
浏览器
  │  HTTPS 443 / HTTP 80
  ▼
┌─────────────────────────────┐
│ Caddy（官方镜像）            │
│  TLS 终止 / 自动续期 / 反代  │
└─────────────┬───────────────┘
              │ app:8000（仅容器网络）
              ▼
┌─────────────────────────────┐
│ pm-app（本仓库构建）         │
│ FastAPI + React 静态文件     │
│ 非 root（UID 10001）         │
└─────────────┬───────────────┘
              │ bind mount
              ▼
┌─────────────────────────────┐
│ 宿主机 PM_DATA_DIR           │
│  ├── pm.db（SQLite）         │
│  ├── backup/                 │
│  ├── log/                    │
│  └── locks/                  │
│ 宿主机 PM_UPLOAD_DIR         │
│  └── 附件文件                │
└─────────────────────────────┘
```

| 镜像 | 内容 | 说明 |
|---|---|---|
| `pm-app:latest` | Python 3.12 + FastAPI + 前端 `dist` | 多阶段构建，运行镜像不含 Node/源码构建工具 |
| `caddy:2-alpine` | Caddy 反向代理 | 官方镜像，负责域名、证书、HTTPS |

离线包会把 **两个镜像** 一起 `docker save` 成一个压缩包；目标机 `docker load` 后即可运行，**不需要源码、Python、Node、npm、外网**。

---

## 二、目录结构

```
docker/
├── Dockerfile                 应用镜像（多阶段构建）
├── compose.yml                app + caddy 基础编排
├── compose.tls.yml            TLS 模式端口覆盖（80/443）
├── compose.http.yml           off 模式端口覆盖（仅 HTTP）
├── compose.build.yml          仅构建用（docker compose build）
├── Caddyfile.tpl              Caddy 配置模板（deploy.sh 渲染）
├── .env.docker.example        部署配置样例
├── deploy.sh                  目标机一键部署
├── pmctl.sh                   日常运维命令
├── lib.sh                     脚本公共函数
├── build-offline.sh           Linux/macOS/WSL 构建离线包
├── build-offline.ps1          Windows 构建离线包
├── certs/                     自有证书目录（file 模式）
└── README.md                  本文件
```

运行后在 `docker/` 下还会生成：

```
.env                  实际配置（含密钥，权限 600）
Caddyfile             由模板生成
data/                 默认数据目录（PM_DATA_DIR）
certs/                证书目录
dist/                 离线包输出目录
images/               镜像 tar.gz
```

---

## 三、构建离线包（在有网、有 Docker 的机器上）

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File docker\build-offline.ps1
```

### Linux / macOS / WSL

```bash
bash docker/build-offline.sh
```

产物：

```
docker/dist/pm-docker-offline-<版本>.tar.gz
docker/dist/pm-docker-offline-<版本>.tar.gz.sha256
```

可选参数：

| 参数（bash / PowerShell） | 默认 | 说明 |
|---|---|---|
| `PM_VERSION` / `-Version` | git 短 SHA | 镜像 tag / 包名 |
| `PM_PLATFORM` / `-Platform` | `linux/amd64` | 目标机架构；ARM 用 `linux/arm64` |
| `PM_IMAGE_NAME` / `-AppImage` | `pm-app` | 应用镜像名 |
| `PM_CADDY_IMAGE` / `-CaddyImage` | `caddy:2-alpine` | Caddy 镜像 |
| `PM_OUT_DIR` / `-OutputDir` | `docker/dist` | 输出目录 |

构建首次会下载 `alpine:3.21`、`node:20-alpine`、`caddy:2-alpine`，并编译前端，约 3~8 分钟；后续有缓存会快很多。构建脚本内部使用 `docker compose -f compose.build.yml build`，也可以手工执行。

> 构建和运行都基于 Docker Compose：构建用 `docker compose -f compose.build.yml build`，运行用 `docker compose -f compose.yml -f compose.tls.yml up -d`。脚本只是把 .env 校验、Caddyfile 生成、镜像 load、健康检查串起来，没有使用 `docker run`。

> **离线构建**：如果构建机也不能联网，需要先把 `alpine:3.21`、`node:20-alpine`、`caddy:2-alpine` 三个基础镜像 `docker pull`/`docker load` 到构建机，再执行构建。构建阶段需要 npm 依赖，因此构建机仍需能访问 npm registry（或提前准备 pnpm store）。

---

## 四、目标机部署（离线）

### 4.1 前提

- Linux x86_64（ARM 需用 `PM_PLATFORM=linux/arm64` 构建）
- Docker 24+，Docker Compose v2（`docker compose version` 可用）
- 建议 2 核 / 2 GB 内存 / 40 GB 磁盘（附件多则按实际加）
- 已放通计划使用的端口（默认 80/443）

### 4.2 三步

```bash
# 1. 上传离线包后解压
tar xzf pm-docker-offline-<版本>.tar.gz
cd pm-docker-offline-<版本>

# 2. 配置
cp .env.docker.example .env
vi .env

# 3. 部署（root 或 docker 组用户）
sudo bash deploy.sh
```

`deploy.sh` 会自动：

1. 校验域名、证书、端口、目录；
2. 生成 `PM_SECRET_KEY`（64 位随机串）并写回 `.env`；
3. 按域名/端口生成 `PM_PUBLIC_URL`；
4. 创建 `PM_DATA_DIR`、`PM_UPLOAD_DIR`、`backup/log/locks`，并 `chown 10001:10001`；
5. 如果本地没有镜像，自动 `docker load images/pm-images-*.tar.gz`；
6. 生成 `Caddyfile`，启动 `app` + `caddy`；
7. 等待健康检查，首次部署自动创建管理员；
8. 打印访问地址、初始密码、数据目录和常用命令。

> 首次 `docker load` 约 1~3 分钟，之后启动只需几秒。

### 4.3 验证

```bash
./pmctl.sh status
curl -k https://你的域名/api/health
```

浏览器访问 `PM_PUBLIC_URL`，用 `PM_ADMIN_USER` / `PM_ADMIN_PASSWORD` 登录。

---

## 五、配置说明（`.env`）

### 5.1 必改项

| 变量 | 示例 | 说明 |
|---|---|---|
| `PM_DOMAIN` | `pm.school.edu.cn` | 访问域名；`off` 模式可留空用服务器 IP |
| `PM_TLS_MODE` | `file` | `auto` / `file` / `internal` / `off`，见下一节 |
| `PM_DATA_DIR` | `/data/pm` | SQLite 数据根目录；**生产建议绝对路径** |
| `PM_UPLOAD_DIR` | `/data/pm/upload` | 附件目录；可单独挂盘/NAS |
| `PM_TLS_CERT_NAME` | `server.crt` | `file` 模式证书文件名（放 `certs/`） |
| `PM_TLS_KEY_NAME` | `server.key` | `file` 模式私钥文件名 |

### 5.2 常用项

| 变量 | 默认 | 说明 |
|---|---|---|
| `PM_HTTP_PORT` | `80` | 宿主机 HTTP 端口 |
| `PM_HTTPS_PORT` | `443` | 宿主机 HTTPS 端口 |
| `PM_PUBLIC_URL` | 自动生成 | 对外访问地址；前置网关终止 HTTPS 时手工填 `https://...` |
| `PM_SECRET_KEY` | 自动生成 | JWT 密钥；**已有部署不要改**，否则所有登录失效 |
| `PM_ADMIN_USER` | `admin` | 首次管理员账号 |
| `PM_ADMIN_PASSWORD` | `Admin@123` | 首次管理员密码，登录后立即改 |
| `PM_WORKERS` | `1` | uvicorn 进程数；SQLite 建议 1 |
| `PM_UPLOAD_MAX_SIZE_MB` | `50` | 单附件大小上限 |
| `PM_BACKUP_ENABLED` | `true` | 每日 02:00 自动备份数据库 |
| `PM_BACKUP_KEEP_DAYS` | `30` | 备份保留天数 |
| `PM_SCHEDULER_ENABLED` | `true` | 定时任务（快照/预警/摘要/备份） |
| `PM_CLEANUP_ENABLED` | `false` | 历史数据清理，默认关闭 |

### 5.3 路径规则

- 相对路径相对于 **`docker/` 目录**（compose 文件所在目录）；
- 绝对路径直接使用；
- 两个目录都可以在挂载的磁盘/NAS 上，只要容器 UID `10001` 有读写权限；
- 默认布局：

```
${PM_DATA_DIR}/
├── pm.db              SQLite 主库
├── pm.db-wal          WAL（运行时）
├── pm.db-shm          WAL 共享内存（运行时）
├── backup/            数据库备份
├── log/               日志目录（应用日志主要走 docker logs）
└── locks/             定时任务文件锁

${PM_UPLOAD_DIR}/      附件（按 YYYY/MM/uuid.ext 存放）
```

---

## 六、域名与证书

### 6.1 `file` 模式（内网/学校统一证书，推荐）

```ini
PM_DOMAIN=pm.school.edu.cn
PM_TLS_MODE=file
PM_TLS_CERT_NAME=server.crt
PM_TLS_KEY_NAME=server.key
```

把文件放到 `docker/certs/`：

- `server.crt`：服务器证书 + 中间证书（fullchain），PEM 格式；
- `server.key`：私钥，PEM 格式，**不能加密**；
- 私钥建议 `chmod 600`。

`deploy.sh` 会检查证书能否解析、是否过期、是否在 30 天内到期；`./pmctl.sh cert-check` 可随时复查。

### 6.2 `auto` 模式（公网域名 + Let's Encrypt）

```ini
PM_DOMAIN=pm.school.edu.cn
PM_TLS_MODE=auto
PM_ACME_EMAIL=admin@school.edu.cn
```

前提：

- 域名 A/AAAA 记录已指向本机；
- 公网可访问 80/443（ACME HTTP-01 / TLS-ALPN）；
- 云安全组、防火墙已放通。

Caddy 自动申请并续期，证书保存在 Docker 命名卷 `caddy_data` 中，重启/升级不会丢。

### 6.3 `internal` 模式（自签，测试用）

```ini
PM_DOMAIN=pm.school.edu.cn
PM_TLS_MODE=internal
```

Caddy 用内部 CA 签发。浏览器默认不信任，会提示证书风险；适合内网测试或临时过渡。正式环境建议换 `file` 或 `auto`。

### 6.4 `off` 模式（前置网关终止 HTTPS）

```ini
PM_TLS_MODE=off
PM_HTTP_PORT=8080
PM_PUBLIC_URL=https://pm.school.edu.cn
```

只映射 HTTP 端口，TLS 由前面的 Nginx/F5/WAF 等终止。前置网关需要：

- 转发到本机 `PM_HTTP_PORT`；
- 设置 `X-Forwarded-Proto: https`、`X-Forwarded-Host`、`X-Forwarded-For`；
- `PM_PUBLIC_URL` 填实际 https 地址（SSO 回调、页面跳转用）。

### 6.5 端口被占用

TLS 模式默认映射宿主机 80/443。如果已被 Nginx 等占用：

- 改 `PM_HTTP_PORT` / `PM_HTTPS_PORT` 为其他端口；或
- 用 `off` 模式，把 `PM_HTTP_PORT` 改成空闲端口，由前置网关转发。

> TLS 模式如果改非 443 端口，Caddy 的 HTTP→HTTPS 跳转仍可能指向 443，建议正式环境保持 80/443。

---

## 七、日常运维

所有命令在 `docker/` 目录执行（离线包解压目录同）。

| 命令 | 作用 |
|---|---|
| `./pmctl.sh status` | 容器状态、访问地址、磁盘占用 |
| `./pmctl.sh logs` | 跟踪 app + caddy 日志 |
| `./pmctl.sh logs app` | 只看应用日志 |
| `./pmctl.sh restart` | 重启并等待健康检查 |
| `./pmctl.sh down` | 停止（**保留数据目录**） |
| `./pmctl.sh up` | 重新拉起 |
| `./pmctl.sh backup` | 立即备份数据库 |
| `./pmctl.sh backup --with-files` | 数据库 + 附件打包（附件大时较慢） |
| `./pmctl.sh backups` | 列出备份 |
| `./pmctl.sh verify` | 校验最近一次备份完整性 |
| `./pmctl.sh restore <文件名>` | 从备份恢复数据库并重启 |
| `./pmctl.sh seed-admin [账号] [密码]` | 创建/重置管理员 |
| `./pmctl.sh cert-check` | 检查证书有效期 |
| `./pmctl.sh upgrade <镜像包>` | 离线升级：加载镜像 + 重启 + 健康检查 |
| `./pmctl.sh shell` | 进入 app 容器 |
| `./pmctl.sh config` | 打印最终 compose 配置 |

### 7.1 备份

- 数据库：应用内置定时任务每天 02:00 备份到 `${PM_DATA_DIR}/backup`，保留 `PM_BACKUP_KEEP_DAYS` 天；
- 附件：`./pmctl.sh backup --with-files` 会额外打 `upload_YYYYmmdd_HHMMSS.tar.gz`；
- **异地备份**：把 `${PM_DATA_DIR}/backup` 和 `${PM_UPLOAD_DIR}` 同步到另一台机器/对象存储；
- 备份文件包含业务数据，注意访问权限。

### 7.2 恢复

```bash
./pmctl.sh backups
./pmctl.sh restore pm_20261026_020000.db.gz
```

恢复脚本会先自动备份当前库，再替换 `pm.db`，然后重启 app。**附件不会随数据库恢复**，需要单独从附件备份恢复。

### 7.3 升级

```bash
# 有网机器：拉新代码 → 重新构建离线包
bash docker/build-offline.sh

# 目标机：上传新包 → 解压 → 用新镜像升级
cd pm-docker-offline-<新版本>
sudo bash deploy.sh          # 首次安装用；升级也可以用
# 或只换镜像：
./pmctl.sh upgrade /path/to/pm-images-<新版本>.tar.gz
```

升级不会动 `PM_DATA_DIR` / `PM_UPLOAD_DIR`；SQLite 表结构由应用启动时自动迁移/补齐。

### 7.4 从原生部署迁移

原生部署（systemd + SQLite）迁到 Docker：

```bash
# 1. 停止原生服务
systemctl stop pm

# 2. 复制数据（连同 WAL/SHM）
mkdir -p /data/pm
cp /data/pm/pm.db /data/pm/pm.db-wal /data/pm/pm.db-shm /data/pm/ 2>/dev/null || true
cp -a /data/pm/upload/. /data/pm/upload/

# 3. 改 .env 指向 /data/pm，启动
sudo bash deploy.sh
```

如果原生库正在运行，建议先在原生环境执行 `python scripts/backup.py`，再用备份恢复到 Docker，避免 WAL 不一致。

---

## 八、常见问题

### 8.1 应用报 `unable to open database file`

宿主机数据目录对容器 UID `10001` 不可写。执行：

```bash
sudo chown -R 10001:10001 /data/pm /data/pm/upload
```

NFS/root-squash 环境需要让 NAS 侧允许 UID 10001 写入，或把数据目录放在本地盘。

### 8.2 80/443 被占用

```bash
sudo ss -lntp | grep -E ':80|:443'
```

改 `.env` 的 `PM_HTTP_PORT` / `PM_HTTPS_PORT`，或使用 `off` 模式。

### 8.3 证书相关报错

- `tls` 证书必须是 **PEM**，不能是 PFX/JKS；PFX 先转换；
- `server.crt` 要包含中间证书（fullchain），否则部分浏览器/移动端报错；
- 私钥不能有密码；Caddy 不会交互式输入密码；
- 检查域名与证书 SAN 是否匹配；
- `./pmctl.sh cert-check` 看有效期。

### 8.4 `auto` 模式签不出证书

- DNS 是否已解析到本机；
- 80/443 是否被防火墙/安全组挡住；
- 是否有其他服务占用 80；
- Let's Encrypt 有频率限制，失败后不要反复重启；
- 查看 `./pmctl.sh logs caddy`。

### 8.5 导出的 PDF 中文乱码/500

运行镜像已安装 `fonts-wqy-zenhei`，正常不需要额外处理。如果替换了基础镜像或裁剪了字体，确认容器内存在：

```bash
docker exec -it pm-app ls /usr/share/fonts/truetype/wqy/
```

也可以在 `backend/fonts/` 放 `simhei.ttf` 后重新构建镜像。

### 8.6 忘记管理员密码

```bash
./pmctl.sh seed-admin admin '新密码'
```

该命令幂等：用户存在则重置密码并确保有 `SYS_ADMIN` 角色。

### 8.7 离线包加载失败

```bash
gzip -dc images/pm-images-*.tar.gz | docker load
docker images | grep -E 'pm-app|caddy'
```

确认 `.env` 的 `PM_IMAGE` 与离线包里的 tag 一致（默认 `pm-app:latest`）。

### 8.8 SELinux（CentOS/RHEL）

如果 Docker 日志报 `permission denied` 且系统开启 SELinux：

```bash
sudo chcon -Rt container_file_t /data/pm /data/pm/upload
```

或在 compose 的 volumes 后加 `:Z`（会递归改 SELinux 标签，确认目录专用后再用）。

---

## 九、安全说明

- app 容器以非 root `10001` 运行，`cap_drop: ALL` + `no-new-privileges`；
- app 不映射宿主机端口，只通过 Caddy 访问；
- `.env` 含 `PM_SECRET_KEY`，部署后权限 600；
- 初始管理员密码 `Admin@123` 仅用于首次登录，**上线后立即修改**；
- 证书私钥只读挂载到 Caddy，建议 `chmod 600`；
- 备份目录和附件目录含业务数据，注意宿主机访问权限与异地备份加密。

---

## 十、镜像体积说明

本方案在体积上做了这些取舍：

| 做法 | 效果 |
|---|---|
| 多阶段构建，只复制前端 `dist` | 运行镜像不含 Node、node_modules、前端源码 |
| 运行镜像去掉 `psycopg[binary]` / `libpq` | 只支持 SQLite；省约 10~15 MB |
| 不装 `uvicorn[standard]` 的 uvloop/httptools | 少约 5 MB；400 人规模足够 |
| 不保留 pip 缓存、apk 索引、文档、pip 自身 | 少约 20 MB |
| 中文字体只保留 TTC，卸掉 fontconfig 等安装期依赖 | PDF 可用，字体层从 32MB 降到 16MB |

实测（`linux/amd64`，2026-10）：`docker images` 显示应用镜像 **约 188 MB**，实际 rootfs 约 133 MB；离线包（app + Caddy，gzip 后）**约 70 MB**。离线包才是需要拷到目标机的文件。

> 如果只需要 HTTP 且已有反向代理，可以不用 Caddy 镜像，把 app 直接暴露给前置网关；但默认方案保留 Caddy，域名/证书配置最简单。

---

## 十一、卸载

```bash
cd docker
./pmctl.sh down
# 确认不再需要数据后，再手工删除数据目录（不可恢复）
# rm -rf /data/pm
```

`down` 不会删除 `PM_DATA_DIR` / `PM_UPLOAD_DIR`，也不会删除 Caddy 的 `caddy_data` 命名卷（证书缓存）。彻底清理：

```bash
docker compose -f compose.yml -f compose.tls.yml down -v
```

> 删除命名卷会丢失 Caddy 自动申请的证书缓存；`file` 模式证书在 `certs/`，不受影响。
