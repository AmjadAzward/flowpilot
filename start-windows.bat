@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo  FlowPilot - Local Automation Studio
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
echo Installing Python dependencies...
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt -q
if errorlevel 1 (echo Python dependency install failed & pause & exit /b 1)
if not exist "frontend\node_modules" (
  echo Installing frontend packages...
  pushd frontend
  call npm install
  popd
  if errorlevel 1 (echo Frontend install failed & pause & exit /b 1)
)
echo Starting backend at http://127.0.0.1:8000...
start "FlowPilot API" /D "%~dp0backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo Starting frontend at http://127.0.0.1:5173...
start "FlowPilot Website" /D "%~dp0frontend" cmd /k "npm run dev"
echo.
echo Open http://127.0.0.1:5173 in your browser.
echo Keep both terminal windows open while using FlowPilot.
pause
