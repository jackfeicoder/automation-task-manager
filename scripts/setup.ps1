param([string]$Python = 'python', [switch]$Browser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& ./.venv/Scripts/python.exe -m pip --isolated install --index-url https://pypi.org/simple -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if ($Browser) {
    & ./.venv/Scripts/python.exe -m pip --isolated install --index-url https://pypi.org/simple -r requirements-browser.txt
    if ($LASTEXITCODE -ne 0) { throw 'Browser dependency installation failed' }
}
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
Write-Host 'Ready. Start with: ./scripts/start.ps1'
