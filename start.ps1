# RAG Project Startup Script (Windows)
# Launches FastAPI backend + React (Vite) frontend

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}
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

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return [System.IO.Path]::GetFullPath((Join-Path $Root $Path))
}

function Test-HttpOk {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

Write-Host "Starting RAG - Retrieval-Augmented Generation System"
Write-Host "======================================================="

$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "No .env found - copy .env.example to .env"
    exit 1
}
Import-DotEnv $envFile
Write-Host "Loaded .env"

$DataDir = if ($env:DATA_DIR) { $env:DATA_DIR } else { ".\local_data" }
$UploadDir = Resolve-ProjectPath $(if ($env:UPLOAD_DIR) { $env:UPLOAD_DIR } else { Join-Path $DataDir "uploads" })
$ChromaDir = Resolve-ProjectPath $(if ($env:CHROMA_DB_PATH) { $env:CHROMA_DB_PATH } else { Join-Path $DataDir "chroma_db" })
$LogDir = Resolve-ProjectPath $(if ($env:LOG_DIR) { $env:LOG_DIR } else { Join-Path $DataDir "logs" })

foreach ($dir in @($UploadDir, $ChromaDir, $LogDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$probe = Join-Path $ChromaDir ".rag_write_probe"
try {
    [System.IO.File]::WriteAllText($probe, "ok")
    Remove-Item -Path $probe -Force
} catch {
    Write-Host "Cannot write to $ChromaDir - fix permissions or set CHROMA_DB_PATH in .env"
    exit 1
}

$ApiPort = Get-EnvPort "API_PORT" 8000
$FrontendPort = Get-EnvPort "FRONTEND_PORT" 5173
$ApiHost = if ($env:API_HOST) { $env:API_HOST.Trim() } else { "127.0.0.1" }
if ($ApiHost -notmatch '^[A-Za-z0-9\.\-:]+$') {
    Write-Host "Invalid API_HOST: $ApiHost"
    exit 1
}

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Host "Python not found. Create .venv and run: pip install -r requirements.txt"
        exit 1
    }
    $python = $found.Source
}

$pref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -c "import uvicorn" 2>&1 | Out-Null
$uvicornOk = $LASTEXITCODE
$ErrorActionPreference = $pref
if ($uvicornOk -ne 0) {
    Write-Host "uvicorn is not installed for $python"
    Write-Host "Run: pip install -r requirements.txt"
    exit 1
}

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npm) {
    Write-Host "npm not found. Install Node.js 18+."
    exit 1
}

$frontendDir = Join-Path $Root "frontend"
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "frontend/node_modules is missing. Run: cd frontend; npm install"
    exit 1
}

& (Join-Path $Root "stop.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:PYTHONUNBUFFERED = "1"
$script:ApiProc = $null
$script:UiProc = $null

function Stop-Started {
    Write-Host ""
    Write-Host "Stopping..."
    foreach ($proc in @($script:UiProc, $script:ApiProc)) {
        if ($null -ne $proc -and -not $proc.HasExited) {
            & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null
        }
    }
    & (Join-Path $Root "stop.ps1") | Out-Null
}

try {
    Write-Host ""
    Write-Host "Starting FastAPI backend on http://localhost:$ApiPort"
    Write-Host "   API Docs: http://localhost:$ApiPort/docs"
    $script:ApiProc = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "backend.api:app", "--host", $ApiHost, "--port", "$ApiPort", "--reload") `
        -WorkingDirectory $Root `
        -PassThru -NoNewWindow

    Write-Host "Waiting for backend..."
    $backendReady = $false
    foreach ($i in 1..20) {
        if (Test-HttpOk "http://127.0.0.1:$ApiPort/health") {
            Write-Host "Backend is ready!"
            $backendReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $backendReady) {
        Write-Host "Backend did not respond on /health yet. Check the log above."
    }

    Write-Host ""
    Write-Host "Starting React frontend on http://localhost:$FrontendPort"
    $script:UiProc = Start-Process -FilePath $env:ComSpec `
        -ArgumentList @("/d", "/c", "npm run dev -- --host 127.0.0.1 --port $FrontendPort") `
        -WorkingDirectory $frontendDir `
        -PassThru -NoNewWindow

    Write-Host "Waiting for frontend..."
    $frontendReady = $false
    foreach ($i in 1..30) {
        if (Test-HttpOk "http://127.0.0.1:$FrontendPort") {
            Write-Host "Frontend is ready!"
            $frontendReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $frontendReady) {
        Write-Host "Frontend did not respond yet. Check the log above."
    }

    Write-Host ""
    Write-Host "======================================================="
    Write-Host "RAG System is running!"
    Write-Host "   Frontend:  http://localhost:$FrontendPort"
    Write-Host "   API:       http://localhost:$ApiPort"
    Write-Host "   API Docs:  http://localhost:$ApiPort/docs"
    Write-Host ""
    Write-Host "   Use http:// (not https://) when opening in your browser."
    Write-Host "   Logs:      $LogDir\rag.log"
    Write-Host ""
    Write-Host "Press Ctrl+C to stop all services."

    while ($true) {
        $apiGone = ($null -eq $script:ApiProc) -or $script:ApiProc.HasExited
        $uiGone = ($null -eq $script:UiProc) -or $script:UiProc.HasExited
        if ($apiGone -and $uiGone) {
            Write-Host "Both services exited."
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    if ($null -ne $script:ApiProc -or $null -ne $script:UiProc) {
        Stop-Started
    }
}
