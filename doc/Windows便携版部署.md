# Windows 便携版部署（解压即用）

**目标机什么都不用装，解压双击就能用。**

---

## 一、方案说明

### 打包内容

```
pm/
├── python/           便携 Python 运行时（114 MB，含全部依赖）
├── app/              应用代码
├── frontend/dist/    前端页面
├── data/             数据目录
│   ├── pm.db         SQLite 数据库（自动创建）
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
| **任何软件** | **都不需要** |
| Python | 已内置 |
| 数据库 | SQLite 已内置（零配置） |
| Node.js | 不需要 |
| 联网 | 不需要 |
| 命令行操作 | 不需要 |

**真正的解压即用**：解压 → 双击 `start.bat` → 浏览器打开

> 如需使用 PostgreSQL，可在 `app/.env` 配置 `PM_DATABASE_URL`，
> 详见 [数据库双支持说明](数据库双支持说明.md)。

---

## 二、一键打包（在能联网的机器上）

### 2.1 准备源码

**不需要 git**。把整个 `pm` 目录拷到打包机即可。

### 2.2 双击打包

**直接用文件管理器双击 `一键打包.bat`**，或在命令行：

```powershell
cd pm
.\一键打包.bat
```

### 2.3 打包机需要什么

| 依赖 | 用途 | 必需 |
|---|---|---|
| Python 3.9+ | 构建便携运行时 | 必须 |
| Node.js 18+ 与 pnpm | 构建前端（`frontend/dist` 已存在则跳过） | 视情况 |
| 网络 | 下载 Python 依赖 | 必须 |

**不需要 git**，源码可以是任何方式拷过来的。

### 2.4 产出

```
pm-windows-portable.zip   约 40 MB
```

### 2.5 打包脚本做了什么

| 步骤 | 内容 |
|---|---|
| 1 | 定位 Python 安装目录，检查版本 >= 3.9 |
| 2 | 创建 `app` / `frontend` / `data` 目录 |
| 3 | 复制 Python 运行时，删除 test/tkinter/idlelib 等（150MB -> 74MB） |
| 4 | 复制应用代码与前端产物，**前端未构建则自动用 pnpm 构建** |
| 5 | 用便携 Python 安装依赖，装完删除 pip |
| 6 | 生成 start.bat / stop.bat / 使用说明，压缩 |

---

## 三、部署（目标服务器）

### 3.1 解压部署包

```
把 pm-windows-portable.zip 拷到服务器
解压到任意目录，例如 D:\pm
```

### 3.2 首次启动

**双击 `start.bat`**，会自动完成：

1. 创建 `data\pm.db`（SQLite 数据库）
2. 建 23 张表
3. 灌入基础数据（7 角色 / 38 权限 / 33 参数 / 9 认证方式）
4. 启动服务

控制台显示：

```
[1/3] 检查配置...
  首次运行，使用默认配置（SQLite 零依赖）

[2/3] 初始化数据库...

[3/3] 启动服务...

================================================
  启动中，请稍候...
  访问地址：http://localhost:8000
  停止服务：按 Ctrl+C 或双击 stop.bat
  数据目录：D:\pm\data
  数据库：  D:\pm\data\pm.db
================================================
```

### 3.3 初始化管理员

**服务启动过一次之后**，在 `D:\pm` 目录打开命令行
（Shift + 右键 → 在此处打开 PowerShell）：

```powershell
.\python\python.exe app\scripts\seed_admin.py admin '你的强密码'
```

输出：

```
已创建用户：admin
已授予系统管理员角色
完成。登录：admin / Admin@123
```

> 脚本会自动初始化数据库（幂等），即使忘了先启动服务也能直接用。

### 3.4 访问

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

## 五、数据备份

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

### 恢复（SQLite）

直接复制备份文件覆盖即可：

```powershell
# 1. 停止服务
.\stop.bat

# 2. 恢复
Copy-Item D:\pm\data\backup\pm_20260101_020000.db D:\pm\data\pm.db -Force

# 3. 启动
.\start.bat
```

> SQLite 模式下备份的就是 `.db` 文件本身，恢复最直接。
> 如果用 PostgreSQL，见 [数据库双支持说明](数据库双支持说明.md)。

---

## 六、常见问题

### Q1: 双击 start.bat 一闪而过

在目录里打开命令行手动运行，才能看到错误：

```powershell
cd D:\pm
.\start.bat
```

### Q2: 提示「端口 8000 被占用」

```powershell
# 查看谁占用了
Get-NetTCPConnection -LocalPort 8000 -State Listen

# 改端口：编辑 start.bat，把 --port 8000 改成 8080
```

### Q3: 浏览器打不开

```powershell
# 1. 本机测试
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing

# 2. 放通防火墙
New-NetFirewallRule -DisplayName "PM-System-8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

### Q4: 中文乱码

`start.bat` 已设置 `chcp 65001`（UTF-8）。如果日志文件仍乱码：

```powershell
Get-Content D:\pm\data\log\app.log -Encoding UTF8 -Tail 50
```

### Q5: 想改用 PostgreSQL

编辑 `D:\pm\app\.env`：

```ini
PM_DATABASE_URL=postgresql+psycopg://用户名:密码@127.0.0.1:5432/pm_prod
```

然后执行迁移（便携包里已内置 alembic）：

```powershell
cd D:\pm\app
..\python\python.exe -m alembic upgrade head
```

### Q6: 想升级到新版本

```powershell
# 1. 备份数据
Copy-Item D:\pm\data D:\data-backup -Recurse

# 2. 用新包替换（保留 data 目录）
Copy-Item 新包\app\app D:\pm\app\app -Recurse -Force
Copy-Item 新包\app\alembic D:\pm\app\alembic -Recurse -Force
Copy-Item 新包\frontend\dist D:\pm\frontend\dist -Recurse -Force

# 3. 启动（会自动执行迁移）
.\start.bat
```

> `data` 目录和 `app\.env` 不要覆盖。

### Q7: 磁盘空间够不够

便携包 40 MB，解压后约 120 MB。运行后数据增长：

| 规模 | 3 年数据量 |
|---|---|
| 400 教师 / 120 子项目 | 约 100 MB |
| 附件（每子项目 10 份） | 约 10 GB |

建议预留 **20 GB** 以上。

---

## 七、与其他方案对比

| 方案 | 目标机要求 | 包体积 | 部署难度 |
|---|---|---|---|
| **便携版**（本方案） | **什么都不用装** | 40 MB | 双击即用 |
| wheels 方案 | Python 3.12 | 100 MB | 需执行脚本 |
| Docker 方案 | Docker | 280 MB | 需会 Docker |

**便携版最省事**，适合：
- 服务器不能联网
- 运维不熟悉命令行
- 不想在服务器上装任何软件

---

## 八、检查清单

- [ ] 打包机有 Python 3.9+
- [ ] `.\一键打包.bat` 执行成功
- [ ] 产出 `pm-windows-portable.zip`（约 40 MB）
- [ ] 目标机已解压到目标目录
- [ ] 双击 `start.bat` 能启动
- [ ] `data\pm.db` 已自动创建
- [ ] 浏览器能打开 `http://IP:8000`
- [ ] 管理员账号已初始化
- [ ] 防火墙已放通 8000
- [ ] 已配置开机自启
- [ ] 备份目录有文件生成
- [ ] 磁盘预留 20 GB 以上
