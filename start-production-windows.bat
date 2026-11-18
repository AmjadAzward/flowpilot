@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo  FlowPilot - production mode (one window, one port)
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (set "PYTHON=py -3") else (set "PYTHON=python")
where node >nul 2>nul
if errorlevel 1 (echo Node.js is missing. Install Node.js LTS from https://nodejs.org & pause & exit /b 1)
%PYTHON% --version >nul 2>nul
if errorlevel 1 (echo Python is missing. Install Python 3.11+ from https://python.org & pause & exit /b 1)
if not exist "backend\.venv\Scripts\python.exe" (
  echo Creating Python environment...
  %PYTHON% -m venv backend\.venv
)
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt -q
if errorlevel 1 (echo Python dependency install failed & pause & exit /b 1)
if not exist "frontend\node_modules" (
  pushd frontend
  call npm install
  popd
  if errorlevel 1 (echo Frontend install failed & pause & exit /b 1)
)
if /I "%~1"=="--rebuild" if exist "frontend\dist" rmdir /s /q "frontend\dist"
if not exist "frontend\dist\index.html" (
  echo Building the interface...
  pushd frontend
  call npm run build
  popd
  if errorlevel 1 (echo Interface build failed & pause & exit /b 1)
)
echo.
echo FlowPilot is starting at http://127.0.0.1:8000
echo Keep this window open. After updating the code, run this file with --rebuild.
start "" "http://127.0.0.1:8000"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-flowpilot.ps1"
pause
