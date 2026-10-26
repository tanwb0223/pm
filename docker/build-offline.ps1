# ============================================================================
# 构建离线 Docker 部署包（Windows / PowerShell 版）
#
# 产出：
#   docker\dist\pm-docker-offline-<版本>.tar.gz
#   内含 app + caddy 镜像、compose 文件、deploy.sh、pmctl.sh、README
#
# 目标机只需要 Docker + 这个压缩包，不需要源码/Python/Node/外网。
#
# 用法（在仓库根目录或 docker 目录均可）：
#   powershell -ExecutionPolicy Bypass -File docker\build-offline.ps1
#   powershell -ExecutionPolicy Bypass -File docker\build-offline.ps1 -Version 20261026
#   powershell -ExecutionPolicy Bypass -File docker\build-offline.ps1 -Platform linux/arm64
# ============================================================================
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Platform = "linux/amd64",
    [string]$AppImage = "pm-app",
    [string]$CaddyImage = "caddy:2-alpine",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$DockerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $DockerDir
Set-Location $DockerDir

function Fail([string]$Message) {
    Write-Host "[X] $Message" -ForegroundColor Red
    exit 1
}

function Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Warn([string]$Message) {
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Invoke-Checked {
    param([scriptblock]$Block, [string]$What)
    & $Block
    if ($LASTEXITCODE -ne 0) { Fail "$What 失败（退出码 $LASTEXITCODE）" }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "未安装 Docker" }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker 未运行，或当前用户无权访问 Docker" }

if (-not $Version) {
    $gitVersion = $null
    try {
        $gitVersion = (& git -C $RootDir rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -ne 0) { $gitVersion = $null }
    } catch { $gitVersion = $null }
    if ($gitVersion) { $Version = $gitVersion.Trim() }
    else { $Version = Get-Date -Format "yyyyMMdd-HHmm" }
}
$Version = ($Version -replace '[^A-Za-z0-9_.-]', '-').Trim('-')
if (-not $Version) { $Version = Get-Date -Format "yyyyMMdd-HHmm" }

if (-not $OutputDir) { $OutputDir = Join-Path $DockerDir "dist" }
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

Write-Host "=============================================================="
Write-Host " 构建离线 Docker 部署包"
Write-Host "=============================================================="
Write-Host "  版本    : $Version"
Write-Host "  平台    : $Platform"
Write-Host "  应用镜像: ${AppImage}:$Version（同时打 latest）"
Write-Host "  Caddy   : $CaddyImage"
Write-Host "  输出目录: $OutputDir"
Write-Host ""

# ---------------------------------------------------------------- 1. 构建应用镜像
Ok "构建应用镜像（docker compose build；首次会下载基础镜像并编译前端，约 3-8 分钟）..."
$env:PM_VERSION = $Version
$env:PM_IMAGE_NAME = $AppImage
$env:DOCKER_DEFAULT_PLATFORM = $Platform
docker compose -f (Join-Path $DockerDir "compose.build.yml") build
if ($LASTEXITCODE -ne 0) { Fail "docker compose build 失败" }
docker tag "${AppImage}:$Version" "${AppImage}:latest"
if ($LASTEXITCODE -ne 0) { Fail "docker tag latest 失败" }

# ---------------------------------------------------------------- 2. 拉取 Caddy
Ok "拉取 Caddy 镜像..."
docker pull --platform $Platform $CaddyImage
if ($LASTEXITCODE -ne 0) { Fail "docker pull $CaddyImage 失败" }

# ---------------------------------------------------------------- 3. 导出镜像
$imagesDir = Join-Path $OutputDir "images"
New-Item -ItemType Directory -Force -Path $imagesDir | Out-Null
$imageTar = Join-Path $imagesDir "pm-images-$Version.tar"
$imageTarGz = "$imageTar.gz"

Ok "导出镜像（app + caddy）..."
docker save "${AppImage}:$Version" "${AppImage}:latest" $CaddyImage -o $imageTar
if ($LASTEXITCODE -ne 0) { Fail "docker save 失败" }

Ok "压缩镜像包（gzip，几分钟）..."
# 注意：必须把 docker save 的 tar 直接 gzip，不能 tar -czf（那会变成“tar 里套 tar”，docker load 不认）
try {
    $inStream = [System.IO.File]::OpenRead($imageTar)
    $outStream = [System.IO.File]::Create($imageTarGz)
    $gzipStream = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionMode]::Compress)
    try {
        $inStream.CopyTo($gzipStream)
    } finally {
        $gzipStream.Dispose()
        $outStream.Dispose()
        $inStream.Dispose()
    }
} catch {
    Fail "压缩镜像包失败：$($_.Exception.Message)"
}
Remove-Item $imageTar -Force

# ---------------------------------------------------------------- 4. 组装离线包
$stage = Join-Path $OutputDir "pm-docker-offline-$Version"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "images") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "certs") | Out-Null

$files = @(
    "compose.yml",
    "compose.tls.yml",
    "compose.http.yml",
    "Caddyfile.tpl",
    ".env.docker.example",
    "deploy.sh",
    "pmctl.sh",
    "lib.sh",
    "README.md"
)
foreach ($f in $files) {
    $src = Join-Path $DockerDir $f
    if (-not (Test-Path $src)) { Fail "缺少文件：$src" }
    Copy-Item $src (Join-Path $stage $f) -Force
}
Copy-Item (Join-Path $DockerDir "certs\README.md") (Join-Path $stage "certs\README.md") -Force
Copy-Item $imageTarGz (Join-Path $stage "images\pm-images-$Version.tar.gz") -Force
Set-Content -Path (Join-Path $stage "VERSION") -Value $Version -Encoding ASCII

Ok "打包部署文件..."
$bundle = Join-Path $OutputDir "pm-docker-offline-$Version.tar.gz"
tar -czf $bundle -C $OutputDir "pm-docker-offline-$Version"
if ($LASTEXITCODE -ne 0) { Fail "打包离线包失败" }

# 校验和
$hash = (Get-FileHash $bundle -Algorithm SHA256).Hash.ToLower()
"$hash  $(Split-Path $bundle -Leaf)" | Set-Content -Path "$bundle.sha256" -Encoding ASCII

$sizeMB = [math]::Round((Get-Item $bundle).Length / 1MB, 1)

Write-Host ""
Write-Host "=============================================================="
Write-Host " 完成"
Write-Host "=============================================================="
Write-Host ""
Write-Host "  离线包： $bundle  ($sizeMB MB)"
Write-Host "  校验和： $bundle.sha256"
Write-Host ""
Write-Host "  目标机部署："
Write-Host "    tar xzf $(Split-Path $bundle -Leaf)"
Write-Host "    cd pm-docker-offline-$Version"
Write-Host "    cp .env.docker.example .env"
Write-Host "    vi .env                       # 填域名、证书、数据/附件目录"
Write-Host "    sudo bash deploy.sh"
Write-Host ""
Write-Host "  目标机不需要源码、Python、Node、外网，只需要 Docker + 本离线包。"
Write-Host ""
