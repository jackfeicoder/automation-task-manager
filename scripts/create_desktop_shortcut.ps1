param([string]$Name = 'Task Harbor')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw 'Run ./scripts/setup.ps1 first.'
}
if ([string]::IsNullOrWhiteSpace($Name) -or $Name.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
    throw 'The shortcut name must be a valid file name.'
}
$desktopDirectory = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopDirectory ($Name + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'launch_ui.py') + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Start Task Harbor and open the management dashboard.'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32/shell32.dll') + ',21'
$shortcut.Save()
Write-Host "Desktop shortcut created: $shortcutPath"
