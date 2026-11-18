@echo off
echo Stopping and removing the FlowPilot start-at-sign-in task...
schtasks /End /TN "FlowPilot" >nul 2>nul
schtasks /Delete /TN "FlowPilot" /F
echo If FlowPilot is still running, close its window or sign out.
pause
