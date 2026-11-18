@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo  FlowPilot - start automatically when you sign in
echo ================================================
if not exist "frontend\dist\index.html" (
  echo Run start-production-windows.bat once first. It prepares and builds everything.
  pause
  exit /b 1
)
echo This adds a Windows scheduled task named FlowPilot that runs for YOUR account at sign-in,
echo hidden, with no administrator rights. Remove it any time with uninstall-startup-windows.bat.
echo Schedules, webhooks and email checks only run while FlowPilot is running, so this keeps them alive.
echo.
set /p OK=Type yes to continue:
if /I not "%OK%"=="yes" (echo Cancelled. & exit /b 0)
schtasks /Create /TN "FlowPilot" /SC ONLOGON /RL LIMITED /F /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%~dp0run-flowpilot.ps1\""
if errorlevel 1 (echo Could not create the task. & pause & exit /b 1)
echo.
echo Done. FlowPilot will start the next time you sign in. To start it now without signing out:
echo   schtasks /Run /TN FlowPilot
echo Then open http://127.0.0.1:8000
pause
