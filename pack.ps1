# ============================================================================
# 离线打包脚本（Windows PowerShell 版）
#
# 用法：
#   .\pack.ps1                    # 同时打包 Windows 和 Linux
#   .\pack.ps1 -Target windows    # 只打 Windows
#   .\pack.ps1 -Target linux      # 只打 Linux
#
# 产出：
#   pm-offline-windows.zip      Windows 部署包
#   pm-offline-linux.tar.gz     Linux 部署包
#
# 不需要 git，源码目录用 U 盘或共享盘拷过来即可。
# 打包机需要：Python 3.12、Node.js 18+（前端未构建时）、网络
# ============================================================================

param(
    [ValidateSet("both", "windows", "linux")]
    [string]$Target = "both"
)

$ErrorActionPreference = "Stop"

$WorkDir = Join-Path $env:TEMP ("pm-pack-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$Root = $PSScriptRoot

function Info($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[X] $m" -ForegroundColor Red }

try {
    Write-Host "=============================================="
    Write-Host " 1/5  检查环境"
    Write-Host "=============================================="

    # ---------------------------------------------------------- Python 3.12
    $python = $null
    foreach ($cand in @("python3.12", "python", "py")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $ver = & $cand --version 2>&1
            if ($ver -match "3\.12") { $python = $cand; break }
        } catch { }
    }
    if (-not $python) {
        Err "需要 Python 3.12"
        Write-Host "  下载：https://www.python.org/downloads/release/python-31214/"
        exit 1
    }
    Info "Python: $(& $python --version)"

    # ---------------------------------------------------------- 源码完整性
    foreach ($d in @("backend", "frontend", "deploy-windows.ps1", "deploy-linux.sh")) {
        if (-not (Test-Path (Join-Path $Root $d))) {
            Err "源码不完整，缺少：$d"
            Write-Host "  请确保整个 pm 目录都拷贝过来了"
            exit 1
        }
    }
    Info "源码完整"

    # ---------------------------------------------------------- 前端构建
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 2/5  构建前端"
    Write-Host "=============================================="

    $distIndex = Join-Path $Root "frontend\dist\index.html"
    if (Test-Path $distIndex) {
        Info "已有构建产物，跳过"
    } else {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        if (-not $npm) {
            Err "未安装 Node.js / npm，且 frontend\dist 不存在"
            Write-Host "  请安装 Node.js 18+：https://nodejs.org/"
            exit 1
        }
        Push-Location (Join-Path $Root "frontend")
        & npm ci --no-audit --no-fund
        & npm run build
        Pop-Location
        Info "前端构建完成"
    }

    # ---------------------------------------------------------- 下载依赖
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 3/5  下载 Python 依赖"
    Write-Host "=============================================="

    $reqFile = Join-Path $Root "backend\requirements-prod.txt"

    function Get-Wheels {
        param([string]$DestDir, [string]$Platform)
        New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
        & $python -m pip download `
            -r $reqFile `
            -d $DestDir `
            --only-binary=:all: `
            --platform $Platform `
            --python-version 312 `
            --implementation cp `
            --abi cp312 `
            --quiet 2>$null
        return ($LASTEXITCODE -eq 0)
    }

    function Build-Package {
        param([string]$PkgDir)
        New-Item -ItemType Directory -Path (Join-Path $PkgDir "app") -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $PkgDir "frontend") -Force | Out-Null

        Copy-Item (Join-Path $Root "backend\app")     (Join-Path $PkgDir "app\app") -Recurse
        Copy-Item (Join-Path $Root "backend\alembic") (Join-Path $PkgDir "app\alembic") -Recurse
        Copy-Item (Join-Path $Root "backend\alembic.ini") (Join-Path $PkgDir "app\")
        Copy-Item (Join-Path $Root "backend\scripts") (Join-Path $PkgDir "app\scripts") -Recurse
        Copy-Item $reqFile (Join-Path $PkgDir "app\requirements.txt")
        Copy-Item (Join-Path $Root "backend\.env.example") (Join-Path $PkgDir "app\")
        Copy-Item (Join-Path $Root "frontend\dist") (Join-Path $PkgDir "frontend\dist") -Recurse

        # 清理缓存
        Get-ChildItem -Path $PkgDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path $PkgDir -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }

    $pkgWin = $null
    $pkgLinux = $null

    if ($Target -eq "both" -or $Target -eq "windows") {
        Write-Host "  打包 Windows（win_amd64）..."
        $pkgWin = Join-Path $WorkDir "windows"
        Build-Package $pkgWin

        if (Get-Wheels -DestDir (Join-Path $pkgWin "wheels") -Platform "win_amd64") {
            $cnt = (Get-ChildItem (Join-Path $pkgWin "wheels")).Count
            Info "Windows 依赖：$cnt 个包"
        } else {
            Warn "Windows 依赖下载失败（目标机需联网）"
        }

        Copy-Item (Join-Path $Root "deploy-windows.ps1") (Join-Path $pkgWin "install.ps1")
    }

    if ($Target -eq "both" -or $Target -eq "linux") {
        Write-Host "  打包 Linux（manylinux_2_28_x86_64）..."
        $pkgLinux = Join-Path $WorkDir "linux"
        Build-Package $pkgLinux

        if (Get-Wheels -DestDir (Join-Path $pkgLinux "wheels") -Platform "manylinux_2_28_x86_64") {
            $cnt = (Get-ChildItem (Join-Path $pkgLinux "wheels")).Count
            Info "Linux 依赖：$cnt 个包"
        } else {
            Warn "跨平台下载失败，改用本机平台"
            & $python -m pip download -r $reqFile -d (Join-Path $pkgLinux "wheels") --quiet
        }

        Copy-Item (Join-Path $Root "deploy-linux.sh") (Join-Path $pkgLinux "install.sh")
    }

    # ---------------------------------------------------------- 压缩
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 4/5  打包压缩"
    Write-Host "=============================================="

    if ($pkgWin) {
        $zipPath = Join-Path $Root "pm-offline-windows.zip"
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        Compress-Archive -Path $pkgWin -DestinationPath $zipPath -CompressionLevel Optimal
        $sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
        Info "pm-offline-windows.zip  ${sizeMB} MB"
    }

    if ($pkgLinux) {
        # Windows 下用 tar（Win10 1803+ 自带）
        $tarPath = Join-Path $Root "pm-offline-linux.tar.gz"
        if (Test-Path $tarPath) { Remove-Item $tarPath -Force }

        $tarCmd = Get-Command tar -ErrorAction SilentlyContinue
        if ($tarCmd) {
            Push-Location $WorkDir
            & tar czf $tarPath linux
            Pop-Location
            $sizeMB = [math]::Round((Get-Item $tarPath).Length / 1MB, 1)
            Info "pm-offline-linux.tar.gz  ${sizeMB} MB"
        } else {
            # 没有 tar 就打成 zip，Linux 用 unzip 解压
            $zipLinux = Join-Path $Root "pm-offline-linux.zip"
            if (Test-Path $zipLinux) { Remove-Item $zipLinux -Force }
            Compress-Archive -Path $pkgLinux -DestinationPath $zipLinux -CompressionLevel Optimal
            $sizeMB = [math]::Round((Get-Item $zipLinux).Length / 1MB, 1)
            Warn "未找到 tar，改用 zip 格式"
            Info "pm-offline-linux.zip  ${sizeMB} MB"
        }
    }

    # ---------------------------------------------------------- 完成
    Write-Host ""
    Write-Host "=============================================="
    Write-Host " 5/5  完成"
    Write-Host "=============================================="
    Write-Host ""
    Write-Host "  产出文件在：$Root"
    Write-Host ""

    if ($pkgWin) {
        Write-Host "  Windows 部署（管理员 PowerShell）："
        Write-Host "    1. 解压 pm-offline-windows.zip"
        Write-Host "    2. cd package"
        Write-Host "    3. .\install.ps1"
        Write-Host ""
    }
    if ($pkgLinux) {
        Write-Host "  Linux 部署："
        Write-Host "    1. tar xzf pm-offline-linux.tar.gz"
        Write-Host "    2. cd package && sudo bash install.sh"
        Write-Host ""
    }
    Write-Host "  前提：目标机已装 Python 3.12 + PostgreSQL 18"
    Write-Host ""

} finally {
    if (Test-Path $WorkDir) {
        Remove-Item $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
