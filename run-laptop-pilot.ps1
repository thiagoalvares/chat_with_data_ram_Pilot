<#
  Laptop pilot launcher (Windows) — serve the app to colleagues from THIS laptop.

  Starts the Python service (localhost only) and the .NET app with the
  LaptopPilot profile (listens on the network, Windows sign-in ON).
  Colleagues browse to:  http://<this-laptop-name>:5080

  One-time setup first — see LAPTOP_PILOT.md (firewall rule + .env values).
  Run from PowerShell:   .\run-laptop-pilot.ps1
  Stop with Ctrl-C (then close the Python window if it remains).
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$PyDir  = Join-Path $PSScriptRoot "python-service"
$NetDir = Join-Path $PSScriptRoot "dotnet-app"

Write-Host "* Setting up the Python service..." -ForegroundColor Cyan
$venv = Join-Path $PyDir ".venv"
if (-not (Test-Path $venv)) {
    python -m venv $venv
    & (Join-Path $venv "Scripts\pip.exe") install -q --upgrade pip
    & (Join-Path $venv "Scripts\pip.exe") install -q -r (Join-Path $PyDir "requirements.txt")
}

$envFile = Join-Path $PyDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "  ! python-service\.env is missing. Copy .env.example to .env and fill in:" -ForegroundColor Yellow
    Write-Host "    LITELLM_API_BASE, ENCRYPTION_KEY, ADMIN_USERS   (see LAPTOP_PILOT.md)" -ForegroundColor Yellow
    exit 1
}

# Pilot posture: .NET is on this same laptop, so Python listens to localhost
# ONLY — port 8000 is not reachable from the network. (Overrides any
# SERVICE_HOST value in .env for this run.)
$env:SERVICE_HOST = "127.0.0.1"
$env:SERVICE_PORT = "8000"

Write-Host "* Starting Python (localhost:8000, this machine only)..." -ForegroundColor Cyan
$pythonExe = Join-Path $venv "Scripts\python.exe"
$pyProc = Start-Process -FilePath $pythonExe -ArgumentList "serve.py" -WorkingDirectory $PyDir -PassThru

for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-WebRequest http://localhost:8000/health -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Milliseconds 500 }
}

$env:ASPNETCORE_ENVIRONMENT = "LaptopPilot"

Write-Host ""
Write-Host "* Starting the app for the network (LaptopPilot profile)..." -ForegroundColor Cyan
Write-Host "  -> Colleagues open:  http://$env:COMPUTERNAME`:5080" -ForegroundColor Green
Write-Host "  -> Windows sign-in is ON (see LAPTOP_PILOT.md if colleagues can't get in)" -ForegroundColor Green
Write-Host ""

try {
    Push-Location $NetDir
    dotnet run -c Release
}
finally {
    Pop-Location
    if ($pyProc -and -not $pyProc.HasExited) {
        Write-Host "Stopping the Python service..." -ForegroundColor Cyan
        Stop-Process -Id $pyProc.Id -Force -ErrorAction SilentlyContinue
    }
}
