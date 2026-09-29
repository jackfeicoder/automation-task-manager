$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run ./scripts/setup.ps1 first.' }
& $python (Join-Path $PSScriptRoot 'prepare_agentrouter.py')
if ($LASTEXITCODE -ne 0) { throw 'Agent Router extension preparation failed.' }
