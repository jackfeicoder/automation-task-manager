param(
    [ValidateRange(1024, 65535)][int]$Port = 9222,
    [string]$ProfileDirectory = '',
    [switch]$Reuse
)
$ErrorActionPreference = 'Stop'
$edgeRegistered = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe') | ForEach-Object {
    if (Test-Path -LiteralPath $_) { (Get-Item -LiteralPath $_).GetValue('') }
}
$edgeCandidates = @($edgeRegistered) + @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft/Edge/Application/msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft/Edge/Application/msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft/Edge/Application/msedge.exe')
)
$edgePath = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $edgePath) { throw 'Microsoft Edge is not installed.' }
if ($Reuse) {
    try {
        $existingProbe = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/json/version")
        $existingProbe.Proxy = $null
        $existingProbe.Timeout = 1000
        $existingResponse = $existingProbe.GetResponse()
        try {
            $existingReader = New-Object System.IO.StreamReader($existingResponse.GetResponseStream())
            try { $existingVersion = $existingReader.ReadToEnd() | ConvertFrom-Json }
            finally { $existingReader.Dispose() }
            if ($existingVersion.'User-Agent' -match 'Edg/' -and $existingVersion.webSocketDebuggerUrl -like "ws://127.0.0.1:$Port/devtools/browser/*") {
                Write-Host "Desktop Edge connection is already ready at port $Port."
                return
            }
        } finally { $existingResponse.Dispose() }
    } catch { }
}
$edgeData = Join-Path $env:LOCALAPPDATA 'Microsoft/Edge/User Data'
if (-not (Test-Path -LiteralPath $edgeData -PathType Container)) {
    throw 'The desktop Edge profile was not found. Open Edge normally and sign in first.'
}
if (-not $ProfileDirectory) {
    $ProfileDirectory = 'Default'
    $localState = Join-Path $edgeData 'Local State'
    if (Test-Path -LiteralPath $localState -PathType Leaf) {
        try {
            $lastProfile = (Get-Content -LiteralPath $localState -Raw | ConvertFrom-Json).profile.last_used
            if ($lastProfile) { $ProfileDirectory = [string]$lastProfile }
        } catch { Write-Warning 'Profile selection metadata was unreadable; using Default.' }
    }
}
if ($ProfileDirectory -notmatch '^(Default|Profile [0-9]+)$') {
    throw 'Specify -ProfileDirectory Default or a directory such as "Profile 1".'
}
$selectedProfile = Join-Path $edgeData $ProfileDirectory
if (-not (Test-Path -LiteralPath $selectedProfile -PathType Container)) {
    throw 'The selected desktop Edge profile does not exist.'
}
$edgeProcesses = @(Get-Process -Name msedge -ErrorAction SilentlyContinue)
if ($edgeProcesses.Count) {
    throw 'Close all Edge windows and background Edge processes first, then run this script again. Existing Edge sessions are left untouched.'
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; select a different -Port and set REWARDS_EDGE_ENDPOINT accordingly."
}
$edgeArguments = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--user-data-dir=`"$edgeData`"",
    "--profile-directory=`"$ProfileDirectory`"",
    'https://rewards.bing.com/earn'
)
# Run this launcher outside task workers. Edge belongs to the desktop session,
# so stopping a scheduled task will not terminate the user's browser.
Start-Process -FilePath $edgePath -ArgumentList $edgeArguments -WindowStyle Normal
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    # A listening socket alone can appear before Edge's automation API is ready.
    try {
        $probe = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/json/version")
        $probe.Proxy = $null
        $probe.Timeout = 1000
        $response = $probe.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try { $version = $reader.ReadToEnd() | ConvertFrom-Json }
            finally { $reader.Dispose() }
            $ready = $version.'User-Agent' -match 'Edg/' -and $version.webSocketDebuggerUrl -like "ws://127.0.0.1:$Port/devtools/browser/*"
        } finally { $response.Dispose() }
        if ($ready) { break }
    } catch { $ready = $false }
}
if (-not $ready) {
    throw 'Edge opened but did not expose the local automation port. Check edge://policy for RemoteDebuggingAllowed, then retry. No replacement profile was created.'
}
Write-Host "Desktop Edge is ready at http://127.0.0.1:$Port. Existing login information is reused."
