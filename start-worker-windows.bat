@echo off
setlocal
cd /d "%~dp0backend"
echo ================================================
echo  FlowPilot - queue worker
echo ================================================
echo Start the API with FLOWPILOT_QUEUE=1 as well, otherwise nothing is queued.
echo   (in the API window:  set FLOWPILOT_QUEUE=1  then start the API)
echo Use the same FLOWPILOT_DB and FLOWPILOT_KEY_FILE settings as the API, if you set any.
echo Press Ctrl+C to stop this worker.
echo.
set FLOWPILOT_QUEUE=1
".venv\Scripts\python.exe" -m app.worker --concurrency 4
pause
