# Bootstrap HOST - Remote PowerShell Commander
# Run this with: iex (irm 'https://raw.githubusercontent.com/Knightlost/remote-shell/main/bootstrap_host.ps1')

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'   # Makes downloads MUCH faster
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Fast-Download($url, $dest) {
    try {
        (New-Object System.Net.WebClient).DownloadFile($url, $dest)
        return $true
    } catch {
        try {
            Invoke-WebRequest $url -OutFile $dest -UseBasicParsing
            return $true
        } catch {
            return $false
        }
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Remote PowerShell Commander - HOST" -ForegroundColor Cyan
Write-Host "  (This computer will be remotely controllable)" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$workDir = "$env:USERPROFILE\remote-shell-host"
if (-not (Test-Path $workDir)) {
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null
}
Set-Location $workDir

$BASE_URL = "https://raw.githubusercontent.com/Knightlost/remote-shell/main"

# Step 1: Download Python files
Write-Host "[1/4] Downloading files from GitHub..." -ForegroundColor Yellow
$hostFiles = @("remote_host.py","server.py","config.py","mcp_server.py","executor.py","node_registry.py","bridge_mcp.py")
foreach ($f in $hostFiles) {
    Write-Host "  $f..." -ForegroundColor Gray -NoNewline
    $ok = Fast-Download "$BASE_URL/$f" "$workDir\$f"
    if ($ok) { Write-Host " OK" -ForegroundColor Green } else { Write-Host " FAILED" -ForegroundColor Red }
}

# Step 2: Find / Install Python
Write-Host ""
Write-Host "[2/4] Checking Python..." -ForegroundColor Yellow

function Find-Python {
    $paths = @(
        "python","python3",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python313\python.exe","C:\Python312\python.exe",
        "C:\Python311\python.exe","C:\Python310\python.exe"
    )
    foreach ($p in $paths) {
        try {
            & $p -c "import sys; sys.exit(0)" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { return $p }
        } catch {}
    }
    return $null
}

$pyExe = Find-Python
if (-not $pyExe) {
    Write-Host "  Python not found. Installing via winget..." -ForegroundColor Yellow
    try {
        winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Start-Sleep -Seconds 5
        $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
        $pyExe = Find-Python
    } catch {}
}
if (-not $pyExe) {
    Write-Host "  Downloading Python 3.12..." -ForegroundColor Yellow
    $inst = "$env:TEMP\python_installer.exe"
    Fast-Download "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" $inst | Out-Null
    Start-Process $inst -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_pip=1" -Wait
    Remove-Item $inst -Force -ErrorAction SilentlyContinue
    $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
    $pyExe = Find-Python
}
if (-not $pyExe) {
    Write-Host "[ERROR] Cannot find Python. Install from https://python.org" -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}
Write-Host "  [OK] $pyExe" -ForegroundColor Green

# Step 3: Install packages
Write-Host ""
Write-Host "[3/4] Installing Python packages..." -ForegroundColor Yellow
foreach ($pkg in @("fastapi","uvicorn","pydantic-settings","httpx","mcp")) {
    Write-Host "  $pkg..." -ForegroundColor Gray -NoNewline
    & $pyExe -m pip install $pkg --quiet --disable-pip-version-check 2>&1 | Out-Null
    Write-Host " OK" -ForegroundColor Green
}

# Download cloudflared (skip if already exists)
Write-Host ""
$cfPath = "$workDir\cloudflared.exe"
if (Test-Path $cfPath) {
    Write-Host "  cloudflared: already exists (skip)" -ForegroundColor Green
} else {
    Write-Host "  Downloading cloudflared (~50MB)..." -ForegroundColor Gray -NoNewline
    $ok = Fast-Download "https://github.com/cloudflare/cloudflared/releases/download/2025.4.0/cloudflared-windows-amd64.exe" $cfPath
    if ($ok) { Write-Host " OK" -ForegroundColor Green }
    else { Write-Host " WARN (will use system cloudflared)" -ForegroundColor Yellow }
}

# Step 4: Run
Write-Host ""
Write-Host "[4/4] Starting HOST..." -ForegroundColor Yellow
Write-Host ""
Set-Location $workDir
& $pyExe "$workDir\remote_host.py"

Write-Host ""
Write-Host "Session ended." -ForegroundColor Gray
Read-Host "Press Enter to close"
