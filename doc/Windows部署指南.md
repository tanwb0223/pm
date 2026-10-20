# Windows 部署指南

在 Windows Server 上部署本系统的完整流程。

---

## 一、前提条件

| 软件 | 版本 | 下载地址 | 安装注意 |
|---|---|---|---|
| Python | **3.12** | python.org | **必须勾选 Add python.exe to PATH** |
| PostgreSQL | **18** | postgresql.org | 记住 postgres 密码，**勾选 Command Line Tools** |

服务器建议：Windows Server 2019+ / Windows 10+，2 核 4GB 起。

### 验证安装

打开 PowerShell：

```powershell
python --version
# 应显示 Python 3.12.x

psql --version
# 应显示 psql (PostgreSQL) 18.x
```

如果 `psql` 找不到，手动加入 PATH：

```powershell
$env:PATH += ";C:\Program Files\PostgreSQL\18\bin"
# 永久生效：
[Environment]::SetEnvironmentVariable("PATH",
    [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";C:\Program Files\PostgreSQL\18\bin",
    "Machine")
```

---

## 二、打包（在能联网的机器上）

在 **Linux 或 macOS** 上执行（需要 Python 3.12 + Node 18+）：

```bash
git clone https://github.com/tanwb0223/pm.git
cd pm

# 打包 Windows 版
bash pack.sh windows
```

产出：**`pm-offline-windows.zip`**（约 100 MB）

内部结构：

```
package/
├── app/                    后端源码
├── frontend/dist/          前端产物
├── wheels/                 Windows 平台的 Python 依赖（.whl）
└── install.ps1             一键安装脚本
```

> **注意**：`wheels/` 里的包是 `win_amd64` 平台的，与 Linux 的不通用。

### 如果只有 Windows 打包机

在 Windows 上用 Git Bash 执行同样的命令：

```bash
# 安装 Git for Windows 后，用 Git Bash
bash pack.sh windows
```

或手动打包：

```powershell
# 1. 构建前端
cd frontend
npm ci
npm run build
cd ..

# 2. 下载依赖
python -m pip download -r backend/requirements-prod.txt -d wheels `
    --only-binary=:all: --platform win_amd64 --python-version 312 --abi cp312

# 3. 组装（手动复制目录）
```

---

## 三、内网部署

### 3.1 解压

把 `pm-offline-windows.zip` 拷到服务器，解压到任意位置，例如 `C:\pm-install`。

### 3.2 以管理员身份运行

**必须用管理员权限**（要创建计划任务和防火墙规则）：

```powershell
# 开始菜单 → 右键 PowerShell → 以管理员身份运行

cd C:\pm-install\package
.\install.ps1
```

如果提示「无法加载文件，未对文件进行数字签名」，先执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

然后重新运行 `.\install.ps1`。

### 3.3 安装过程

脚本会依次完成 8 步：

| 步骤 | 内容 |
|---|---|
| 1 | 检查 Python 3.12、PostgreSQL 18、服务状态 |
| 2 | 创建 `C:\pm` 和 `D:\data\pm` 目录 |
| 3 | 复制代码 |
| 4 | 创建虚拟环境，**离线安装**依赖 |
| 5 | 创建数据库（会提示输入 postgres 密码） |
| 6 | 执行迁移建表 |
| 7 | 注册计划任务实现开机自启 |
| 8 | 启动服务、创建管理员、放通防火墙 |

安装过程中会**提示输入 postgres 超级用户密码**，用于创建数据库和专用用户。

完成后输出：

```
==============================================
 部署完成
==============================================

  访问地址： http://192.168.1.100:8000
  管理员账号：admin  密码：Admin@123

  数据库密码： xxxxxxxxxxxxxxxxxxxxxxxx
  （已写入 C:\pm\app\.env，请记录备用）
```

### 3.4 目录结构

```
C:\pm\                          程序目录
├── app\
│   ├── .venv\                  虚拟环境
│   ├── .env                    配置（含密码，注意权限）
│   ├── app\                    应用代码
│   ├── alembic\                迁移
│   └── scripts\                管理脚本（含 backup.py）
└── frontend\dist\              前端静态文件

D:\data\pm\                     数据目录
├── upload\                     附件
├── log\                        日志
└── backup\                     数据库备份
```

**如果服务器没有 D 盘**，数据会自动放到 `C:\pm\data`（脚本会提示）。

---

## 四、服务管理

系统用 **Windows 计划任务**实现开机自启（不需要额外装 NSSM）。

### 查看状态

```powershell
Get-ScheduledTask -TaskName "PM-System" | Get-ScheduledTaskInfo
```

### 启动 / 停止 / 重启

```powershell
# 启动
Start-ScheduledTask -TaskName "PM-System"

# 停止（先停任务，再停进程）
Stop-ScheduledTask -TaskName "PM-System"
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "C:\pm\*"
} | Stop-Process -Force

# 重启
Stop-ScheduledTask -TaskName "PM-System"
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "C:\pm\*"
} | Stop-Process -Force
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "PM-System"
```

### 查看日志

```powershell
# 应用日志（实时）
Get-Content "D:\data\pm\log\app.log" -Tail 50 -Wait

# 错误日志
Get-Content "D:\data\pm\log\app.err.log" -Tail 50
```

### 修改配置

```powershell
notepad C:\pm\app\.env
# 改完后重启服务
```

---

## 五、数据库备份

**自动备份已内置**，每日 02:00 执行。

### 检查是否正常

```powershell
# 看备份文件
Get-ChildItem "D:\data\pm\backup"

# 看备份日志
Get-Content "D:\data\pm\log\app.log" | Select-String "backup"
```

### 手动备份

```powershell
cd C:\pm\app
.\.venv\Scripts\python.exe scripts\backup.py
```

### 其他命令

```powershell
# 列出所有备份
.\.venv\Scripts\python.exe scripts\backup.py --list

# 校验最近一次备份
.\.venv\Scripts\python.exe scripts\backup.py --verify
```

### 恢复

```powershell
# 1. 停服务
Stop-ScheduledTask -TaskName "PM-System"
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "C:\pm\*" } | Stop-Process -Force

# 2. 恢复
$env:PGPASSWORD = "你的数据库密码"
& "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" `
    -h 127.0.0.1 -U pm_app -d pm_prod --clean --if-exists `
    "D:\data\pm\backup\pm_20260101_020000.dump"

# 3. 启动
Start-ScheduledTask -TaskName "PM-System"
```

### 备份脚本找不到 pg_dump 时

`scripts/backup.py` 会自动查找以下位置：

```
1. PATH 环境变量
2. C:\Program Files\PostgreSQL\18\bin\pg_dump.exe
3. C:\Program Files\PostgreSQL\17\bin\pg_dump.exe
4. C:\Program Files (x86)\PostgreSQL\18\bin\pg_dump.exe
```

如果都找不到，把 PostgreSQL 的 bin 目录加入 PATH。

---

## 六、日常运维

### 6.1 升级

```powershell
# 1. 备份
cd C:\pm\app
.\.venv\Scripts\python.exe scripts\backup.py

# 2. 停服务
Stop-ScheduledTask -TaskName "PM-System"
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "C:\pm\*" } | Stop-Process -Force

# 3. 备份配置
Copy-Item "C:\pm\app\.env" "C:\pm\app\.env.bak"

# 4. 替换代码（保留 .venv 和 .env）
Remove-Item "C:\pm\app\app" -Recurse -Force
Copy-Item "新版本\package\app\app" "C:\pm\app\app" -Recurse

# 5. 更新依赖
cd C:\pm\app
.\.venv\Scripts\pip.exe install --no-index --find-links "新版本\package\wheels" -r requirements.txt

# 6. 执行迁移
.\.venv\Scripts\alembic.exe upgrade head

# 7. 启动
Start-ScheduledTask -TaskName "PM-System"
```

### 6.2 端口冲突

改端口需要同时改两处：

```powershell
# 1. 改计划任务参数
$task = Get-ScheduledTask -TaskName "PM-System"
$task.Actions[0].Arguments = "app.main:app --host 0.0.0.0 --port 8080 --workers 2 --proxy-headers --forwarded-allow-ips=*"
Set-ScheduledTask -TaskName "PM-System" -Action $task.Actions[0]

# 2. 放通新端口
New-NetFirewallRule -DisplayName "PM-System-8080" -Direction Inbound `
    -Protocol TCP -LocalPort 8080 -Action Allow

# 3. 重启
Stop-ScheduledTask -TaskName "PM-System"
Start-ScheduledTask -TaskName "PM-System"
```

### 6.3 时间同步

预警时效、进度偏差都依赖系统时间，**必须准确**：

```powershell
# 检查
w32tm /query /status

# 同步
w32tm /resync

# 配置内网 NTP 服务器
w32tm /config /manualpeerlist:"内网NTP服务器IP" /syncfromflags:manual /update
Restart-Service w32time
```

### 6.4 中文字体

Windows 自带宋体、黑体，PDF 导出**不需要额外配置**。

程序会自动从 `C:/Windows/Fonts` 查找：

```
C:/Windows/Fonts/simhei.ttf   ← 优先
C:/Windows/Fonts/simsun.ttc
C:/Windows/Fonts/msyh.ttc
```

---

## 七、常见问题

### Q1: 安装脚本报「无法加载文件，未对文件进行数字签名」

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

这是 PowerShell 的执行策略限制，不影响系统安全。

### Q2: 提示「需要 Python 3.12」

可能装了多个 Python 版本。检查：

```powershell
py --list
```

如果 3.12 存在但不在 PATH：

```powershell
# 用 py 启动器指定版本
py -3.12 -m venv C:\pm\app\.venv
```

### Q3: 数据库连接失败

```powershell
# 1. 检查服务
Get-Service postgresql*

# 2. 手动测试连接
$env:PGPASSWORD = "密码"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h 127.0.0.1 -U pm_app -d pm_prod -c "SELECT 1"

# 3. 检查 pg_hba.conf
notepad "C:\Program Files\PostgreSQL\18\data\pg_hba.conf"
# 确保有：
# host    all    all    127.0.0.1/32    scram-sha-256
```

改完 `pg_hba.conf` 需重启服务：

```powershell
Restart-Service postgresql-x64-18
```

### Q4: 服务启动后无法访问

```powershell
# 1. 本机测试
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing

# 2. 检查端口监听
Get-NetTCPConnection -LocalPort 8000 -State Listen

# 3. 检查防火墙
Get-NetFirewallRule -DisplayName "PM-System-8000"

# 4. 查看错误日志
Get-Content "D:\data\pm\log\app.err.log" -Tail 30
```

### Q5: 备份报「找不到 pg_dump」

把 PostgreSQL 的 bin 目录加入系统 PATH：

```powershell
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
[Environment]::SetEnvironmentVariable("PATH",
    [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";$pgBin", "Machine")
```

然后**重新打开** PowerShell（或重启计划任务）使其生效。

### Q6: 内存占用高

减少 worker 数：

```powershell
$task = Get-ScheduledTask -TaskName "PM-System"
$task.Actions[0].Arguments = $task.Actions[0].Arguments -replace "--workers 2", "--workers 1"
Set-ScheduledTask -TaskName "PM-System" -Action $task.Actions[0]
Stop-ScheduledTask -TaskName "PM-System"
Start-ScheduledTask -TaskName "PM-System"
```

PostgreSQL 内存调优：编辑 `C:\Program Files\PostgreSQL\18\data\postgresql.conf`：

```ini
shared_buffers = 1GB          # 8GB 内存的机器设 2GB
effective_cache_size = 3GB
work_mem = 16MB
random_page_cost = 1.1        # SSD 必改
```

改完重启 PostgreSQL 服务。

---

## 八、Windows 与 Linux 的差异

| 项 | Windows | Linux |
|---|---|---|
| 服务管理 | 计划任务 | systemd |
| 默认安装路径 | `C:\pm` | `/opt/pm` |
| 默认数据路径 | `D:\data\pm`（无 D 盘则 `C:\pm\data`） | `/data/pm` |
| 路径分隔符 | 反斜杠（配置里用正斜杠） | 正斜杠 |
| 中文字体 | 系统自带 | 需装 `fonts-wqy-zenhei` |
| 备份脚本 | `backup.py`（Python，跨平台） | 同左 |
| 防火墙 | `New-NetFirewallRule` | `firewall-cmd` / `ufw` |

**代码完全一致**，只有部署脚本不同。

---

## 九、检查清单

- [ ] Python 3.12 已装，`python --version` 正常
- [ ] PostgreSQL 18 已装，`psql --version` 正常
- [ ] PostgreSQL 服务运行中
- [ ] 已解压 `pm-offline-windows.zip`
- [ ] 以**管理员**身份运行 `install.ps1`
- [ ] 安装过程无报错
- [ ] `Get-ScheduledTask -TaskName "PM-System"` 显示 Ready/Running
- [ ] `Invoke-WebRequest http://127.0.0.1:8000/api/health` 返回 200
- [ ] 浏览器能打开登录页
- [ ] 管理员能登录，菜单完整
- [ ] 防火墙已放通端口
- [ ] 系统时间准确（`w32tm /query /status`）
- [ ] PDF 导出中文正常
- [ ] 备份目录 `D:\data\pm\backup` 有文件生成
