$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& ./.venv/Scripts/python.exe -m pytest backend/tests -q
exit $LASTEXITCODE
