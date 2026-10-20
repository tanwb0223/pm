# Windows 便携版部署（解压即用）

**目标机只需装 PostgreSQL 18，其他什么都不用装。**

---

## 一、方案说明

### 打包内容

```
pm/
├── python/           便携 Python 运行时（114 MB，含全部依赖）
├── app/              应用代码
├── frontend/dist/    前端页面
├── data/             数据目录
│   ├── upload/       附件
│   ├── log/          日志
│   └── backup/       数据库备份
├── start.bat         双击启动
├── stop.bat          停止
└── 使用说明.txt
```

压缩后 **约 40 MB**。

### 目标机要求

| 项 | 要求 |
|---|---|
| PostgreSQL | **18**（唯一需要预装的软件） |
| Python | ❌ 不需要 |
| Node.js | ❌ 不需要 |
| 联网 | ❌ 不需要 |
| 命令行操作 | ❌ 不需要 |

---

## 二、打包（在能联网的机器上）

### 2.1 准备源码

**不需要 git**。把整个 `pm` 目录拷到打包机即可。

### 2.2 执行打包

```powershell
cd pm

# 自动查找 Python（需 3.9+）
.\pack-portable.ps1

# 或指定 Python 位置
.\pack-portable.ps1 -PythonHome "D:\Programs\Python312"

# 指定输出目录
.\pack-portable.ps1 -OutputDir "E:\dist"
```

**打包机需要**：
- Python 3.9+（用来构建便携运行时）
- Node.js 18+（仅当 `frontend/dist` 不存在时需要）
- 网络（下载 Python 依赖）

**耗时**：约 1.5-2 分钟。

### 2.3 产出

```
pm-windows-portable.zip   约 40 MB
```

### 2.4 打包脚本做了什么

| 步骤 | 内容 |
|---|---|
| 1 | 定位 Python 安装目录，检查版本 ≥ 3.9 |
| 2 | 创建 `app` / `frontend` / `data` 目录 |
| 3 | 复制 Python 运行时，删除 `test`/`tkinter`/`idlelib` 等（150MB → 74MB） |
| 4 | 复制应用代码与前端产物，清理 `__pycache__` |
| 5 | 用便携 Python 安装依赖，装完删除 `pip` |
| 6 | 生成 `start.bat` / `stop.bat` / 使用说明，压缩 |

---

## 三、部署（目标服务器）

### 3.1 安装 PostgreSQL

唯一需要预装的软件。

1. 下载：https://www.postgresql.org/download/windows/
2. 安装时设置 **postgres 用户密码**（记住它）
3. **勾选 Command Line Tools**（备份功能需要 `pg_dump`）
4. 安装完成后确认服务运行：

```powershell
Get-Service postgresql*
```

### 3.2 解压部署包

```
把 pm-windows-portable.zip 拷到服务器
解压到任意目录，例如 D:\pm
```

### 3.3 创建数据库

用 pgAdmin 或命令行创建：

```powershell
$env:PGPASSWORD = "你的postgres密码"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE pm_prod ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;"
```

或直接在 pgAdmin 里右键新建数据库，编码选 UTF8。

### 3.4 首次启动

**双击 `start.bat`**，会发生：

1. 自动生成 `app\.env` 配置模板
2. 自动用记事本打开，让你填数据库连接
3. 填写后保存，关闭记事本
4. 脚本继续检查数据库 → 执行迁移 → 启动服务

配置文件需要改这一行：

```ini
PM_DATABASE_URL=postgresql+psycopg://postgres:你的密码@127.0.0.1:5432/pm_prod
```

其他项可以先用默认值。

### 3.5 初始化管理员

在 `D:\pm` 目录打开命令行（Shift + 右键 → 在此处打开 PowerShell）：

```powershell
.\python\python.exe app\scripts\seed_admin.py admin '你的强密码'
```

### 3.6 访问

浏览器打开：`http://服务器IP:8000`

用 `admin` 和你设置的密码登录。

---

## 四、日常使用

### 启动 / 停止

| 操作 | 方式 |
|---|---|
| 启动 | 双击 `start.bat` |
| 停止 | 双击 `stop.bat`，或关闭命令行窗口 |
| 查看日志 | `data\log\app.log` |

### 开机自启（推荐）

创建快捷方式放到启动目录：

```powershell
# 1. 右键 start.bat → 创建快捷方式
# 2. Win+R 输入 shell:startup
# 3. 把快捷方式拖进去
```

或用计划任务（更可靠）：

```powershell
$action = New-ScheduledTaskAction -Execute "D:\pm\start.bat" -WorkingDirectory "D:\pm"
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName "PM-System" -Action $action -Trigger $trigger -RunLevel Highest
```

### 后台运行（不显示命令行窗口）

创建 `start-hidden.vbs`：

```vbscript
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "D:\pm\start.bat" & chr(34), 0
Set WshShell = Nothing
```

双击这个 `.vbs` 即可后台运行。

---

## 五、数据库备份

**自动备份已内置**，每日 02:00 执行。

### 检查备份

```powershell
Get-ChildItem D:\pm\data\backup
```

### 手动备份

```powershell
cd D:\pm
.\python\python.exe app\scripts\backup.py
```

### 恢复

```powershell
# 1. 停止服务
.\stop.bat

# 2. 恢复
$env:PGPASSWORD = "你的密码"
& "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" `
    -h 127.0.0.1 -U postgres -d pm_prod --clean --if-exists `
    "D:\pm\data\backup\pm_20260101_020000.dump"

# 3. 启动
.\start.bat
```

> 如果备份报「找不到 pg_dump」，说明装 PostgreSQL 时没勾选 Command Line Tools，
> 把 `C:\Program Files\PostgreSQL\18\bin` 加入系统 PATH 即可。

---

## 六、常见问题

### Q1: 双击 start.bat 一闪而过

在目录里打开命令行手动运行，才能看到错误：

```powershell
cd D:\pm
.\start.bat
```

常见原因：
- 数据库没启动
- `app\.env` 里的密码不对
- 端口 8000 被占用

### Q2: 提示「数据库连接失败」

```powershell
# 1. 检查服务
Get-Service postgresql*

# 2. 手动测试
$env:PGPASSWORD = "密码"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1 -d pm_prod -c "SELECT 1"
```

### Q3: 端口 8000 被占用

```powershell
# 查看谁占用了
Get-NetTCPConnection -LocalPort 8000 -State Listen

# 改端口：编辑 start.bat，把 --port 8000 改成 8080
```

### Q4: 浏览器打不开

```powershell
# 1. 本机测试
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing

# 2. 放通防火墙
New-NetFirewallRule -DisplayName "PM-System-8000" -Direction Inbound `
    -Protocol TCP -LocalPort 8000 -Action Allow
```

### Q5: 中文乱码

`start.bat` 已设置 `chcp 65001`（UTF-8）。如果日志文件仍乱码：

```powershell
# 用 UTF-8 读取
Get-Content D:\pm\data\log\app.log -Encoding UTF8 -Tail 50
```

### Q6: 想升级到新版本

```powershell
# 1. 备份配置和数据
Copy-Item D:\pm\app\.env D:\pm\.env.bak
Copy-Item D:\pm\data D:\data-backup -Recurse

# 2. 用新包替换（保留 data 和 .env）
#    解压新包到临时目录，然后：
Copy-Item 新包\app\app D:\pm\app\app -Recurse -Force
Copy-Item 新包\app\alembic D:\pm\app\alembic -Recurse -Force
Copy-Item 新包\frontend\dist D:\pm\frontend\dist -Recurse -Force

# 3. 恢复配置
Copy-Item D:\pm\.env.bak D:\pm\app\.env

# 4. 启动（会自动执行迁移）
.\start.bat
```

---

## 七、与其他方案对比

| 方案 | 目标机要求 | 包体积 | 部署难度 |
|---|---|---|---|
| **便携版**（本方案） | 只需 PostgreSQL | 40 MB | ⭐ 双击即用 |
| wheels 方案 | Python 3.12 + PostgreSQL | 100 MB | ⭐⭐ 需执行脚本 |
| Docker 方案 | 只需 Docker | 280 MB | ⭐⭐ 需会 Docker |

**便携版是最省事的**，适合：
- 服务器不能联网
- 运维不熟悉命令行
- 不想在服务器上装 Python

---

## 八、检查清单

- [ ] 打包机有 Python 3.9+
- [ ] `frontend/dist` 已构建
- [ ] `.\pack-portable.ps1` 执行成功
- [ ] 产出 `pm-windows-portable.zip`（约 40 MB）
- [ ] 目标机已装 PostgreSQL 18
- [ ] 已创建 `pm_prod` 数据库（UTF8 编码）
- [ ] 已解压到目标目录
- [ ] `start.bat` 首次运行能打开配置
- [ ] 填写 `PM_DATABASE_URL` 后能启动
- [ ] 浏览器能打开 `http://IP:8000`
- [ ] 管理员账号已初始化
- [ ] 防火墙已放通 8000
- [ ] 已配置开机自启
- [ ] 备份目录有文件生成
