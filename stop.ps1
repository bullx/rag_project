# Stop RAG backend + frontend processes (Windows)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

function Import-DotEnv {
    param([string]$Path)
    Get-Content -Path $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        Set-Item -Path "Env:$key" -Value $val
    }
}

function Get-EnvPort {
    param([string]$Name, [int]$Default)
    $parsed = 0
    $raw = [Environment]::GetEnvironmentVariable($Name)
    if ([int]::TryParse($raw, [ref]$parsed) -and $parsed -gt 0 -and $parsed -lt 65536) {
        return $parsed
    }
    return $Default
}

function Get-ListeningPids {
    param([int]$Port)
    $ids = New-Object System.Collections.Generic.List[int]
    $escaped = [regex]::Escape("$Port")
    $pattern = "^\s*TCP\s+\S+:$escaped\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in @(netstat -ano -p TCP)) {
        if ($line -match $pattern) {
            $procId = [int]$Matches[1]
            if ($procId -gt 4 -and -not $ids.Contains($procId)) {
                [void]$ids.Add($procId)
            }
        }
    }
    return [int[]]$ids.ToArray()
}

function Test-ServerCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    if ($CommandLine -match 'uvicorn backend\.api:app') { return $true }
    if ($CommandLine -match 'streamlit run frontend[\\/]app\.py') { return $true }
    $rootPattern = [regex]::Escape($Root)
    if ($CommandLine -match $rootPattern -and $CommandLine -match 'vite\.js|npm-cli\.js|npm(\.cmd)? run dev') {
        return $true
    }
    return $false
}

function Stop-PidTree {
    param([int]$ProcId)
    if ($ProcId -le 4 -or $ProcId -eq $PID) { return }
    & taskkill.exe /PID $ProcId /T /F 2>&1 | Out-Null
}

function Stop-ByCommandLine {
    Get-CimInstance Win32_Process | ForEach-Object {
        if (Test-ServerCommandLine $_.CommandLine) {
            Stop-PidTree $_.ProcessId
        }
    }
}

function Stop-ListenerTree {
    param([int]$Port)
    $pids = @(Get-ListeningPids $Port)
    foreach ($procId in $pids) {
        $killId = [int]$procId
        $current = $procId
        for ($i = 0; $i -lt 8; $i++) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
            if (-not $proc) { break }
            if (Test-ServerCommandLine $proc.CommandLine) { $killId = [int]$proc.ProcessId }
            $parentId = [int]$proc.ParentProcessId
            if ($parentId -le 4 -or $parentId -eq $current) { break }
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$parentId" -ErrorAction SilentlyContinue
            if (-not $parent -or -not (Test-ServerCommandLine $parent.CommandLine)) { break }
            $current = $parentId
        }
        Stop-PidTree $killId
    }
}

if (Test-Path (Join-Path $Root ".env")) {
    Import-DotEnv (Join-Path $Root ".env")
}

$ApiPort = Get-EnvPort "API_PORT" 8000
$FrontendPort = Get-EnvPort "FRONTEND_PORT" 5173

Write-Host "Stopping RAG services..."

Stop-ByCommandLine
foreach ($port in @($ApiPort, $FrontendPort, 8501)) {
    Stop-ListenerTree $port
}

$busy = @()
foreach ($attempt in 1..10) {
    Start-Sleep -Milliseconds 500
    $busy = @()
    foreach ($port in @($ApiPort, $FrontendPort)) {
        if (@(Get-ListeningPids $port).Count -gt 0) {
            $busy += $port
        }
    }
    if ($busy.Count -eq 0) { break }
}

if ($busy.Count -gt 0) {
    Write-Host "Port(s) still in use: $($busy -join ', ')"
    Write-Host "Run: netstat -ano | findstr :<port>"
    exit 1
}

Write-Host "Stopped. Ports $ApiPort and $FrontendPort are free."
exit 0
