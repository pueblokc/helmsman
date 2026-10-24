# Build a one-file Helmsman.exe for Windows.
# Requires:
#   pip install pyinstaller pillow requests
#
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File build/build.ps1
#
# Output:
#   dist/Helmsman.exe   (double-click to run)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path 'build/helmsman.ico')) {
    Write-Host "[1/3] Generating icon"
    python build/make_icon.py
} else {
    Write-Host "[1/3] Icon already present"
}

Write-Host "[2/3] Cleaning previous build"
Remove-Item -Recurse -Force build/_pyi-work -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue

Write-Host "[3/3] Bundling exe"
$icon = (Resolve-Path 'build/helmsman.ico').Path
$src  = (Resolve-Path 'helmsman.py').Path
python -m PyInstaller `
    --onefile `
    --name Helmsman `
    --icon "$icon" `
    --workpath build/_pyi-work `
    --specpath build `
    --clean `
    "$src"

if (Test-Path 'dist/Helmsman.exe') {
    $size = (Get-Item 'dist/Helmsman.exe').Length / 1MB
    Write-Host ("[OK] dist/Helmsman.exe built ({0:N1} MB)" -f $size) -ForegroundColor Green
    Write-Host "  Double-click to launch - opens the joystick UI in your browser."
} else {
    Write-Host "[FAIL] Build did not produce dist/Helmsman.exe" -ForegroundColor Red
    exit 1
}
