$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'Run ./scripts/setup.ps1 first.' }
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Edge Rewards.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'launch_edge.py') + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Open desktop Edge with its original profile for Rewards tasks.'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32/shell32.dll') + ',20'
$shortcut.Save()
Write-Host "Edge Rewards desktop shortcut created: $shortcutPath"
