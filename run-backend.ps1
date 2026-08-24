# DropPilot AI — start the backend locally (native path, no Docker).
#
# Configuration is read from the repository-root .env (including AliExpress
# credentials). From the repository root:
#
#   .\run-backend.ps1
#
$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$BackendRoot = Join-Path $RepoRoot "backend"
$EnvFile = Join-Path $RepoRoot ".env"
$Uvicorn = Join-Path $BackendRoot ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path $EnvFile)) {
    Write-Error @"
.env not found at repository root.

Copy the template and configure it:
  git show HEAD:.env.example | Out-File -Encoding utf8 .env
"@
}

if (-not (Test-Path $Uvicorn)) {
    Write-Error @"
Backend virtual environment not found.

From the repository root:
  cd backend
  python -m venv .venv
  .\.venv\Scripts\pip install -e ".[dev]"
"@
}

Set-Location $BackendRoot
Write-Host "Starting DropPilot backend on http://localhost:8000"
Write-Host "API docs: http://localhost:8000/docs"
# --no-proxy-headers is a security setting, not a preference. Uvicorn's default
# is to parse X-Forwarded-For and overwrite scope["client"] whenever the socket
# peer is 127.0.0.1 — which is exactly what cloudflared is. That would put a
# second, separately-configured proxy-trust decision underneath the application,
# and anything able to connect on loopback could then choose its own client
# address. `app.core.client_ip` is the single authority; see
# docs/PRODUCTION_SECURITY.md section 7.
& $Uvicorn app.main:app --reload --no-proxy-headers --host 0.0.0.0 --port 8000
