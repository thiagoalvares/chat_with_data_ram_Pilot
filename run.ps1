<#
  Chat with Data (.NET + Python) — local launcher for Windows (PowerShell).
  Starts the Python micro-service (port 8000) and the .NET front end (port 5080).
  Run from a PowerShell prompt:   .\run.ps1
  Press Ctrl-C to stop, then close the Python window if it remains.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$PyDir  = Join-Path $PSScriptRoot "python-service"
$NetDir = Join-Path $PSScriptRoot "dotnet-app"

Write-Host "* Setting up Python micro-service..." -ForegroundColor Cyan
$venv = Join-Path $PyDir ".venv"
if (-not (Test-Path $venv)) {
    python -m venv $venv
    & (Join-Path $venv "Scripts\pip.exe") install -q --upgrade pip
    & (Join-Path $venv "Scripts\pip.exe") install -q -r (Join-Path $PyDir "requirements.txt")
}

$envFile = Join-Path $PyDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "  ! python-service\.env not found — copying from .env.example." -ForegroundColor Yellow
    Write-Host "    Edit it with your LITELLM_API_KEY / LITELLM_API_BASE for live answers."
    Copy-Item (Join-Path $PyDir ".env.example") $envFile
}

Write-Host "* Starting Python micro-service on http://localhost:8000 ..." -ForegroundColor Cyan
$env:SERVICE_PORT = "8000"   # app.py also defaults to 8000
$pythonExe = Join-Path $venv "Scripts\python.exe"
$pyProc = Start-Process -FilePath $pythonExe -ArgumentList "app.py" `
    -WorkingDirectory $PyDir -PassThru

# Wait for Python to be ready
for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-WebRequest http://localhost:8000/health -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Milliseconds 500 }
}

Write-Host ""
Write-Host "* Starting .NET front end on http://localhost:5080 ..." -ForegroundColor Cyan
Write-Host "  -> Open http://localhost:5080 in your browser." -ForegroundColor Green
Write-Host ""

try {
    Push-Location $NetDir
    dotnet run -c Release
}
finally {
    Pop-Location
    if ($pyProc -and -not $pyProc.HasExited) {
        Write-Host "Stopping Python micro-service..." -ForegroundColor Cyan
        Stop-Process -Id $pyProc.Id -Force -ErrorAction SilentlyContinue
    }
}
