param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvDir = Join-Path $env:TEMP "hil_platform_build_venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    python -m venv $VenvDir
}

if (-not $SkipInstall) {
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r requirements.txt
    & $VenvPython -m pip install "setuptools<82" pyinstaller
}

& $VenvPython -m PyInstaller --noconfirm --clean hil_platform.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$DistDir = Join-Path $ProjectRoot "dist\HILInterfacePlatform"
Copy-Item -LiteralPath (Join-Path $ProjectRoot "config.example.yaml") -Destination (Join-Path $DistDir "config.yaml") -Force

Write-Host ""
Write-Host "Build complete:"
Write-Host "  $DistDir\HILInterfacePlatform.exe"
Write-Host "  $DistDir\config.yaml"
Write-Host ""
Write-Host "Run:"
Write-Host "  cd $DistDir"
Write-Host "  .\HILInterfacePlatform.exe"
