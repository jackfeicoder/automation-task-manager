$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    throw 'Run ./scripts/setup.ps1 first'
}
& ./.venv/Scripts/python.exe -m backend.app
