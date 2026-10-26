# ============================================================================
# 产教融合项目管理系统 · Linux 部署包构建（Windows 打包机版）
#
# 与 pack.sh 产出完全一致的包：标准 Python 应用部署包
#   - 源码 + 离线依赖 wheel + 前端构建产物
#   - 目标机只需预装 Python 3.13 即可离线安装
#
# 用法：
#   .\pack.ps1
#   .\pack.ps1 -PythonHome "D:\Programs\Python313"
#
# 产出：
#   dist\pm-linux-<版本>.tar.gz
#   dist\pm-linux-<版本>.tar.gz.sha256
#
# 目标机部署：
#   tar xzf pm-linux-<版本>.tar.gz
#   cd pm && sudo bash deploy.sh
#
# 打包机需要：Python 3.13、pnpm（前端未构建时）、网络
# ============================================================================

param(
    [string]$PythonHome = ""
)

$ErrorActionPreference = "Stop"

$Root    = $PSScriptRoot
$Version = (Get-Date).ToString("yyyyMMdd")
$WorkDir = Join-Path $env:TEMP ("pm-pack-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$OutDir  = Join-Path $Root "dist"

function Info($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "[X] $m"  -ForegroundColor Red }
function Step($m) {
    Write-Host ""
    Write-Host "=============================================================="
    Write-Host " $m"
    Write-Host "=============================================================="
}

function Test-Py313 {
    param([string]$Exe)
    if (-not $Exe -or -not (Test-Path $Exe)) { return $false }
    try {
        $v = & $Exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($v -eq "3.13")
    } catch { return $false }
}

function Resolve-Python {
    param([string]$Hint)

    # 1) 显式指定优先
    if ($Hint) {
        $h = Join-Path $Hint "python.exe"
        if (Test-Py313 $h) { return $h }
        if (Test-Py313 $Hint) { return $Hint }
    }

    # 2) py 启动器：枚举已注册的 3.13
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $listed = @()
        try { $listed = & py -0p 2>$null } catch { }
        foreach ($line in $listed) {
            if ($line -match '(3\.13)') {
                $m = [regex]::Match($line, '([A-Za-z]:\\.*python\.exe)')
                if ($m.Success -and (Test-Py313 $m.Groups[1].Value)) { return $m.Groups[1].Value }
            }
        }
    }

    # 3) PATH 上的候选
    foreach ($name in @("python3.13", "python")) {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c -and (Test-Py313 $c.Source)) { return $c.Source }
    }

    # 4) 常见安装目录
    $patterns = @(
        "$env:APPDATA\uv\python\cpython-3.13*\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\uv\python\cpython-3.13*\python.exe",
        "C:\Python313\python.exe",
        "C:\Program Files\Python313\python.exe",
        "D:\Programs\Python313\python.exe"
    )
    foreach ($pat in $patterns) {
        foreach ($f in @(Get-Item $pat -ErrorAction SilentlyContinue)) {
            if (Test-Py313 $f.FullName) { return $f.FullName }
        }
    }

    return $null
}

try {
    # ------------------------------------------------------------ 1. 环境
    Step "1/6  检查构建环境"

    $python = Resolve-Python -Hint $PythonHome
    if (-not $python) {
        Err "需要 Python 3.13"
        Write-Host "  下载：https://www.python.org/downloads/release/python-3130/"
        Write-Host "  或用 -PythonHome 指定已有 3.13 目录"
        exit 1
    }
    Info "Python: $(& $python --version)  ($python)"

    foreach ($p in @("backend\app", "backend\alembic", "backend\scripts",
                     "backend\requirements-prod.txt", "deploy-linux.sh")) {
        if (-not (Test-Path (Join-Path $Root $p))) { Err "源码不完整，缺少：$p"; exit 1 }
    }
    Info "源码完整"

    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    Info "版本号：$Version"

    # ------------------------------------------------------------ 2. 前端
    Step "2/6  构建前端"

    $distIndex = Join-Path $Root "frontend\dist\index.html"
    $buildFe = $true
    if (Test-Path $distIndex) {
        $distTime = (Get-Item $distIndex).LastWriteTime
        $newer = Get-ChildItem (Join-Path $Root "frontend\src") -Recurse -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.LastWriteTime -gt $distTime } | Select-Object -First 1
        if ($newer) { Info "源码有更新，重新构建" }
        else { Info "dist 已是最新，跳过"; $buildFe = $false }
    }

    if ($buildFe) {
        if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
            Err "未安装 pnpm：npm install -g pnpm"; exit 1
        }
        Push-Location (Join-Path $Root "frontend")
        try {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { Warn "frozen-lockfile 失败，改用普通 install"; & pnpm install }
            & pnpm build
            if ($LASTEXITCODE -ne 0) { Err "前端构建失败"; exit 1 }
        } finally { Pop-Location }
        Info "前端构建完成"
    }
    if (-not (Test-Path $distIndex)) { Err "frontend\dist\index.html 不存在"; exit 1 }

    # ------------------------------------------------------------ 3. 组装
    Step "3/6  组装部署目录"

    $pkg = Join-Path $WorkDir "pm"
    New-Item -ItemType Directory -Path $pkg -Force | Out-Null

    # 包结构对齐生产布局：解压后即 backend/ + frontend/dist/
    $beDst = Join-Path $pkg "backend"
    $feDst = Join-Path $pkg "frontend\dist"
    New-Item -ItemType Directory -Path $beDst -Force | Out-Null
    New-Item -ItemType Directory -Path $feDst -Force | Out-Null

    Copy-Item (Join-Path $Root "backend\app")                   (Join-Path $beDst "app")           -Recurse
    Copy-Item (Join-Path $Root "backend\alembic")               (Join-Path $beDst "alembic")       -Recurse
    Copy-Item (Join-Path $Root "backend\scripts")               (Join-Path $beDst "scripts")       -Recurse
    Copy-Item (Join-Path $Root "backend\fonts")                 (Join-Path $beDst "fonts")         -Recurse
    Copy-Item (Join-Path $Root "backend\alembic.ini")           (Join-Path $beDst "alembic.ini")
    Copy-Item (Join-Path $Root "backend\requirements-prod.txt")  (Join-Path $beDst "requirements.txt")
    Copy-Item (Join-Path $Root "backend\.env.example")          (Join-Path $beDst ".env.example")
    Copy-Item (Join-Path $Root "frontend\dist\*") $feDst -Recurse -Force
    Copy-Item (Join-Path $Root "deploy-linux.sh") (Join-Path $pkg "deploy.sh")

    Get-ChildItem $pkg -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $pkg -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem $pkg -Recurse -Filter "*.pyo" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Info "代码与前端产物已就位"

    # ------------------------------------------------------------ 4. 依赖
    Step "4/6  下载离线依赖 wheel"

    $wheels = Join-Path $pkg "wheels"
    New-Item -ItemType Directory -Path $wheels -Force | Out-Null

    # pip 会把提示信息写到 stderr，PowerShell 5.1 下会被当成 NativeCommandError，
    # 因此临时把 stderr 重定向到空，只看退出码判断成败。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $python -m pip download `
            -r (Join-Path $pkg "backend\requirements.txt") `
            -d $wheels `
            --only-binary=:all: `
            --platform manylinux2014_x86_64 `
            --platform manylinux_2_28_x86_64 `
            --platform manylinux_2_17_x86_64 `
            --python-version 313 `
            --implementation cp `
            --abi cp313 `
            --quiet 2>&1 | Out-Null
        $pipExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($pipExit -eq 0) {
        Info "已下载 $((Get-ChildItem $wheels -Filter '*.whl').Count) 个 wheel（Linux x86_64）"
    } else {
        Warn "跨平台下载失败，改用打包机本机平台"
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $python -m pip download -r (Join-Path $pkg "backend\requirements.txt") -d $wheels --quiet 2>&1 | Out-Null
            $pipExit2 = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEap
        }
        if ($pipExit2 -ne 0) { Err "wheel 下载失败"; exit 1 }
    }

    # ------------------------------------------------------------ 5. 说明
    Step "5/6  生成部署说明"

    $readmeLines = @(
        '# 部署说明',
        '',
        '本包为标准 Python 应用部署包，**目标机需要预装 Python 3.13**。',
        '',
        '## 安装',
        '',
        '```bash',
        'tar xzf pm-linux-<版本>.tar.gz',
        'cd pm',
        'sudo bash deploy.sh',
        '```',
        '',
        '`deploy.sh` 会依次完成：创建 `pm` 用户与目录 → 建 venv → 离线装依赖 →',
        '建库 → 写 `.env` → Alembic 迁移 → 配置 systemd → 健康检查。',
        '',
        '## 包内容',
        '',
        '| 路径 | 说明 |',
        '|---|---|',
        '| `backend/app/` | 后端源码（FastAPI） |',
        '| `backend/alembic/` | 数据库迁移 |',
        '| `backend/scripts/` | 运维脚本（建管理员、备份、清理） |',
        '| `backend/fonts/` | PDF 导出用中文字体（需自行放入 ttf/ttc） |',
        '| `frontend/dist/` | 前端构建产物 |',
        '| `wheels/` | 离线依赖 wheel（Linux x86_64） |',
        '| `deploy.sh` | 一键部署脚本 |',
        '| `.env.example` | 环境变量样例 |',
        '',
        '## 常用命令',
        '',
        '```bash',
        'systemctl status pm          # 状态',
        'journalctl -u pm -f          # 日志',
        'systemctl restart pm         # 重启',
        '```',
        ''
    )
    $readme = ($readmeLines -join "`n")
    [System.IO.File]::WriteAllText((Join-Path $pkg "README.md"), $readme, (New-Object System.Text.UTF8Encoding($false)))
    Info "部署说明已生成"

    # ------------------------------------------------------------ 6. 压缩
    Step "6/6  生成部署包"

    $tarName = "pm-linux-$Version.tar.gz"
    $tarPath = Join-Path $OutDir $tarName
    Remove-Item $tarPath -Force -ErrorAction SilentlyContinue
    Remove-Item "$tarPath.sha256" -Force -ErrorAction SilentlyContinue

    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        Err "未找到 tar 命令（Windows 10 1803+ 自带）"; exit 1
    }
    Push-Location $WorkDir
    try {
        & tar czf $tarPath pm
        if ($LASTEXITCODE -ne 0) { Err "压缩失败"; exit 1 }
    } finally { Pop-Location }

    $sizeMB = [math]::Round((Get-Item $tarPath).Length / 1MB, 1)
    $hash   = (Get-FileHash $tarPath -Algorithm SHA256).Hash.ToLower()
    "$hash  $tarName" | Out-File -FilePath "$tarPath.sha256" -Encoding ascii -NoNewline

    Write-Host ""
    Write-Host "=============================================================="
    Write-Host " 打包完成"
    Write-Host "=============================================================="
    Write-Host "  文件： $tarPath"
    Write-Host "  大小： $sizeMB MB"
    Write-Host "  SHA256： $hash"
    Write-Host ""
    Write-Host "  目标机部署（需预装 Python 3.13）："
    Write-Host "    tar xzf $tarName"
    Write-Host "    cd pm && sudo bash deploy.sh"
    Write-Host ""

} finally {
    if (Test-Path $WorkDir) {
        Remove-Item $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}