# Linux 部署指南（含内网离线）

从打包到部署的完整流程，支持联网与内网离线两种场景。

---

## 一、整体流程

```
能联网的机器                     内网 Linux 服务器
──────────────                   ─────────────────
拷贝源码目录（不用 git）
        ↓
bash pack.sh
        ↓
pm-offline-linux.tar.gz  ──拷贝──→  tar xzf
                                      ↓
                                   sudo bash install.sh
```

**打包一次，内网一条命令装完。**

全程不需要 git：源码用 U 盘或共享盘拷到打包机即可。

---

## 二、前提条件

内网服务器需要预先装好两样东西（离线安装方法见第五节）：

| 软件 | 版本 | 检查命令 |
|---|---|---|
| Python | **3.12** | `python3.12 --version` |
| PostgreSQL | **18** | `psql --version` |

系统建议：Ubuntu 22.04 / CentOS 7+ / 麒麟 V10，2 核 4GB 起。

---

## 三、打包（在能联网的机器上）

### 3.1 准备源码

**不需要 git**。把源码目录整个拷到打包机上即可：

- U 盘拷贝
- 或从内网共享盘复制
- 或解压源码压缩包

只要保证目录结构完整：

```
pm/
├── backend/
├── frontend/
├── pack.sh
├── deploy-linux.sh
└── deploy-windows.ps1
```

### 3.2 执行打包

**Linux / macOS 打包机**：

```bash
cd pm

# 同时打包 Windows 和 Linux（推荐）
bash pack.sh

# 或只打 Linux
bash pack.sh linux
```

**Windows 打包机**（用 PowerShell，见 `pack.ps1`）：

```powershell
cd pm
.\pack.ps1            # 同时打包双平台
.\pack.ps1 -Target linux
```

### 3.3 打包机需要什么

| 依赖 | 用途 | 必需 |
|---|---|---|
| Python 3.12 | 下载与目标平台匹配的 wheel 包 | ✅ 必须 |
| Node.js 18+ | 构建前端（如果 `frontend/dist` 已存在则跳过） | 视情况 |
| 网络 | 从 pypi.org 下载依赖 | ✅ 必须 |

**不需要 git**，源码可以是任何方式拷过来的。

### 3.4 产出

```
pm-offline-linux.tar.gz      约 100 MB
pm-offline-windows.zip       约 100 MB
```

内部结构：

```
package/
├── app/                    后端源码
│   ├── app/                FastAPI 应用
│   ├── alembic/            数据库迁移
│   ├── scripts/            管理脚本（含 backup.py）
│   ├── requirements.txt    依赖清单
│   └── .env.example
├── frontend/dist/          前端产物（2.1 MB）
├── wheels/                 Python 离线依赖包（约 40 MB）
└── install.sh              一键安装脚本
```

### 3.5 打包机与目标机的关系

| 项 | 要求 |
|---|---|
| Python 版本 | 打包机与目标机**都必须是 3.12** |
| 操作系统 | 可不同（脚本会按目标平台下载对应 wheel） |
| 架构 | 都需 x86_64（ARM 需改脚本中的 platform 参数） |

`pack.sh` 会为两个平台分别下载：

```bash
# Linux
--platform manylinux_2_28_x86_64

# Windows
--platform win_amd64
```

这样打出的包在对应平台上可直接安装，不依赖打包机的操作系统。

## 四、内网部署

```bash
# 1. 拷贝文件到服务器（U盘/共享盘/scp）
# 假设放到 /root/

# 2. 解压
tar xzf pm-linux-offline.tar.gz
cd package

# 3. 一键安装（需要 root）
sudo bash install.sh
```

### install.sh 做了什么

| 步骤 | 内容 |
|---|---|
| 1 | 检查 Python 3.12、PostgreSQL 18 是否就绪 |
| 2 | 创建 `pm` 用户与 `/opt/pm`、`/data/pm` 目录 |
| 3 | 部署代码到 `/opt/pm` |
| 4 | 创建虚拟环境，**离线安装**依赖（`--no-index --find-links wheels`） |
| 5 | 创建数据库 `pm_prod`、用户 `pm_app`，**自动生成随机密码** |
| 6 | 生成 `.env` 配置，执行 `alembic upgrade head` 建表 |
| 7 | 配置 systemd 服务，开机自启 |
| 8 | 启动并等待健康检查通过，创建管理员账号 |

安装完成后会打印：

```
==============================================
 部署完成
==============================================

  访问地址： http://192.168.1.100:8000
  管理员账号：admin  密码：Admin@123

  数据库密码： xxxxxxxxxxxxxxxxxxxxxxxx
  （已写入 /opt/pm/app/.env，请记录备用）
```

### 部署后的目录结构

```
/opt/pm/                        程序目录
├── app/
│   ├── .venv/                  虚拟环境（含全部依赖）
│   ├── .env                    配置文件（权限 600）
│   ├── app/                    应用代码
│   ├── alembic/                迁移脚本
│   └── scripts/                管理脚本
└── frontend/dist/              前端静态文件

/data/pm/                       数据目录（独立于程序，升级不丢）
├── upload/                     附件
├── log/                        日志
└── backup/                     数据库备份
```

**代码与数据分离**，升级时只替换 `/opt/pm`，`/data/pm` 不动。

---

## 五、离线安装 Python 与 PostgreSQL

### 5.1 Python 3.12

**方式一：源码编译**（通用，但慢，约 10 分钟）

```bash
# 联网机器下载源码包
wget https://www.python.org/ftp/python/3.12.14/Python-3.12.14.tgz

# 拷到内网后
tar xzf Python-3.12.14.tgz
cd Python-3.12.14
./configure --prefix=/usr/local --enable-optimizations
make -j$(nproc)
sudo make altinstall

# 验证
python3.12 --version
```

**方式二：用系统包**（Ubuntu 24.04 自带 3.12）

```bash
# 联网机器下载 deb 包
apt-get download python3.12 python3.12-venv python3.12-dev
# 拷到内网
sudo dpkg -i *.deb
```

### 5.2 PostgreSQL 18

**Ubuntu / Debian**

```bash
# 联网机器：下载 deb 包
apt-get install -y --download-only postgresql-18 postgresql-client-18
# 包在 /var/cache/apt/archives/

# 拷到内网
sudo dpkg -i postgresql-*.deb
sudo systemctl enable --now postgresql
```

**CentOS / RHEL**

```bash
# 联网机器
yumdownloader --resolve postgresql18-server postgresql18
# 拷到内网
sudo rpm -ivh *.rpm
sudo /usr/pgsql-18/bin/postgresql-18-setup initdb
sudo systemctl enable --now postgresql-18
```

### 5.3 验证

```bash
python3.12 --version      # 应显示 Python 3.12.x
psql --version            # 应显示 psql (PostgreSQL) 18.x
systemctl status postgresql
```

---

## 六、日常运维

### 6.1 服务管理

```bash
# 状态
systemctl status pm

# 启动 / 停止 / 重启
systemctl start pm
systemctl stop pm
systemctl restart pm

# 开机自启（安装时已设置）
systemctl enable pm
systemctl disable pm
```

### 6.2 查看日志

```bash
# 实时日志（systemd）
journalctl -u pm -f

# 应用日志文件
tail -f /data/pm/log/app.log
tail -f /data/pm/log/app.err.log

# 只看错误
journalctl -u pm -p err -n 50
```

### 6.3 数据库备份

**自动备份已内置**，每日 02:00 执行（应用内的定时任务，不是 crontab）：

```bash
# 看备份是否正常
journalctl -u pm | grep backup
ls -lh /data/pm/backup/

# 手动触发一次
sudo -u pm /opt/pm/app/.venv/bin/python -c "
from app.jobs.scheduler import job_database_backup
job_database_backup()
"

# 或直接用 pg_dump
sudo -u pm bash -c 'cd /opt/pm/app && set -a && . ./.env && set +a && \
  pg_dump "${PM_DATABASE_URL/postgresql+psycopg/postgresql}" \
  --format=custom --compress=6 -f /data/pm/backup/manual_$(date +%Y%m%d).dump'
```

**恢复**：

```bash
systemctl stop pm

sudo -u pm bash -c 'cd /opt/pm/app && set -a && . ./.env && set +a && \
  pg_restore --clean --if-exists \
  -d "${PM_DATABASE_URL/postgresql+psycopg/postgresql}" \
  /data/pm/backup/pm_20260101_020000.dump'

systemctl start pm
```

### 6.4 升级

```bash
# 1. 备份
sudo -u pm bash -c 'cd /opt/pm/app && set -a && . ./.env && set +a && \
  pg_dump "${PM_DATABASE_URL/postgresql+psycopg/postgresql}" \
  --format=custom -f /data/pm/backup/before_upgrade.dump'

# 2. 停服务
systemctl stop pm

# 3. 替换代码（保留 .env 和 .venv）
cd /opt/pm
cp app/.env /tmp/pm.env.bak
mv app /opt/pm/app.old
tar xzf pm-linux-offline-new.tar.gz
cp -r package/app /opt/pm/app
cp /tmp/pm.env.bak /opt/pm/app/.env
chown -R pm:pm /opt/pm/app

# 4. 更新依赖（如果有变化）
cd /opt/pm/app
sudo -u pm ./.venv/bin/pip install --no-index --find-links /opt/pm/wheels -r requirements.txt

# 5. 执行迁移
sudo -u pm bash -c 'cd /opt/pm/app && ./.venv/bin/alembic upgrade head'

# 6. 启动
systemctl start pm
systemctl status pm

# 7. 确认无误后清理旧版
rm -rf /opt/pm/app.old
```

### 6.5 修改配置

```bash
sudo -u pm vi /opt/pm/app/.env
systemctl restart pm
```

常用配置：

| 配置项 | 说明 |
|---|---|
| `HTTP_PORT` | 监听端口（在 systemd 文件里改，见下） |
| `PM_SECRET_KEY` | 密钥，**不要随意改**（改了所有人要重新登录） |
| `PM_SMTP_*` | 邮件服务器 |
| `PM_SCHEDULER_ENABLED` | 是否启用定时任务 |
| `PM_BACKUP_HOUR` | 备份时间 |

改端口需要改 systemd 文件：

```bash
sudo vi /etc/systemd/system/pm.service
# 找到 --port 8000，改成需要的端口
sudo systemctl daemon-reload
sudo systemctl restart pm
```

---

## 七、常见问题

### Q1: `pip install` 报 "No matching distribution"

说明 `wheels/` 里的包与服务器平台不匹配。

检查服务器架构：

```bash
uname -m
# x86_64 → 用 manylinux_x86_64 的包
# aarch64 → 需要 manylinux_aarch64 的包
```

检查 Python 版本：

```bash
python3.12 --version
# 必须与打包时的 3.12 一致
```

重新打包时改对应参数。

### Q2: 服务启动失败，日志显示数据库连接错误

```bash
# 1. 检查 PostgreSQL 是否运行
systemctl status postgresql

# 2. 手动测试连接
sudo -u pm bash -c 'cd /opt/pm/app && set -a && . ./.env && set +a && \
  psql "${PM_DATABASE_URL/postgresql+psycopg/postgresql}" -c "SELECT 1"'

# 3. 检查 .env 里的密码是否正确
sudo cat /opt/pm/app/.env | grep DATABASE_URL
```

常见原因：PostgreSQL 的 `pg_hba.conf` 不允许密码登录。

```bash
sudo vi /etc/postgresql/18/main/pg_hba.conf
# 确保有这行（md5 或 scram-sha-256）
# local   all   all                 scram-sha-256
# host    all   all   127.0.0.1/32  scram-sha-256

sudo systemctl reload postgresql
```

### Q3: 前端页面 404 或空白

```bash
# 1. 确认前端产物存在
ls /opt/pm/frontend/dist/
# 应有 index.html 和 assets/

# 2. 确认 .env 里的路径正确
grep FRONTEND /opt/pm/app/.env
# PM_FRONTEND_SERVE=true
# PM_FRONTEND_DIR=/opt/pm/frontend/dist

# 3. 确认 FastAPI 已启用静态托管
journalctl -u pm | grep "静态托管"
# 应看到「已启用前端静态托管（FastAPI 模式）」
```

### Q4: 导出 PDF 中文乱码

缺少中文字体：

```bash
# Ubuntu
sudo apt install -y fonts-wqy-zenhei

# CentOS
sudo yum install -y wqy-zenhei-fonts

# 或手动放置
sudo mkdir -p /opt/pm/app/fonts
sudo cp /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc /opt/pm/app/fonts/
sudo chown pm:pm /opt/pm/app/fonts/*
```

程序会自动查找以下路径：

```
/opt/pm/app/fonts/simhei.ttf
/opt/pm/app/fonts/simsun.ttc
/usr/share/fonts/.../NotoSansCJK-Regular.ttc
/usr/share/fonts/.../wqy-zenhei.ttc
```

### Q5: 内存占用过高

```bash
# 看进程占用
ps aux | grep uvicorn
free -h

# 减少 worker 数
sudo vi /etc/systemd/system/pm.service
# 把 --workers 2 改成 --workers 1
sudo systemctl daemon-reload && sudo systemctl restart pm
```

PostgreSQL 内存调优见 [服务器配置建议.md](服务器配置建议.md)。

### Q6: 时间不对导致预警异常

```bash
# 检查时间
date
timedatectl

# 配置 NTP
sudo timedatectl set-ntp true
# 或指定内网 NTP
sudo vi /etc/systemd/timesyncd.conf
# NTP=内网NTP服务器IP
sudo systemctl restart systemd-timesyncd
```

---

## 八、检查清单

- [ ] 服务器已装 Python 3.12（`python3.12 --version`）
- [ ] 服务器已装 PostgreSQL 18（`psql --version`）
- [ ] PostgreSQL 服务已启动（`systemctl status postgresql`）
- [ ] 已拷贝 `pm-linux-offline.tar.gz`
- [ ] 已解压并进入 `package` 目录
- [ ] `sudo bash install.sh` 执行无报错
- [ ] `systemctl status pm` 显示 active (running)
- [ ] `curl http://127.0.0.1:8000/api/health` 返回 200
- [ ] 浏览器能打开登录页
- [ ] 管理员能登录，左侧菜单完整
- [ ] 防火墙已放通端口
- [ ] 系统时间准确
- [ ] 中文字体已装（导出 PDF 正常）
- [ ] 备份目录 `/data/pm/backup` 有文件生成
