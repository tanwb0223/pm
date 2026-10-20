# ============================================================================
# 便携版打包（Windows 打包机运行）
#
# 产出「解压即用」的 Windows 包：
#   pm-windows-portable.zip
#     ├── python/          便携 Python 运行时（从打包机复制）
#     ├── app/             应用代码
#     ├── frontend/dist/   前端
#     ├── data/            数据目录（附件/日志/备份）
#     ├── start.bat        双击启动
#     ├── stop.bat         停止
#     └── 使用说明.txt
#
# 目标机无需安装 Python、无需联网、无需命令行操作。
#
# 用法：
#   .\pack-portable.ps1
# ============================================================================

param(
    [string]$PythonHome = "",          # 指定 Python 安装目录（留空自动查找）
    [string]$OutputDir = ""            # 输出目录（留空用当前目录）
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not $OutputDir) { $OutputDir = $Root }

function Info($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[X] $m" -ForegroundColor Red }

$WorkDir = Join-Path $env:TEMP ("pm-portable-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

try {
    Write-Host "=============================================="
    Write-Host " 1/6  定位 Python 运行时"
    Write-Host "=============================================="

    # ---------------------------------------------------------- 找 Python
    if (-not $PythonHome) {
        # 优先用 PATH 里的 python
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pyCmd) {
            $PythonHome = Split-Path $pyCmd.Source -Parent
        } else {
            # 常见安装位置
            foreach ($p in @(
                "$env:LOCALAPPDATA\Programs\Python\Python312",
                "C:\Python312",
                "C:\Program Files\Python312",
                "D:\Programs\Python312"
            )) {
                if (Test-Path "$p\python.exe") { $PythonHome = $p; break }
            }
        }
    }

    if (-not $PythonHome -or -not (Test-Path "$PythonHome\python.exe")) {
        Err "找不到 Python 安装目录"
        Write-Host "  请用 -PythonHome 参数指定，例如："
        Write-Host "    .\pack-portable.ps1 -PythonHome 'D:\Programs\Python312'"
        exit 1
    }

    $pyExe = Join-Path $PythonHome "python.exe"
    $ver = & $pyExe --version 2>&1
    Info "Python: $ver  ($PythonHome)"

    if ($ver -notmatch "3\.(1[0-9]|[89])") {
        Err "Python 版本过低，需要 3.9+"
        exit 1
    }

    # ---------------------------------------------------------- 检查源码
    foreach ($d in @("backend", "frontend")) {
        if (-not (Test-Path (Join-Path $Root $d))) {
            Err "源码不完整，缺少：$d"
            exit 1
        }
    }
    Info "源码完整"

    # ---------------------------------------------------------- 创建目录
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 2/6  准备目录结构"
    Write-Host "=============================================="

    $Pkg = Join-Path $WorkDir "pm"
    New-Item -ItemType Directory -Path $Pkg -Force | Out-Null
    New-Item -ItemType Directory -Path "$Pkg\app" -Force | Out-Null
    New-Item -ItemType Directory -Path "$Pkg\frontend" -Force | Out-Null
    New-Item -ItemType Directory -Path "$Pkg\data\upload" -Force | Out-Null
    New-Item -ItemType Directory -Path "$Pkg\data\log" -Force | Out-Null
    New-Item -ItemType Directory -Path "$Pkg\data\backup" -Force | Out-Null
    Info "目录已创建"

    # ---------------------------------------------------------- 复制 Python
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 3/6  复制 Python 运行时（约 150 MB，稍慢）"
    Write-Host "=============================================="

    $pyDst = Join-Path $Pkg "python"
    Copy-Item $PythonHome $pyDst -Recurse -Force

    # 删除不需要的部分（减小体积）
    foreach ($junk in @("Lib\test", "Lib\idlelib", "Lib\tkinter", "Lib\turtledemo",
                        "Lib\ensurepip", "Lib\distutils", "Scripts\pip*.exe",
                        "Doc", "Tools", "tcl")) {
        $j = Join-Path $pyDst $junk
        if (Test-Path $j) { Remove-Item $j -Recurse -Force -ErrorAction SilentlyContinue }
    }

    $pySize = [math]::Round((Get-ChildItem $pyDst -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0)
    Info "Python 已复制（精简后 $pySize MB）"

    # ---------------------------------------------------------- 复制代码
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 4/6  复制应用代码"
    Write-Host "=============================================="

    Copy-Item (Join-Path $Root "backend\app")     "$Pkg\app\app" -Recurse
    Copy-Item (Join-Path $Root "backend\alembic") "$Pkg\app\alembic" -Recurse
    Copy-Item (Join-Path $Root "backend\alembic.ini") "$Pkg\app\"
    Copy-Item (Join-Path $Root "backend\scripts") "$Pkg\app\scripts" -Recurse
    Copy-Item (Join-Path $Root "backend\requirements-prod.txt") "$Pkg\app\requirements.txt"

    # 前端（未构建则自动构建）
    $dist = Join-Path $Root "frontend\dist"
    if (-not (Test-Path "$dist\index.html")) {
        Warn "前端未构建，正在自动构建..."
        $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
        if (-not $pnpm) {
            Err "未安装 pnpm，无法构建前端"
            Write-Host "  请先安装 Node.js 18+：https://nodejs.org/"
            Write-Host "  再安装 pnpm：npm install -g pnpm"
            exit 1
        }
        Push-Location (Join-Path $Root "frontend")
        if (-not (Test-Path "node_modules")) {
            Info "安装前端依赖..."
            & pnpm install
            if ($LASTEXITCODE -ne 0) { Err "依赖安装失败"; Pop-Location; exit 1 }
        }
        Info "构建前端..."
        & pnpm build
        if ($LASTEXITCODE -ne 0) { Err "前端构建失败"; Pop-Location; exit 1 }
        Pop-Location
        Info "前端构建完成"
    } else {
        Info "前端已构建，直接使用"
    }
    Copy-Item $dist "$Pkg\frontend\dist" -Recurse

    # 清理缓存
    Get-ChildItem $Pkg -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $Pkg -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Info "代码已复制"

    # ---------------------------------------------------------- 安装依赖
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 5/6  安装依赖到便携 Python"
    Write-Host "=============================================="

    $pkgPy = Join-Path $pyDst "python.exe"
    & $pkgPy -m pip install --quiet --no-warn-script-location `
        -r "$Pkg\app\requirements.txt" 2>&1 | Select-Object -Last 3

    if ($LASTEXITCODE -ne 0) {
        Err "依赖安装失败"
        exit 1
    }

    # 删掉 pip 缓存和测试工具
    Remove-Item "$pyDst\Lib\site-packages\pip" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$pyDst\Lib\site-packages\pip-*" -Recurse -Force -ErrorAction SilentlyContinue

    Info "依赖安装完成"

    # ---------------------------------------------------------- 生成启动脚本
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 6/6  生成启动脚本与说明"
    Write-Host "=============================================="

    # ---- start.bat
    @'
@echo off
chcp 65001 >nul
title 产教融合项目管理系统

cd /d "%~dp0"

echo ================================================
echo   产教融合项目管理系统
echo ================================================
echo.

REM 检查配置文件
if not exist "app\.env" (
    echo [首次运行] 正在生成配置文件...
    copy "app\.env.example" "app\.env" >nul 2>&1
    if not exist "app\.env" (
        echo [错误] 缺少 app\.env.example
        pause
        exit /b 1
    )
    echo.
    echo ================================================
    echo  请编辑 app\.env 填写数据库连接信息
    echo  必填项：PM_DATABASE_URL
    echo ================================================
    echo.
    notepad "app\.env"
)

REM 设置环境变量
set "PM_DATA_DIR=%~dp0data"
set "PM_UPLOAD_DIR=%~dp0data\upload"
set "PM_BACKUP_DIR=%~dp0data\backup"
set "PM_FRONTEND_SERVE=true"
set "PM_FRONTEND_DIR=%~dp0frontend\dist"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%~dp0app"

echo [1/3] 检查数据库连接...
"python\python.exe" -c "import sys; sys.path.insert(0,'app'); from app.core.config import settings; import psycopg; psycopg.connect(settings.database_url.replace('postgresql+psycopg://','postgresql://'), connect_timeout=5); print('  数据库连接正常')" 2>nul
if errorlevel 1 (
    echo.
    echo [错误] 数据库连接失败
    echo.
    echo 请检查：
    echo   1. PostgreSQL 是否已启动
    echo   2. app\.env 中的 PM_DATABASE_URL 是否正确
    echo.
    echo 当前配置：
    findstr /C:"PM_DATABASE_URL" "app\.env"
    echo.
    pause
    exit /b 1
)

echo [2/3] 执行数据库迁移...
cd app
"..\python\python.exe" -m alembic upgrade head
if errorlevel 1 (
    echo [错误] 迁移失败
    cd ..
    pause
    exit /b 1
)
cd ..

echo [3/3] 启动服务...
echo.
echo ================================================
echo   启动中，请稍候...
echo   访问地址：http://localhost:8000
echo   停止服务：按 Ctrl+C 或双击 stop.bat
echo ================================================
echo.

"python\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips=*

echo.
echo 服务已停止
pause
'@ | Set-Content "$Pkg\start.bat" -Encoding Default

    # ---- stop.bat
    @'
@echo off
chcp 65001 >nul
echo 正在停止服务...
taskkill /F /IM python.exe /T >nul 2>&1
echo 已停止
timeout /t 2 >nul
'@ | Set-Content "$Pkg\stop.bat" -Encoding Default

    # ---- 使用说明
    @'
============================================================
 产教融合项目管理系统 · 使用说明
============================================================

一、首次使用

  1. 确保服务器已安装 PostgreSQL 18（这是唯一需要预装的软件）
  2. 双击 start.bat
  3. 首次运行会自动打开配置文件，填写数据库连接：
        PM_DATABASE_URL=postgresql+psycopg://用户名:密码@127.0.0.1:5432/数据库名
  4. 保存后重新双击 start.bat

二、日常使用

  启动：双击 start.bat
  停止：双击 stop.bat 或关闭命令行窗口
  访问：浏览器打开 http://本机IP:8000

三、目录说明

  python\        便携 Python（已含全部依赖，不要删除）
  app\           程序代码
  frontend\      前端页面
  data\upload\   上传的附件
  data\log\      运行日志
  data\backup\   数据库备份（每日自动）

四、数据库备份

  系统每日 02:00 自动备份到 data\backup\
  手动备份：命令行执行
      python\python.exe app\scripts\backup.py

五、常见问题

  Q: 提示数据库连接失败
  A: 检查 PostgreSQL 服务是否启动（services.msc 中查看）
     检查 app\.env 中的密码是否正确

  Q: 端口 8000 被占用
  A: 编辑 start.bat，把 --port 8000 改成其他端口

  Q: 浏览器打不开
  A: 检查 Windows 防火墙是否放通 8000 端口
     本机测试：http://127.0.0.1:8000/api/health

六、管理员账号

  首次部署后需初始化，在 start.bat 所在目录打开命令行：
      python\python.exe app\scripts\seed_admin.py admin 你的密码

============================================================
'@ | Set-Content "$Pkg\使用说明.txt" -Encoding UTF8

    Info "启动脚本已生成"

    # ---------------------------------------------------------- 压缩
    $zipPath = Join-Path $OutputDir "pm-windows-portable.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    Write-Host ""
    Write-Host "  正在压缩（约需 1-2 分钟）..."
    Compress-Archive -Path $Pkg -DestinationPath $zipPath -CompressionLevel Optimal

    $zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)

    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 打包完成"
    Write-Host "=============================================="
    Write-Host ""
    Write-Host "  文件：pm-windows-portable.zip  ($zipSize MB)"
    Write-Host "  位置：$zipPath"
    Write-Host ""
    Write-Host "  目标机使用："
    Write-Host "    1. 解压到任意目录，例如 D:\pm"
    Write-Host "    2. 双击 start.bat"
    Write-Host "    3. 按提示填写数据库连接"
    Write-Host ""
    Write-Host "  前提：目标机已装 PostgreSQL 18（唯一要求）"
    Write-Host ""

} finally {
    if (Test-Path $WorkDir) {
        Remove-Item $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
