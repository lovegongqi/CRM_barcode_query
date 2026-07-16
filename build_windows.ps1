$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = "python"
try {
    $PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
} catch {
    $PythonVersion = ""
}
if ($PythonVersion -ne "3.11") {
    $Python = "py"
    $PythonArgs = @("-3.11")
} else {
    $PythonArgs = @()
}

Write-Host "==> Creating virtual environment"
if (!(Test-Path ".venv-win")) {
    & $Python @PythonArgs -m venv .venv-win
}

Write-Host "==> Installing Python dependencies"
& .\.venv-win\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-win\Scripts\pip.exe install -r requirements.txt -r requirements-desktop.txt pyinstaller

Write-Host "==> Cleaning previous build"
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\CRMBarcodeQuery") { Remove-Item -Recurse -Force "dist\CRMBarcodeQuery" }

Write-Host "==> Generating app icon"
& .\.venv-win\Scripts\python.exe scripts\generate_app_icon.py

Write-Host "==> Building exe"
& .\.venv-win\Scripts\pyinstaller.exe `
    --noconfirm `
    --onedir `
    --windowed `
    --name "CRMBarcodeQuery" `
    --icon "build\app_icon.ico" `
    --add-data "templates;templates" `
    --add-data "static;static" `
    --add-data "build\app_icon.png;." `
    --add-data "config.example.json;." `
    --add-data "config.docker.example.json;." `
    --add-data "crm_storage/migrations;crm_storage/migrations" `
    --collect-all playwright `
    --collect-all webview `
    --collect-all pystray `
    --hidden-import crm_storage.postgres_store `
    --hidden-import openpyxl.cell._writer `
    app_launcher.py

Write-Host "==> Installing Chromium into the exe folder"
$env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT = "120000"
& .\.venv-win\Scripts\python.exe scripts\install_packaged_chromium.py `
    --target (Join-Path $ProjectRoot "dist\CRMBarcodeQuery\ms-playwright")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to package Playwright Chromium"
}

Write-Host "==> Creating writable data folders"
New-Item -ItemType Directory -Force "dist\CRMBarcodeQuery\barcode" | Out-Null
New-Item -ItemType Directory -Force "dist\CRMBarcodeQuery\results" | Out-Null
New-Item -ItemType Directory -Force "dist\CRMBarcodeQuery\session" | Out-Null

Write-Host ""
Write-Host "Build complete:"
Write-Host "  dist\CRMBarcodeQuery\CRMBarcodeQuery.exe"
Write-Host ""
Write-Host "Copy the whole dist\CRMBarcodeQuery folder to the Windows computer, then double-click CRMBarcodeQuery.exe."
