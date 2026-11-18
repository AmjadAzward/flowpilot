# Starts FlowPilot in production style: ONE process, the API serves the built interface.
# Used by start-production-windows.bat and by the "start at sign-in" task. Settings can be overridden with
# environment variables first: FLOWPILOT_PORT (default 8000), FLOWPILOT_DB, FLOWPILOT_KEY_FILE.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Error 'Run start-production-windows.bat once first, it prepares the Python environment.'; exit 1 }
if (-not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) { Write-Error 'The interface is not built yet. Run start-production-windows.bat once first.'; exit 1 }
$port = if ($env:FLOWPILOT_PORT) { $env:FLOWPILOT_PORT } else { '8000' }
$env:FLOWPILOT_SERVE_UI = '1'
Set-Location $backend
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $port
