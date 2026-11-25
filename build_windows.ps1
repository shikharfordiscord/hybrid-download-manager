# build_windows.ps1 — Windows Packaging Script for hydownload managers
# ========================================================================
# Equivalent of bundle_app.sh but for Windows 10/11.
# Stages aria2c.exe and 7z.exe, then uses PyInstaller to build a
# standalone one-directory distribution in dist/
#
# Prerequisites (install once):
#   choco install python aria2 7zip -y
#   pip install -r requirements.txt
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File build_windows.ps1

$ErrorActionPreference = "Stop"
$AppName = "hydownload managers"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  HyDownload Manager — Windows Build Script" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Locate aria2c.exe ─────────────────────────────────────────────────
$Aria2Candidates = @(
    "C:\ProgramData\chocolatey\bin\aria2c.exe",
    "C:\Program Files\aria2\aria2c.exe",
    "C:\Program Files (x86)\aria2\aria2c.exe",
    "$env:LOCALAPPDATA\aria2\aria2c.exe"
)
$Aria2Path = $null
foreach ($p in $Aria2Candidates) {
    if (Test-Path $p) { $Aria2Path = $p; break }
}
if (-not $Aria2Path) {
    $found = Get-Command "aria2c.exe" -ErrorAction SilentlyContinue
    if ($found) { $Aria2Path = $found.Source }
}
if (-not $Aria2Path) {
    Write-Host "ERROR: aria2c.exe not found. Run: choco install aria2" -ForegroundColor Red
    exit 1
}
Write-Host "✓ aria2c found: $Aria2Path" -ForegroundColor Green

# ── 2. Locate 7z.exe ────────────────────────────────────────────────────
$SevenZipCandidates = @(
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files (x86)\7-Zip\7z.exe",
    "$env:LOCALAPPDATA\7-Zip\7z.exe",
    "$env:PROGRAMFILES\7-Zip\7z.exe"
)
$SevenZipPath = $null
foreach ($p in $SevenZipCandidates) {
    if (Test-Path $p) { $SevenZipPath = $p; break }
}
if (-not $SevenZipPath) {
    $found = Get-Command "7z.exe" -ErrorAction SilentlyContinue
    if ($found) { $SevenZipPath = $found.Source }
}
if (-not $SevenZipPath) {
    Write-Host "ERROR: 7z.exe not found. Run: choco install 7zip" -ForegroundColor Red
    exit 1
}
Write-Host "✓ 7z found: $SevenZipPath" -ForegroundColor Green

# ── 3. Stage binaries in build_bin/ ─────────────────────────────────────
$BuildBin = "build_bin"
if (Test-Path $BuildBin) { Remove-Item -Recurse -Force $BuildBin }
New-Item -ItemType Directory -Path $BuildBin | Out-Null

Copy-Item $Aria2Path  "$BuildBin\aria2c.exe"
Copy-Item $SevenZipPath "$BuildBin\7z.exe"
Write-Host "✓ Binaries staged in $BuildBin\" -ForegroundColor Green

# ── 4. Clean previous build artefacts ───────────────────────────────────
foreach ($d in @("dist", "build")) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
if (Test-Path "$AppName.spec") { Remove-Item -Force "$AppName.spec" }

# ── 5. Run PyInstaller ───────────────────────────────────────────────────
Write-Host ""
Write-Host "Running PyInstaller..." -ForegroundColor Cyan

$PyInstallerArgs = @(
    "--name=$AppName",
    "--windowed",           # No console window
    "--onedir",             # Folder output (faster startup than --onefile)
    "--noconfirm",
    # Bundle aria2c and 7z next to the exe under bin/
    "--add-data=build_bin\aria2c.exe;bin",
    "--add-data=build_bin\7z.exe;bin",
    # Bundle the browser extension folder
    "--add-data=extension;extension",
    # Exclude heavy QtQuick modules we don't use
    "--exclude-module=PyQt6.QtQuick",
    "--exclude-module=PyQt6.QtQuickWidgets",
    "--exclude-module=PyQt6.QtQml",
    "--exclude-module=PyQt6.QtQmlModels",
    "main_ui.py"
)

python -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller FAILED (exit code $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

# ── 6. Verify output ────────────────────────────────────────────────────
$DistPath = "dist\$AppName"
if (-not (Test-Path $DistPath)) {
    Write-Host "ERROR: Expected dist folder not found: $DistPath" -ForegroundColor Red
    exit 1
}

$ExePath = "$DistPath\$AppName.exe"
if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: Executable not found: $ExePath" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  Build successful!" -ForegroundColor Green
Write-Host "  Output: $DistPath\" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "To create a release ZIP, run:" -ForegroundColor Yellow
Write-Host "  Compress-Archive -Path `"dist\$AppName\*`" -DestinationPath `"dist\HyDownload-Manager-Windows.zip`"" -ForegroundColor Yellow
