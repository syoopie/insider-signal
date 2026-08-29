# Starts the Insider Signal web dashboard in development.
#
# The Python pipeline runs on GitHub Actions, not locally. The only thing to run
# on your machine is the Next.js app in web/, which reads the same Neon database.
#
# Run with:  powershell -ExecutionPolicy Bypass -File scripts/dev/start.ps1
# (or just double-click scripts/dev/start.bat)

$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$webDir = Join-Path $root "web"
$port = 3000

function Test-Port($p) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$p" -UseBasicParsing -TimeoutSec 1
        return $r.StatusCode -eq 200
    } catch { return $false }
}

Write-Host "Insider Signal - web dashboard" -ForegroundColor Cyan
Write-Host "------------------------------"

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: 'pnpm' is not installed or not on PATH. Run 'corepack enable' or install from https://pnpm.io/" -ForegroundColor Red
    exit 1
}

if (Test-Port $port) {
    Write-Host "Something is already serving http://localhost:$port - opening it and leaving it alone." -ForegroundColor Yellow
    Start-Process "http://localhost:$port"
    exit 0
}

# --- .env.local -------------------------------------------------------------
$envLocal = Join-Path $webDir ".env.local"
$rootEnv = Join-Path $root ".env"
if (-not (Test-Path $envLocal)) {
    if (Test-Path $rootEnv) {
        Write-Host "Creating web/.env.local with DATABASE_URL from the repo-root .env"
        (Select-String -Path $rootEnv -Pattern '^DATABASE_URL=').Line | Set-Content -Encoding utf8 $envLocal
    } else {
        Write-Host "ERROR: web/.env.local is missing and there is no repo-root .env to copy DATABASE_URL from." -ForegroundColor Red
        Write-Host "Create web/.env.local with a single line:  DATABASE_URL=<your Neon connection string>" -ForegroundColor Red
        exit 1
    }
}

# --- dependencies ----------------------------------------------------------
if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
    Write-Host "Installing dependencies (first run only)..."
    Push-Location $webDir
    pnpm install
    Pop-Location
}

# --- dev server ----------------------------------------------------------
Write-Host "Starting Next.js dev server (http://localhost:$port)..."
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command', "cd '$webDir'; pnpm dev"
)

Write-Host "Waiting for the server to come up..."
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Port $port) { $ready = $true; break }
    Start-Sleep -Seconds 1
}

if ($ready) {
    Start-Process "http://localhost:$port"
    Write-Host ""
    Write-Host "Dashboard is up. Close the new window (or Ctrl+C inside it) to stop the server." -ForegroundColor Cyan
} else {
    Write-Host "Server didn't respond within 60s - check the new window for errors." -ForegroundColor Yellow
}
