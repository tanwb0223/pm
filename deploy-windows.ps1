#!/usr/bin/env pwsh
# ============================================================================
# Windows 部署脚本（PowerShell）
#
# 用法（管理员权限）：
#   .\deploy-windows.ps1
#
# 前提：
#   - 已安装 Python 3.12（勾选 Add to PATH）
#   - 已安装 PostgreSQL 18（记住 postgres 密码，勾选 Command Line Tools）
# ============================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------- 配置区
$InstallDir = "C:\pm"
$DataDir    = "D:\data\pm"
$HttpPort   = 8000
$Workers    = 2

function Info($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "[X] $msg" -ForegroundColor Red }

Write-Host "=============================================="
Write-Host " 1/8  检查环境"
Write-Host "=============================================="

# Python 3.12
$python = $null
foreach ($cmd in @("python3.12", "python", "py")) {
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($c) {
        $ver = & $cmd --version 2>&1
        if ($ver -match "3\.12") { $python = $cmd; break }
    }
}
# py 启动器特殊处理
if (-not $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
    $ver = & py -3.12 --version 2>&1
    if ($ver -match "3\.12") { $python = "py -3.12" }
}

if (-not $python) {
    Err "需要 Python 3.12"
    Write-Host "  下载：https://www.python.org/downloads/release/python-31214/"
    Write-Host "  安装时务必勾选 Add python.exe to PATH"
    exit 1
}
Info "Python: $(& $python --version)"

# PostgreSQL
$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) {
    # 尝试常见路径
    $candidates = @(
        "C:\Program Files\PostgreSQL\18\bin\psql.exe",
        "C:\Program Files\PostgreSQL\17\bin\psql.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $psql = Get-Item $c; break }
    }
}
if (-not $psql) {
    Err "未找到 PostgreSQL 客户端"
    Write-Host "  请安装 PostgreSQL 18：https://www.postgresql.org/download/windows/"
    Write-Host "  安装时勾选 Command Line Tools"
    exit 1
}
Info "PostgreSQL: $(& $psql.Source --version)"

# 检查 PostgreSQL 服务
$svc = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $svc) {
    Err "PostgreSQL 服务未找到"
    exit 1
}
if ($svc.Status -ne "Running") {
    Warn "PostgreSQL 服务未运行，尝试启动..."
    Start-Service $svc.Name
    Start-Sleep -Seconds 3
}
Info "PostgreSQL 服务运行中（$($svc.Name)）"

Write-Host ""
Write-Host "=============================================="
Write-Host " 2/8  创建目录"
Write-Host "=============================================="

foreach ($d in @($InstallDir, "$DataDir\upload", "$DataDir\log", "$DataDir\backup")) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}
Info "目录已就绪：$InstallDir / $DataDir"

Write-Host ""
Write-Host "=============================================="
Write-Host " 3/8  部署代码"
Write-Host "=============================================="

# 当前脚本所在目录视为发布包根目录
$pkgRoot = $PSScriptRoot

foreach ($item in @("app", "frontend")) {
    $src = Join-Path $pkgRoot $item
    $dst = Join-Path $InstallDir $item
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse -Force
        Info "已复制 $item"
    } else {
        Err "发布包缺少 $item 目录"
        exit 1
    }
}

Write-Host ""
Write-Host "=============================================="
Write-Host " 4/8  创建虚拟环境并安装依赖"
Write-Host "=============================================="

$venv = Join-Path $InstallDir "app\.venv"
if (-not (Test-Path $venv)) {
    & $python -m venv $venv
    Info "虚拟环境已创建"
}

$pip = Join-Path $venv "Scripts\pip.exe"
$pyExe = Join-Path $venv "Scripts\python.exe"

& $pip install --upgrade pip --quiet

# 离线安装
$wheels = Join-Path $pkgRoot "wheels"
$req = Join-Path $InstallDir "app\requirements.txt"

if (Test-Path $wheels) {
    & $pip install --no-index --find-links $wheels -r $req --quiet
} else {
    Warn "未找到 wheels 目录，尝试联网安装"
    & $pip install -r $req --quiet
}

Info "依赖安装完成"
& $pyExe -c "import fastapi, sqlalchemy, psycopg, alembic; print('  验证通过')"

Write-Host ""
Write-Host "=============================================="
Write-Host " 5/8  初始化数据库"
Write-Host "=============================================="

$dbName = "pm_prod"
$dbUser = "pm_app"

# 生成随机密码（纯字母数字，避免连接串解析问题）
function New-RandomString([int]$Length) {
    $chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

$dbPass = New-RandomString 24
$secret = New-RandomString 48

# 检查数据库是否存在
$env:PGPASSWORD = Read-Host "请输入 postgres 超级用户密码" -AsSecureString | ForEach-Object {
    [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($_))
}

$env:PGCLIENTENCODING = "UTF8"
$dbExists = & $psql.Source -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$dbName'" 2>&1

if ($dbExists -notmatch "1") {
    Info "创建数据库与用户..."
    $sql = @"
CREATE USER $dbUser WITH PASSWORD '$dbPass';
CREATE DATABASE $dbName OWNER $dbUser ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
GRANT ALL PRIVILEGES ON DATABASE $dbName TO $dbUser;
"@
    $sql | & $psql.Source -U postgres -d postgres
    Info "数据库创建完成"
} else {
    Warn "数据库 $dbName 已存在"
    $dbPass = Read-Host "请输入现有 pm_app 用户密码"
}

$env:PGPASSWORD = $null

# 写配置文件
$envFile = Join-Path $InstallDir "app\.env"
$uploadPath = ($DataDir + "\upload").Replace("\", "/")
$backupPath = ($DataDir + "\backup").Replace("\", "/")
$frontPath  = ($InstallDir + "\frontend\dist").Replace("\", "/")

@"
PM_DATABASE_URL=postgresql+psycopg://${dbUser}:${dbPass}@127.0.0.1:5432/$dbName
PM_APP_NAME=产教融合项目管理系统
PM_DEBUG=false
PM_SECRET_KEY=$secret
PM_ACCESS_TOKEN_EXPIRE_MINUTES=30

PM_DATA_DIR=$($DataDir.Replace('\','/'))
PM_UPLOAD_DIR=$uploadPath
PM_UPLOAD_MAX_SIZE_MB=50

PM_FRONTEND_SERVE=true
PM_FRONTEND_DIR=$frontPath

PM_SCHEDULER_ENABLED=true
PM_DAILY_SCAN_HOUR=1
PM_DIGEST_HOUR=8
PM_DIGEST_MINUTE=30

PM_BACKUP_ENABLED=true
PM_BACKUP_HOUR=2
PM_BACKUP_DIR=$backupPath
PM_BACKUP_KEEP_DAYS=30

PM_SMTP_HOST=
PM_SMTP_PORT=465
PM_SMTP_USER=
PM_SMTP_PASSWORD=
PM_SMTP_USE_SSL=true
PM_MAIL_FROM=
"@ | Set-Content -Path $envFile -Encoding UTF8

Info "配置文件已生成：$envFile"

Write-Host ""
Write-Host "=============================================="
Write-Host " 6/8  执行数据库迁移"
Write-Host "=============================================="

$alembic = Join-Path $venv "Scripts\alembic.exe"
Push-Location (Join-Path $InstallDir "app")
& $alembic upgrade head
Pop-Location
Info "迁移完成"

Write-Host ""
Write-Host "=============================================="
Write-Host " 7/8  注册 Windows 服务"
Write-Host "=============================================="

$uvicorn = Join-Path $venv "Scripts\uvicorn.exe"
$appDir = Join-Path $InstallDir "app"

# 用计划任务实现开机自启（比 NSSM 简单，无需额外下载）
$taskName = "PM-System"
$action = New-ScheduledTaskAction -Execute $uvicorn `
    -Argument "app.main:app --host 0.0.0.0 --port $HttpPort --workers $Workers --proxy-headers --forwarded-allow-ips=*" `
    -WorkingDirectory $appDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description "产教融合项目管理系统" | Out-Null

Start-ScheduledTask -TaskName $taskName
Info "计划任务已注册并启动（$taskName）"

Write-Host ""
Write-Host "=============================================="
Write-Host " 8/8  等待服务就绪"
Write-Host "=============================================="

$ready = $false
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$HttpPort/api/health" `
            -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Write-Host "." -NoNewline
}
Write-Host ""

if (-not $ready) {
    Err "服务启动超时"
    Write-Host "  查看日志：Get-Content '$DataDir\log\app.err.log' -Tail 50"
    Write-Host "  检查任务：Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
    exit 1
}
Info "服务已就绪"

# 初始化管理员
$hasUser = & $pyExe -c @"
import sys
sys.path.insert(0, r'$appDir')
from app.db.session import SessionLocal
from app.models.system import SysUser
from sqlalchemy import select
db = SessionLocal()
print('yes' if db.scalar(select(SysUser)) else 'no')
"@ 2>$null

if ($hasUser -notmatch "yes") {
    Push-Location $appDir
    & $pyExe scripts\seed_admin.py admin 'Admin@123'
    Pop-Location
    $adminMsg = "管理员账号：admin  密码：Admin@123"
} else {
    $adminMsg = "已有用户，未创建新管理员"
}

# 防火墙
$ruleName = "PM-System-$HttpPort"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound `
        -Protocol TCP -LocalPort $HttpPort -Action Allow | Out-Null
    Info "防火墙已放通 $HttpPort"
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notmatch "^127\." -and $_.IPAddress -notmatch "^169\." } |
    Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "=============================================="
Write-Host " 部署完成"
Write-Host "=============================================="
Write-Host ""
Write-Host "  访问地址： http://${ip}:${HttpPort}"
Write-Host "  $adminMsg"
Write-Host ""
Write-Host "  数据库密码： $dbPass"
Write-Host "  （已写入 $envFile，请记录备用）"
Write-Host ""
Write-Host "  常用命令："
Write-Host "    查看状态  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "    启动服务  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "    停止服务  Stop-ScheduledTask -TaskName '$taskName'"
Write-Host "    查看日志  Get-Content '$DataDir\log\app.log' -Tail 50 -Wait"
Write-Host "    手动备份  & '$pyExe' '$appDir\scripts\backup.py'"
Write-Host ""
Write-Host "  ⚠️  请登录后立即修改管理员密码"
Write-Host ""
